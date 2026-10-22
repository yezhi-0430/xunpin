import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review_id: str = Field(min_length=1, max_length=120)
    text: str = Field(max_length=2000)
    observed_at: str


class Snapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    title: str = Field(min_length=1, max_length=1000)
    role: Literal["own", "competitor"]
    source_id: str = Field(max_length=80)
    observed_at: str
    description: str | None = Field(default=None, max_length=16000)
    spec: str | None = Field(default=None, max_length=200)
    conditions: str | None = Field(default=None, max_length=500)
    price_cents: int | None = Field(default=None, ge=0, le=100000000)
    coupon_cents: int | None = Field(default=None, ge=0, le=100000000)
    coupon_threshold_cents: int | None = Field(default=None, ge=0)
    price_confirmed: bool | None = None
    promotion_text: str | None = Field(default=None, max_length=2000)
    reviews: list[Review] | None = Field(default=None, max_length=200)

    @field_validator("observed_at")
    @classmethod
    def aware_time(cls, value):
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError("observed_at 必须带时区")
        return parsed.isoformat()

    def content(self):
        data = self.model_dump(exclude={"observed_at"})
        if data["reviews"] is not None:
            data["reviews"] = sorted(
                {r["review_id"]: r for r in data["reviews"]}.values(),
                key=lambda r: r["review_id"],
            )
        return data


def payable(item: Snapshot):
    if (
        not item.price_confirmed
        or item.price_cents is None
        or item.coupon_cents is None
        or not item.conditions
        or (item.coupon_cents > 0 and item.coupon_threshold_cents is None)
    ):
        return None
    coupon = (
        item.coupon_cents
        if item.coupon_cents == 0 or item.price_cents >= item.coupon_threshold_cents
        else 0
    )
    return max(0, item.price_cents - coupon)


def clean(text):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", text or "")).strip()


def changes(before: Snapshot, after: Snapshot):
    events = []

    def add(kind, title, facts, priority="medium"):
        events.append(dict(kind=kind, title=title, facts=facts, priority=priority))

    for field, label in [("title", "标题"), ("description", "描述")]:
        a, b = getattr(before, field), getattr(after, field)
        if a is not None and b is not None and clean(a) != clean(b):
            add(field, f"{label}内容发生变化", {"before": clean(a), "after": clean(b)})
    a, b = payable(before), payable(after)
    comparable = (
        before.spec
        and before.spec == after.spec
        and before.conditions == after.conditions
    )
    if comparable and a is not None and b is not None and a != b:
        add(
            "price",
            "可比到手价下降" if b < a else "可比到手价上涨",
            {
                "before_cents": a,
                "after_cents": b,
                "difference_cents": b - a,
                "spec": after.spec,
                "conditions": after.conditions,
            },
            "high" if a and abs(b - a) / a >= 0.1 else "medium",
        )
    promo_fields = [
        "price_cents",
        "coupon_cents",
        "coupon_threshold_cents",
        "promotion_text",
        "conditions",
        "price_confirmed",
    ]
    # Losing a field is a coverage problem; its first recovery establishes a baseline.
    # Only two known values can prove a promotion or price term changed.
    if any(
        getattr(before, f) is not None
        and getattr(after, f) is not None
        and getattr(before, f) != getattr(after, f)
        for f in promo_fields
    ) and not any(
        e["kind"] == "price" for e in events
    ):
        add(
            "promotion",
            "价格或优惠条件有调整",
            {
                "before": {f: getattr(before, f) for f in promo_fields},
                "after": {f: getattr(after, f) for f in promo_fields},
                "payable_unchanged": bool(comparable and a is not None and a == b),
                "comparable": bool(comparable and a is not None and b is not None),
            },
        )
    if after.reviews is not None and before.reviews is not None:
        seen = {r.review_id for r in before.reviews}
        new = {
            r.review_id: r.model_dump()
            for r in after.reviews
            if r.review_id not in seen
        }
        if new:
            add(
                "reviews",
                "发现新增评价样本",
                {
                    "sample_count": len(new),
                    "samples": list(new.values()),
                    "limitation": "仅代表本来源本次新增样本，不代表全部用户",
                },
            )
    unavailable = [
        f
        for f in [
            "description", "price_cents", "coupon_cents", "reviews",
            "promotion_text", "conditions", "spec", "coupon_threshold_cents", "price_confirmed",
        ]
        if getattr(before, f) is not None and getattr(after, f) is None
    ]
    if unavailable:
        add(
            "availability",
            "部分字段暂不可用",
            {
                "fields": unavailable,
                "limitation": "缺失不等于内容删除、零价格或商品下架",
            },
        )
    return events


