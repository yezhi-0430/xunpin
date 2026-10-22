import threading
import time
from datetime import datetime, timezone, timedelta

from .domain import now_iso
from .scenarios import SCENARIOS, products_for
from .sources import SourceError, fetch_snapshot


class MonitorService:
    def __init__(self, store, config, transport=None):
        self.store, self.config, self.transport = store, config, transport
        self.lock = threading.RLock()

    def source_status(self):
        rows = []
        for source in self.config.sources:
            state = self.store.state("live:" + source.source_id, {})
            expired = self.expired(source)
            status = (
                "disabled"
                if not source.ready()
                else "expired"
                if expired
                else state.get("status", "ready")
            )
            rows.append(
                dict(
                    source_id=source.source_id,
                    kind=source.kind,
                    status=status,
                    configured=source.ready(),
                    permission_ref_configured=bool(source.permission_ref),
                    permission_note="许可依据由配置人提供，系统未独立核验",
                    validation="尚无真实调用记录"
                    if not state.get("last_success")
                    else "已收到真实响应；字段与用途仍以来源许可为准",
                    product_count=len(source.product_ids),
                    interval_seconds=source.interval_seconds,
                    daily_budget=source.daily_budget,
                    requests_today=state.get("used", 0)
                    if state.get("day") == self.day()
                    else 0,
                    next_due=state.get("next_due"),
                    last_success=state.get("last_success"),
                    reason=state.get(
                        "reason",
                        "缺少凭证、商品范围或许可依据"
                        if not source.ready()
                        else "等待首次采集",
                    ),
                    ai_allowed=source.ai_allowed,
                )
            )
        return rows

    @staticmethod
    def day():
        return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")

    @staticmethod
    def expired(source):
        if not source.expires_at:
            return False
        try:
            expiry = datetime.fromisoformat(source.expires_at)
            return expiry.tzinfo is None or expiry <= datetime.now(timezone.utc)
        except ValueError:
            return True

    def collect(self, source_id, scheduled=False):
        if not self.lock.acquire(blocking=False):
            return dict(status="busy", message="已有采集任务在执行，本次合并")
        try:
            source = next(
                (s for s in self.config.sources if s.source_id == source_id), None
            )
            if source is None:
                raise KeyError("来源不存在")
            if not source.ready():
                return dict(
                    status="disabled", message="来源未配置或许可范围未确认，未发起请求"
                )
            if self.expired(source):
                return dict(
                    status="expired", message="来源许可已过期或到期格式无效，未发起请求"
                )
            key, now = "live:" + source_id, time.time()
            state = self.store.state(key, {})
            if state.get("status") == "paused":
                return dict(status="paused", message=state.get("reason", "已暂停"))
            if state.get("day") != self.day():
                state.update(day=self.day(), used=0)
            if state.get("used", 0) >= source.daily_budget:
                return dict(status="budget", message="每日请求预算已用尽")
            if now < state.get("next_allowed", 0):
                return dict(
                    status=state.get("status", "cached"),
                    message="限频或退避窗口内，本次未请求",
                )
            if now < state.get("next_due", 0) and state.get("status") == "ok":
                return dict(status="cached", message="当前快照仍在检查周期内，共享缓存")
            success, events = 0, 0
            # One network request per tick. Round-robin avoids a burst for a large watchlist.
            index = state.get("cursor", 0) % len(source.product_ids)
            pid = source.product_ids[index]
            state.update(
                used=state.get("used", 0) + 1, next_allowed=now + source.min_gap_seconds
            )
            self.store.set_state(key, state)
            try:
                snapshot = fetch_snapshot(source, pid, self.transport)
                age = (
                    datetime.now(timezone.utc)
                    - datetime.fromisoformat(snapshot.observed_at)
                ).total_seconds()
                if age > source.max_age_seconds or age < -300:
                    raise SourceError("schema", "源数据过期或时间超前，未覆盖有效快照")
                result = self.store.ingest(snapshot, "live")
                if result.get("out_of_order"):
                    raise SourceError("schema", "收到乱序观测，未覆盖当前快照")
                events = result["events_created"]
                next_index = (index + 1) % len(source.product_ids)
                state.update(
                    status="ok",
                    failures=0,
                    retries=0,
                    cursor=next_index,
                    next_due=now
                    + (
                        source.interval_seconds
                        if next_index == 0
                        else source.min_gap_seconds
                    ),
                    last_success=now_iso(),
                    reason=f"商品 {pid} 获取成功，新增 {events} 个事件",
                )
                self.store.log("live", source_id, "ok", state["reason"])
                success = 1
            except SourceError as error:
                failures = state.get("failures", 0) + 1
                if error.kind in ["auth", "challenge", "schema"]:
                    state.update(status="paused", reason=str(error), failures=failures)
                elif failures >= 3:
                    state.update(
                        status="paused" if error.kind == "rate" else "cooldown",
                        reason=str(error) + "；连续失败达到阈值",
                        next_allowed=max(state["next_allowed"], now + 900),
                        failures=failures,
                    )
                else:
                    delay = max(error.retry_after, 30 * 2 ** (failures - 1))
                    state.update(
                        status="backoff",
                        reason=str(error),
                        next_allowed=max(state["next_allowed"], now + delay),
                        failures=failures,
                    )
                self.store.log("live", source_id, state["status"], state["reason"])
            self.store.set_state(key, state)
            return dict(
                status=state["status"],
                message=state["reason"],
                collected=success,
                events_created=events,
            )
        finally:
            self.lock.release()

    def pause(self, source_id):
        if not any(s.source_id == source_id for s in self.config.sources):
            raise KeyError("来源不存在")
        with self.lock:
            key = "live:" + source_id
            state = self.store.state(key, {})
            state.update(status="paused", reason="人工暂停，等待核实来源状态")
            self.store.set_state(key, state)
            self.store.log("live", source_id, "paused", state["reason"])

    def resume(self, source_id):
        source = next(
            (s for s in self.config.sources if s.source_id == source_id), None
        )
        if source is None:
            raise KeyError("来源不存在")
        if not source.ready() or self.expired(source):
            return dict(status="disabled", message="请先在本地配置有效凭证与许可范围")
        with self.lock:
            key = "live:" + source_id
            state = self.store.state(key, {})
            state.update(
                status="ready",
                failures=0,
                next_allowed=state.get("next_allowed", 0),
                next_due=0,
                reason="人工恢复，下一轮将重新核验响应",
            )
            self.store.set_state(key, state)
        return dict(status="ready", message="已恢复；请求仍受配额和频率控制")

    def replay(self, scenario_id):
        with self.lock:
            index = next(
                (i for i, s in enumerate(SCENARIOS) if s["id"] == scenario_id), None
            )
            if index is None:
                raise KeyError("场景不存在")
            previous = self.store.state("replay:step", -1)
            if index < previous:
                return dict(
                    status="rejected", message="回放仅向前推进；返回早期场景请重置"
                )
            results = []
            for step in range(max(0, previous), index + 1):
                for product in products_for(step):
                    results.append(self.store.ingest(product, "replay"))
            self.store.set_state("replay:step", index)
            created = sum(r["events_created"] for r in results)
            self.store.log(
                "replay",
                "replay",
                "ok",
                f"构造样例：{SCENARIOS[index]['title']}；新增 {created} 个事件",
            )
            return dict(
                status="ok",
                message=f"{SCENARIOS[index]['title']} · 新增 {created} 个事件",
                events_created=created,
            )

    def inject(self, fault):
        if fault not in ["rate", "auth", "schema", "resume"]:
            raise KeyError("故障类型不存在")
        state = dict(
            status="backoff"
            if fault == "rate"
            else "ready"
            if fault == "resume"
            else "paused",
            reason={
                "rate": "故障注入：429，退避 120 秒；不立即重试",
                "auth": "故障注入：403，需人工恢复授权",
                "schema": "故障注入：HTML / 结构异常，暂停解析",
                "resume": "回放恢复，不触发网络请求",
            }[fault],
            simulated=True,
        )
        self.store.set_state("replay:risk", state)
        self.store.log("replay", "replay-risk", state["status"], state["reason"])
        return state
