import json

import httpx
from fastapi.testclient import TestClient

from backend.ai import Analyst
from backend.app import create_app
from backend.service import MonitorService
from backend.settings import Config
from backend.storage import Store


def build(tmp_path, **config):
    store = Store(tmp_path / "api.db")
    svc = MonitorService(store, Config(**config))
    svc.replay("price-drop")
    return svc


def test_model_not_configured_is_explicit_and_does_not_call_network(tmp_path):
    svc = build(tmp_path)
    event = svc.store.overview("replay")["events"][0]
    result = Analyst(svc.store, svc.config).analyze(event["id"], "replay")
    assert result["engine"] == "rules" and result["status"] == "unconfigured"
    assert result["evidence_ids"] == [event["id"]]


def test_real_model_contract_and_bounded_tool_call(tmp_path):
    svc = build(tmp_path, ai_key="test-key", ai_model="test-model")
    event = svc.store.overview("replay")["events"][0]
    requests = []

    def handler(req):
        data = json.loads(req.content)
        requests.append(data)
        if len(requests) == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call1",
                                        "type": "function",
                                        "function": {
                                            "name": "read_event_evidence",
                                            "arguments": "{}",
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
            )
        result = {
            "summary": "竞品可比到手价下降，需要先核实本店毛利。",
            "evidence_ids": [event["id"]],
            "findings": [],
            "hypotheses": [],
            "recommendations": [
                {
                    "action": "创建促销测算任务",
                    "owner": "运营",
                    "metric": "毛利率",
                    "conditions": "确认成本和预算后执行",
                    "evaluation_plan": "先记录毛利基线，再按实际活动窗口复查",
                }
            ],
            "limitations": "单次观测不能预测销量变化",
        }
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": json.dumps(result, ensure_ascii=False)}}
                ]
            },
        )

    result = Analyst(svc.store, svc.config, httpx.MockTransport(handler)).analyze(
        event["id"], "replay"
    )
    assert result["engine"] == "llm" and result["status"] == "ok"
    assert len(requests) == 2
    assert requests[1]["messages"][-1]["role"] == "tool"
    assert result["trace"][-1]["tool"] == "read_event_evidence"


def test_invalid_model_evidence_falls_back_without_publish(tmp_path):
    svc = build(tmp_path, ai_key="test-key", ai_model="test-model")
    event = svc.store.overview("replay")["events"][0]

    def handler(req):
        result = {
            "summary": "销量提升百分之九十",
            "evidence_ids": ["made-up"],
            "recommendations": [],
            "limitations": "",
        }
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(result)}}]}
        )

    result = Analyst(svc.store, svc.config, httpx.MockTransport(handler)).analyze(
        event["id"], "replay"
    )
    assert result["engine"] == "rules" and result["status"] == "failed"


def test_api_static_replay_evidence_feedback_and_reset(tmp_path):
    app = create_app(Config(database_path=tmp_path / "app.db"), start_scheduler=False)
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/static/style.css").status_code == 200
        assert client.get("/static/app.js").status_code == 200
        assert (
            client.post(
                "/api/replay", json={"scenario_id": "content-change"}
            ).status_code
            == 200
        )
        state = client.get("/api/overview?mode=replay").json()
        assert len(state["products"]) == 4 and len(state["events"]) >= 5
        event = state["events"][0]
        detail = client.get(f"/api/events/{event['id']}?mode=replay").json()
        assert detail["before"]["product_id"] == detail["after"]["product_id"]
        task = state["tasks"][0]
        assert (
            client.post(
                f"/api/tasks/{task['id']}/feedback",
                json={"mode": "replay", "status": "accepted", "note": "人工核查"},
            ).status_code
            == 200
        )
        assert client.get("/api/overview?mode=live").json()["products"] == []
        assert client.post("/api/replay/reset", json={}).status_code == 200
        assert client.get("/api/overview?mode=replay").json()["events"] == []


def test_mutations_reject_foreign_origin(tmp_path):
    with TestClient(
        create_app(Config(database_path=tmp_path / "app.db"), start_scheduler=False)
    ) as client:
        assert (
            client.post(
                "/api/replay/reset",
                json={},
                headers={"Origin": "https://foreign.example"},
            ).status_code
            == 403
        )
        assert client.get("/api/overview?mode=unknown").status_code == 422


def test_real_scheduler_executes_configured_source(tmp_path):
    from threading import Event
    from backend.settings import SourceConfig
    from datetime import datetime, timezone

    called = Event()

    def handler(req):
        called.set()
        return httpx.Response(
            200,
            json=dict(
                product_id="2001",
                title="测试商品",
                observed_at=datetime.now(timezone.utc).isoformat(),
            ),
        )

    source = SourceConfig(
        source_id="provider",
        kind="provider",
        permitted=True,
        permission_ref="test",
        base_url="https://example.test",
        token="test",
        product_ids=["2001"],
    )
    app = create_app(
        Config(database_path=tmp_path / "scheduler.db", sources=[source]),
        transport=httpx.MockTransport(handler),
    )
    with TestClient(app) as client:
        assert client.get("/api/health").json()["scheduler"]
        assert called.wait(4), "scheduler did not send a request"
    assert len(app.state.service.store.overview("live")["products"]) == 1


