import hashlib
from datetime import datetime, timezone

import httpx
import pytest

from backend.settings import Config, SourceConfig
from backend.sources import SourceError, fetch_snapshot, top_sign
from backend.service import MonitorService
from backend.storage import Store


def provider(**updates):
    data = dict(
        source_id="provider",
        kind="provider",
        base_url="https://data.example.test",
        token="test-token",
        permitted=True,
        permission_ref="local-test-approval",
        product_ids=["2001"],
        interval_seconds=60,
        min_gap_seconds=5,
        daily_budget=10,
    )
    data.update(updates)
    return SourceConfig(**data)


def payload():
    return dict(
        product_id="2001",
        title="洗发水 500ml",
        role="competitor",
        source_id="provider",
        observed_at=datetime.now(timezone.utc).isoformat(),
        price_cents=9900,
        coupon_cents=2000,
        coupon_threshold_cents=0,
        price_confirmed=True,
        conditions="公开券",
        spec="500ml×1",
    )


def service(tmp_path, source, handler):
    return MonitorService(
        Store(tmp_path / "test.db"),
        Config(sources=[source]),
        transport=httpx.MockTransport(handler),
    )


def test_sign_matches_official_md5_protocol():
    assert (
        top_sign({"foo": "1", "bar": "2"}, "secret")
        == hashlib.md5(b"secretbar2foo1secret").hexdigest().upper()
    )


def test_unapproved_source_never_sends_a_request(tmp_path):
    calls = []
    svc = service(tmp_path, provider(permitted=False), lambda req: calls.append(req))
    assert svc.collect("provider")["status"] == "disabled"
    assert calls == []


def test_provider_data_is_bound_to_requested_identity():
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json={**payload(), "product_id": "9999"})
    )
    with pytest.raises(SourceError, match="identity"):
        fetch_snapshot(provider(), "2001", transport)


def test_official_source_does_not_invent_coupon_description_or_reviews():
    source = SourceConfig(
        source_id="official",
        kind="official",
        app_key="key",
        app_secret="secret",
        session="session",
        permitted=True,
        permission_ref="test",
        product_ids=["1001"],
    )
    seen = []

    def handler(req):
        seen.append(req.content.decode())
        return httpx.Response(
            200,
            json={
                "item_seller_get_response": {
                    "item": {"num_iid": 1001, "title": "本店洗发水", "price": "99.00"}
                }
            },
        )

    result = fetch_snapshot(source, "1001", httpx.MockTransport(handler))
    assert result.role == "own" and result.price_cents == 9900
    assert (
        result.coupon_cents is None
        and result.description is None
        and result.reviews is None
    )
    assert "sign=" in seen[0] and "session=session" in seen[0]


def test_429_is_persisted_as_backoff_without_immediate_retry(tmp_path):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(429, headers={"Retry-After": "120"})

    svc = service(tmp_path, provider(), handler)
    assert svc.collect("provider")["status"] == "backoff"
    assert svc.collect("provider")["status"] == "backoff"
    assert len(calls) == 1
    assert (
        MonitorService(svc.store, svc.config).source_status()[0]["status"] == "backoff"
    )


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(403),
        httpx.Response(
            200, text="<html>login</html>", headers={"Content-Type": "text/html"}
        ),
        httpx.Response(
            200, json={"error_response": {"code": 27, "msg": "session invalid"}}
        ),
    ],
)
def test_auth_challenge_and_business_auth_errors_pause(tmp_path, response):
    svc = service(tmp_path, provider(), lambda req: response)
    assert svc.collect("provider")["status"] == "paused"
    assert svc.collect("provider")["status"] == "paused"


def test_success_dedup_rate_limit_and_budget(tmp_path):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200, json=payload())

    svc = service(tmp_path, provider(daily_budget=1), handler)
    assert svc.collect("provider")["status"] == "ok"
    assert svc.collect("provider")["status"] == "budget"
    assert len(calls) == 1
    assert len(svc.store.overview("live")["products"]) == 1


def test_replay_is_repeatable_and_does_not_create_duplicate_events(tmp_path):
    svc = service(
        tmp_path,
        provider(permitted=False),
        lambda req: pytest.fail("replay must not call network"),
    )
    svc.replay("baseline")
    svc.replay("price-drop")
    count = len(svc.store.overview("replay")["events"])
    svc.replay("price-drop")
    assert len(svc.store.overview("replay")["events"]) == count == 1
    assert svc.store.overview("live")["products"] == []


def test_expired_permission_never_calls_network(tmp_path):
    svc = service(
        tmp_path,
        provider(expires_at="2020-01-01T00:00:00+08:00"),
        lambda req: pytest.fail("expired permission"),
    )
    assert svc.collect("provider")["status"] == "expired"


def test_stale_data_is_quarantined(tmp_path):
    svc = service(
        tmp_path,
        provider(),
        lambda req: httpx.Response(
            200, json={**payload(), "observed_at": "2020-01-01T00:00:00+08:00"}
        ),
    )
    assert svc.collect("provider")["status"] == "paused"
    assert svc.store.overview("live")["products"] == []


def test_malformed_payload_pauses_without_overwriting_history(tmp_path):
    svc = service(
        tmp_path,
        provider(),
        lambda req: httpx.Response(200, json={"product_id": "2001"}),
    )
    assert svc.collect("provider")["status"] == "paused"
    assert svc.store.overview("live")["products"] == []


def test_three_transport_failures_enter_cooldown_then_single_probe(
    tmp_path, monkeypatch
):
    clock = [1000.0]
    monkeypatch.setattr("backend.service.time.time", lambda: clock[0])
    calls = []

    def handler(req):
        calls.append(req)
        raise httpx.ReadTimeout("timeout")

    svc = service(tmp_path, provider(), handler)
    assert svc.collect("provider")["status"] == "backoff"
    clock[0] += 31
    assert svc.collect("provider")["status"] == "backoff"
    clock[0] += 61
    assert svc.collect("provider")["status"] == "cooldown"
    assert svc.collect("provider")["status"] == "cooldown"
    assert len(calls) == 3
    clock[0] += 901
    assert svc.collect("provider")["status"] == "cooldown"
    assert len(calls) == 4


@pytest.mark.parametrize("failure", ["rate", "timeout"])
def test_error_backoff_never_shortens_source_minimum_gap(
    tmp_path, monkeypatch, failure
):
    clock = [1000.0]
    monkeypatch.setattr("backend.service.time.time", lambda: clock[0])
    calls = []

    def handler(req):
        calls.append(req)
        if failure == "timeout":
            raise httpx.ReadTimeout("test")
        return httpx.Response(429)

    svc = service(tmp_path, provider(min_gap_seconds=120), handler)
    svc.collect("provider")
    clock[0] += 31
    svc.collect("provider")
    assert len(calls) == 1
    clock[0] += 90
    svc.collect("provider")
    assert len(calls) == 2


def test_manual_resume_does_not_bypass_request_frequency(tmp_path, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("backend.service.time.time", lambda: clock[0])
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(403)

    svc = service(tmp_path, provider(min_gap_seconds=120), handler)
    svc.collect("provider")
    svc.resume("provider")
    clock[0] += 1
    svc.collect("provider")
    assert len(calls) == 1
