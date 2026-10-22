import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .ai import Analyst
from .domain import payable, Snapshot
from .operations import ExecutionRecord
from .history import History
from .simulation import Simulation
from .scenarios import SCENARIOS
from .service import MonitorService
from .settings import Config, ROOT
from .storage import Store

Mode = Literal["live", "replay"]


class ReplayRequest(BaseModel):
    scenario_id: str


class FaultRequest(BaseModel):
    fault: Literal["rate", "auth", "schema", "resume"]


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    mode: Mode
    status: Literal["pending", "accepted", "rejected", "executed", "evaluated"]
    note: str = Field(min_length=1, max_length=1000)
    execution: ExecutionRecord | None = None

    @model_validator(mode="after")
    def evaluated_execution(self):
        if self.status == "evaluated" and self.execution is not None and self.execution.evaluation_status == "pending":
            raise ValueError("已评估任务须记录观察结果，或选择无法评估并说明原因")
        return self


class AnalyzeRequest(BaseModel):
    mode: Mode


class SourceAction(BaseModel):
    mode: Mode


def require_live(body):
    if body.mode != "live":
        raise HTTPException(status_code=409, detail="回放模式不能操作正式来源")


def create_app(config=None, start_scheduler=True, transport=None, example_path=ROOT / "examples" / "demo-data.json"):
    config = config or Config.from_env()
    store = Store(config.database_path)
    service = MonitorService(store, config, transport)
    analyst = Analyst(store, config)
    history = History(store)
    simulation = Simulation(store, bundled_path=example_path)
    stop = threading.Event()

    def loop():
        while not stop.wait(1):
            for source in config.sources:
                try:
                    service.collect(source.source_id, scheduled=True)
                except Exception:
                    # Do not emit request objects or exception strings containing credentials.
                    store.log(
                        "live",
                        source.source_id,
                        "error",
                        "调度器出现内部错误，请检查本地配置和测试结果",
                    )

    @asynccontextmanager
    async def lifespan(app):
        worker = threading.Thread(target=loop, name="monitor-scheduler", daemon=True)
        if start_scheduler:
            worker.start()
        yield
        stop.set()
        if worker.is_alive():
            worker.join(timeout=22)

    app = FastAPI(title="巡品 · 淘宝商品与竞品监控", lifespan=lifespan)
    app.state.service = service
    app.state.analyst = analyst

    @app.middleware("http")
    async def local_mutations(request: Request, call_next):
        if request.method in ["POST", "PUT", "DELETE", "PATCH"]:
            origin = request.headers.get("origin")
            if origin:
                parsed = urlparse(origin)
                if parsed.netloc != request.headers.get(
                    "host"
                ) or parsed.scheme not in ["http", "https"]:
                    return JSONResponse({"detail": "仅允许同源操作"}, status_code=403)
            if request.url.path.startswith(
                "/api/"
            ) and "application/json" not in request.headers.get("content-type", ""):
                return JSONResponse(
                    {"detail": "操作需使用 application/json"}, status_code=415
                )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({"detail": "请求的记录或来源不存在"}, status_code=404)

    @app.get("/api/health")
    def health():
        import hashlib
        instance = hashlib.sha256(str(config.database_path.resolve()).encode()).hexdigest()[:16]
        return dict(status="ok", scheduler=start_scheduler, service="xunpin", instance_id=instance)

    @app.get("/api/simulation")
    def simulated_overview():
        return simulation.overview()

    @app.get("/api/dashboard")
    def dashboard():
        imported = simulation.overview()
        if imported["manifest"]:
            return {**imported, "imported_examples": True}
        return overview("live")

    @app.get("/api/overview")
    def overview(mode: Mode = "live"):
        state = store.overview(mode)
        for product in state["products"]:
            source = next(
                (s for s in config.sources if s.source_id == product["source_id"]), None
            )
            age = (
                datetime.now(timezone.utc)
                - datetime.fromisoformat(product["observed_at"])
            ).total_seconds()
            allowed = (
                source
                and source.ready()
                and not service.expired(source)
                and product["product_id"] in source.product_ids
            )
            product["freshness"] = (
                "sample"
                if mode == "replay"
                else "restricted"
                if not allowed
                else "stale"
                if age > source.max_age_seconds or age < -300
                else "fresh"
            )
            product["age_seconds"] = max(0, int(age))
            value = payable(
                Snapshot(
                    **{k: v for k, v in product.items() if k in Snapshot.model_fields}
                )
            )
            product["historical_payable_cents"] = value
            product["payable_cents"] = (
                value if product["freshness"] in ["sample", "fresh"] else None
            )
        state.update(
            sources=service.source_status(),
            ai=analyst.status(),
            scenarios=SCENARIOS,
            replay_step=store.state("replay:step", -1),
            risk=store.state(
                "replay:risk",
                {"status": "ready", "reason": "故障注入尚未执行", "simulated": True},
            ),
            scheduler=start_scheduler,
        )
        return state

    @app.get("/api/history")
    def historical_samples(offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=50)):
        return history.list_samples(offset, limit)

    @app.get("/api/history/{sample_id}")
    def historical_sample(sample_id: str):
        return history.get(sample_id)

    @app.post("/api/history/{sample_id}/analyze")
    def historical_analysis(sample_id: str):
        result = analyst.analyze_history(history.get(sample_id))
        saved = history.save_analysis(sample_id, result)
        if saved["status"] == "ok" and result["status"] != "ok":
            return {**result, "preserved_previous": True, "saved_analysis": saved}
        return result

    @app.post("/api/replay")
    def replay(body: ReplayRequest):
        return service.replay(body.scenario_id)

    @app.post("/api/replay/reset")
    def reset():
        with service.lock:
            store.reset_replay()
        return dict(status="ok", message="回放数据已重置；真实采集数据保留")

    @app.post("/api/replay/fault")
    def fault(body: FaultRequest):
        return service.inject(body.fault)

    @app.post("/api/sources/{source_id}/collect")
    def collect(source_id: str, body: SourceAction):
        require_live(body)
        return service.collect(source_id)

    @app.post("/api/sources/{source_id}/pause")
    def pause(source_id: str, body: SourceAction):
        require_live(body)
        service.pause(source_id)
        return dict(status="paused", message="来源已暂停")

    @app.post("/api/sources/{source_id}/resume")
    def resume(source_id: str, body: SourceAction):
        require_live(body)
        return service.resume(source_id)

    @app.get("/api/events/{event_id}")
    def evidence(event_id: str, mode: Mode = "live"):
        return store.evidence(event_id, mode)

    @app.post("/api/events/{event_id}/analyze")
    def analyze(event_id: str, body: AnalyzeRequest):
        result = analyst.analyze(event_id, body.mode)
        store.save_analysis("task-" + event_id, body.mode, result)
        return result

    @app.post("/api/tasks/{task_id}/feedback")
    def feedback(task_id: str, body: FeedbackRequest):
        return store.feedback(
            task_id, body.status, body.note, body.mode,
            body.execution.model_dump(mode="json") if body.execution is not None else None,
        )

    @app.get("/")
    def index():
        return FileResponse(ROOT / "frontend" / "index.html")

    if (ROOT / "frontend").exists():
        app.mount("/static", StaticFiles(directory=ROOT / "frontend"), name="static")
    return app