def task_draft(event):
    kind = event["kind"]
    texts = {
        "price": (
            "促销测算",
            "核实同规格价差，在取得本店成本与活动预算后测算限时券；毛利约束未确认前不改价。",
            "运营",
            "毛利率、转化率；对照试验后评估",
        ),
        "promotion": (
            "核验优惠机制",
            "核实券门槛、有效期及叠加规则；到手价未变时不发起跟价。",
            "运营",
            "有效告警率、活动实际到手价",
        ),
        "title": (
            "商品信息优化",
            "比较前后标题的卖点、规格和适用场景，核实本店是否存在信息缺口，再起草标题测试。",
            "商品运营",
            "搜索点击率；避免夸大功效",
        ),
        "description": (
            "美工素材需求",
            "根据描述差异核实本店信息缺口，准备对比说明图草稿；需求单需注明人群、文案、画面与品牌规范，不复制竞品素材。",
            "运营 / 美工",
            "详情转化率、相关咨询量",
        ),
        "reviews": (
            "评价主题核查",
            "阅读新增评价证据，归纳具体问题和需求；小样本仅建立核查任务，不推断全体消费者。",
            "运营 / 客服",
            "相关咨询与投诉；持续观察样本",
        ),
        "availability": (
            "核查数据覆盖",
            "检查来源字段及权限，暂停依赖缺失字段的判断，保留历史证据。",
            "数据维护",
            "字段覆盖率、数据时效",
        ),
    }
    title, action, owner, metric = texts[kind]
    facts = event["facts"]
    if kind == "price":
        action = (
            f"观测到同规格到手价 ¥{facts['before_cents'] / 100:.2f} → ¥{facts['after_cents'] / 100:.2f}，价差 ¥{abs(facts['difference_cents']) / 100:.2f}。"
            + action
        )
    elif kind in ["title", "description"]:
        action = (
            f"变化前：{facts['before'][:180]}；变化后：{facts['after'][:180]}。"
            + action
        )
    elif kind == "reviews":
        excerpts = "；".join(r["text"][:120] for r in facts["samples"][:3])
        action = (
            f"本次新增 {facts['sample_count']} 条样本，原文包括“{excerpts}”。" + action
        )
    elif kind == "promotion" and facts.get("payable_unchanged"):
        action = "本次标价与优惠变化相互抵消，可比到手价未变。" + action
    brief = None
    if kind == "description":
        brief = dict(
            objective="根据已核实的本店信息缺口制作说明图",
            reference_change=facts["after"][:500],
            deliverable="详情页说明图；尺寸与版式沿用本店素材规范",
            copy_requirement="只写已核实的本店规格、使用和服务信息，文案由运营确认",
            visual_elements=[
                "本店商品实物与容量标识",
                "与已核实场景对应的操作或收纳说明",
                "本店品牌视觉",
            ],
            acceptance=[
                "规格和适用条件清晰",
                "不复制竞品图片或未经验证的承诺",
                "运营审核后再发布",
            ],
        )
    return dict(
        title=title,
        action=action,
        owner=owner,
        metric=metric,
        status="pending",
        note="",
        evidence_ids=[event["id"]],
        engine="rules",
        limitation="规则草稿，需人工核实；未调用大模型，不提供收益预测",
        brief=brief,
    )