def test_source_response_never_exposes_tokens(tmp_path):
    from backend.settings import SourceConfig

    source = SourceConfig(
        source_id="official",
        kind="official",
        app_key="private-app-key",
        app_secret="private-secret",
        session="private-session",
    )
    with TestClient(
        create_app(
            Config(database_path=tmp_path / "secrets.db", sources=[source]),
            start_scheduler=False,
        )
    ) as client:
        text = client.get("/api/overview").text
        assert all(
            secret not in text
            for secret in [source.app_key, source.app_secret, source.session]
        )


def test_replay_source_actions_cannot_call_or_modify_live_source(tmp_path):
    from backend.settings import SourceConfig

    source = SourceConfig(
        source_id="provider",
        kind="provider",
        permitted=True,
        permission_ref="test",
        base_url="https://example.test",
        token="test",
        product_ids=["2001"],
    )
    calls = []
    app = create_app(
        Config(database_path=tmp_path / "isolated.db", sources=[source]),
        start_scheduler=False,
        transport=httpx.MockTransport(lambda req: calls.append(req)),
    )
    with TestClient(app) as client:
        for action in ["collect", "pause", "resume"]:
            assert (
                client.post(
                    f"/api/sources/provider/{action}", json={"mode": "replay"}
                ).status_code
                == 409
            )
    assert calls == []
    assert app.state.service.store.state("live:provider") is None


def test_expired_quotes_are_labeled_and_not_current_comparisons(tmp_path):
    from backend.settings import SourceConfig
    from backend.domain import Snapshot

    source = SourceConfig(
        source_id="provider",
        kind="provider",
        permitted=True,
        permission_ref="test",
        base_url="https://example.test",
        token="test",
        product_ids=["2001"],
    )
    app = create_app(
        Config(database_path=tmp_path / "stale.db", sources=[source]),
        start_scheduler=False,
    )
    app.state.service.store.ingest(
        Snapshot(
            product_id="2001",
            title="旧报价",
            role="competitor",
            source_id="provider",
            observed_at="2020-01-01T00:00:00+08:00",
            price_cents=9900,
            coupon_cents=2000,
            coupon_threshold_cents=0,
            price_confirmed=True,
            conditions="公开券",
            spec="500ml",
        ),
        "live",
    )
    with TestClient(app) as client:
        product = client.get("/api/overview?mode=live").json()["products"][0]
        assert product["freshness"] == "stale"
        assert product["payable_cents"] is None
        assert product["historical_payable_cents"] == 7900


def test_ai_comparison_excludes_expired_or_removed_products(tmp_path):
    from backend.domain import Snapshot
    from backend.settings import SourceConfig
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    active = SourceConfig(
        source_id="active",
        kind="provider",
        permitted=True,
        permission_ref="test",
        token="test",
        base_url="https://test.example",
        product_ids=["2001"],
        ai_allowed=True,
    )
    expired = active.model_copy(
        update={
            "source_id": "expired",
            "product_ids": ["3001"],
            "expires_at": "2020-01-01T00:00:00+08:00",
        }
    )
    config = Config(sources=[active, expired], ai_key="test", ai_model="test")
    store = Store(tmp_path / "ai-scope.db")
    original = dict(
        product_id="2001",
        title="有效商品",
        role="competitor",
        source_id="active",
        observed_at=now,
        price_cents=9900,
        coupon_cents=3000,
        coupon_threshold_cents=0,
        price_confirmed=True,
        conditions="公开券",
        spec="500ml",
    )
    store.ingest(Snapshot(**original), "live")
    store.ingest(Snapshot(**{**original, "price_cents": 8900}), "live")
    store.ingest(
        Snapshot(
            **{
                **original,
                "source_id": "expired",
                "product_id": "3001",
                "title": "EXPIRED_SECRET_TITLE",
            }
        ),
        "live",
    )
    store.ingest(
        Snapshot(**{**original, "product_id": "9999", "title": "REMOVED_SECRET_TITLE"}),
        "live",
    )
    event = store.overview("live")["events"][0]
    requests = []

    def handler(req):
        requests.append(json.loads(req.content))
        if len(requests) == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call1",
                                        "type": "function",
                                        "function": {
                                            "name": "compare_cached_products",
                                            "arguments": "{}",
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "summary": "有可比价格变化",
                                    "evidence_ids": [event["id"]],
                                    "findings": [],
                                    "hypotheses": [],
                                    "recommendations": [
                                        {
                                            "action": "核查毛利",
                                            "owner": "运营",
                                            "metric": "毛利率",
                                            "conditions": "确认成本",
                                            "evaluation_plan": "按运营确认的窗口记录毛利与咨询",
                                        }
                                    ],
                                    "limitations": "单次观测",
                                }
                            )
                        }
                    }
                ]
            },
        )

    result = Analyst(store, config, httpx.MockTransport(handler)).analyze(
        event["id"], "live"
    )
    assert result["status"] == "ok"
    tool_content = requests[1]["messages"][-1]["content"]
    assert "EXPIRED_SECRET_TITLE" not in tool_content
    assert "REMOVED_SECRET_TITLE" not in tool_content
