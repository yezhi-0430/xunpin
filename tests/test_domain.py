import pytest

from backend.domain import Snapshot, changes, payable
from backend.storage import Store


def sample(**updates):
    data = dict(
        product_id="2001",
        title="轻净洗发水 500ml",
        role="competitor",
        source_id="replay",
        observed_at="2026-10-21T10:00:00+08:00",
        price_cents=9900,
        coupon_cents=3000,
        coupon_threshold_cents=0,
        price_confirmed=True,
        conditions="公开券/单件/上海",
        spec="500ml×1",
        description="清洁头皮",
        reviews=[],
    )
    data.update(updates)
    return Snapshot(**data)


def test_coupon_offset_is_not_a_price_drop():
    before = sample()
    after = sample(price_cents=8900, coupon_cents=2000)
    assert payable(before) == payable(after) == 6900
    assert "price" not in {e["kind"] for e in changes(before, after)}
    assert "promotion" in {e["kind"] for e in changes(before, after)}


def test_real_drop_has_calculated_evidence():
    events = changes(sample(), sample(price_cents=8900))
    price = next(e for e in events if e["kind"] == "price")
    assert price["facts"]["before_cents"] == 6900
    assert price["facts"]["after_cents"] == 5900


def test_missing_price_or_changed_context_is_not_comparable():
    for after in [
        sample(price_cents=None),
        sample(conditions="会员专享"),
        sample(spec="100ml×1"),
        sample(price_confirmed=False),
    ]:
        assert "price" not in {e["kind"] for e in changes(sample(), after)}


def test_new_reviews_are_deduplicated_and_text_changes_are_visible():
    review = {
        "review_id": "r1",
        "text": "希望标清容量",
        "observed_at": "2026-10-21T10:00:00+08:00",
    }
    before = sample(reviews=[review])
    after = sample(
        title="便携洗发水 500ml",
        description="增加旅行收纳说明",
        reviews=[review, {**review, "review_id": "r2", "text": "携带方便"}],
    )
    events = changes(before, after)
    assert {e["kind"] for e in events} == {"title", "description", "reviews"}
    assert (
        next(e for e in events if e["kind"] == "reviews")["facts"]["sample_count"] == 1
    )


def test_persistence_snapshot_dedup_and_feedback(tmp_path):
    store = Store(tmp_path / "test.db")
    before = sample()
    assert store.ingest(before, "replay")["baseline"]
    assert store.ingest(before, "replay")["duplicate"]
    after = sample(price_cents=8900)
    assert store.ingest(after, "replay")["events_created"] == 1
    assert store.ingest(after, "replay")["duplicate"]
    state = Store(tmp_path / "test.db").overview("replay")
    assert len(state["events"]) == 1
    task = state["tasks"][0]
    store.feedback(task["id"], "accepted", "先测算毛利", "replay")
    assert store.overview("replay")["tasks"][0]["status"] == "accepted"


def test_replay_and_live_are_isolated(tmp_path):
    store = Store(tmp_path / "test.db")
    store.ingest(sample(), "replay")
    assert store.overview("live")["products"] == []


def test_out_of_order_observation_does_not_replace_current_snapshot(tmp_path):
    store = Store(tmp_path / "test.db")
    store.ingest(sample(observed_at="2026-10-21T12:00:00+08:00"), "live")
    result = store.ingest(
        sample(observed_at="2026-10-21T11:00:00+08:00", price_cents=1000), "live"
    )
    assert result["out_of_order"]
    assert store.overview("live")["products"][0]["price_cents"] == 9900
    assert store.overview("live")["events"] == []


def test_purchase_condition_change_creates_noncomparable_promotion_event():
    events = changes(sample(), sample(conditions="会员限定券"))
    assert len(events) == 1 and events[0]["kind"] == "promotion"
    assert not events[0]["facts"]["comparable"]


@pytest.mark.parametrize("field", ["price_cents", "coupon_cents", "promotion_text", "conditions", "spec"])
def test_missing_field_is_coverage_loss_and_recovery_establishes_baseline(field):
    before = sample(promotion_text="公开券")
    missing = before.model_copy(update={field: None})
    events = changes(before, missing)
    assert [event["kind"] for event in events] == ["availability"]
    assert events[0]["facts"]["fields"] == [field]
    assert changes(missing, before) == []


def test_real_promotion_change_survives_unrelated_coverage_loss():
    events = changes(sample(promotion_text="单件券"), sample(promotion_text="买赠活动", description=None))
    assert {event["kind"] for event in events} == {"promotion", "availability"}


@pytest.mark.parametrize("field", ["coupon_threshold_cents", "price_confirmed"])
def test_omitted_purchase_metadata_does_not_become_known_default(field):
    before = sample(coupon_threshold_cents=10000)
    data = before.model_dump(exclude={field})
    after = Snapshot(**data)
    assert payable(after) is None
    events = changes(before, after)
    assert [event["kind"] for event in events] == ["availability"]
    assert events[0]["facts"]["fields"] == [field]
    assert changes(after, before) == []


def test_review_dedup_survives_rolling_windows_and_restart(tmp_path):
    review = {
        "review_id": "r1",
        "text": "携带方便",
        "observed_at": "2026-10-21T10:00:00+08:00",
    }
    store = Store(tmp_path / "test.db")
    store.ingest(sample(reviews=[review]), "live")
    store.ingest(sample(reviews=[]), "live")
    store = Store(tmp_path / "test.db")
    result = store.ingest(sample(reviews=[review]), "live")
    assert result["events_created"] == 0
    assert store.overview("live")["events"] == []


def test_competitor_task_targets_matching_own_product_and_has_specific_evidence(
    tmp_path,
):
    from backend.scenarios import products_for

    store = Store(tmp_path / "target.db")
    for item in products_for(0):
        store.ingest(item, "replay")
    for item in products_for(1):
        store.ingest(item, "replay")
    task = store.overview("replay")["tasks"][0]
    assert task["observed_product_id"] == "2001"
    assert task["target_product_ids"] == ["1001"]
    assert "69.00" in task["action"] and "59.00" in task["action"]


def test_task_enrichment_preserves_existing_human_feedback(tmp_path):
    import json
    from backend.scenarios import products_for

    store = Store(tmp_path / "upgrade.db")
    for step in [0, 1]:
        for item in products_for(step):
            store.ingest(item, "replay")
    task = store.overview("replay")["tasks"][0]
    store.feedback(task["id"], "accepted", "保留人工判断", "replay")
    with store.db() as db:
        old = json.loads(
            db.execute("SELECT data FROM tasks WHERE id=?", (task["id"],)).fetchone()[0]
        )
        old.pop("observed_product_id")
        old.pop("target_product_ids")
        old["action"] = "旧版通用建议"
        db.execute("UPDATE tasks SET data=? WHERE id=?", (store.dumps(old), task["id"]))
    upgraded = Store(tmp_path / "upgrade.db").overview("replay")["tasks"][0]
    assert upgraded["target_product_ids"] == ["1001"]
    assert upgraded["note"] == "保留人工判断" and upgraded["status"] == "accepted"
    assert "69.00" in upgraded["action"]
