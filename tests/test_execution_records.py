import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.settings import Config


def client_and_task(tmp_path):
    app = create_app(Config(database_path=tmp_path / "records.db"), start_scheduler=False)
    client = TestClient(app)
    client.post("/api/replay", json={"scenario_id": "price-drop"})
    task = client.get("/api/overview?mode=replay").json()["tasks"][0]
    return client, task


def execution():
    return dict(planned_date="2026-10-22", observation_start="2026-10-22", observation_end="2026-10-29", actual_action="发布经审核的容量说明图", metric_name="容量相关咨询", metric_unit="次", baseline_value=12, outcome_value=8, data_reference="人工咨询台账", confounders="同期活动可能影响咨询量", evaluation_status="observed", evaluation_note="只报告观察，不认定因果")


def test_structured_execution_and_history_survive_restart_and_legacy_update(tmp_path):
    client, task = client_and_task(tmp_path)
    body = dict(mode="replay", status="evaluated", note="保留观察结果", execution=execution())
    response = client.post(f"/api/tasks/{task['id']}/feedback", json=body)
    assert response.status_code == 200
    assert response.json()["execution"] == execution()
    assert response.json()["history"][-1]["execution"]["baseline_value"] == 12
    # Old clients may update only status/note; that must not erase structured evidence.
    client.post(f"/api/tasks/{task['id']}/feedback", json=dict(mode="replay", status="evaluated", note="补充人工判断"))
    reopened = TestClient(create_app(Config(database_path=tmp_path / "records.db"), start_scheduler=False))
    saved = reopened.get("/api/overview?mode=replay").json()["tasks"][0]
    assert saved["execution"] == execution()
    assert saved["history"][-1]["execution"]["data_reference"] == "人工咨询台账"


@pytest.mark.parametrize("patch", [
    {"observation_end": "2026-10-20"},
    {"baseline_value": None},
    {"metric_unit": ""},
    {"data_reference": "   "},
    {"outcome_value": "NaN"},
])
def test_invalid_observation_cannot_be_saved_as_measurement(tmp_path, patch):
    client, task = client_and_task(tmp_path)
    response = client.post(f"/api/tasks/{task['id']}/feedback", json=dict(mode="replay", status="evaluated", note="待核查", execution={**execution(), **patch}))
    assert response.status_code == 422


def test_inconclusive_result_can_be_saved_without_inventing_numbers(tmp_path):
    client, task = client_and_task(tmp_path)
    record = dict(evaluation_status="inconclusive", evaluation_note="没有可比咨询台账，无法判断")
    response = client.post(f"/api/tasks/{task['id']}/feedback", json=dict(mode="replay", status="evaluated", note="无法评估", execution=record))
    assert response.status_code == 200
    assert response.json()["execution"]["evaluation_status"] == "inconclusive"
    assert response.json()["execution"]["outcome_value"] is None


def test_unexplained_inconclusive_result_and_blank_note_are_rejected(tmp_path):
    client, task = client_and_task(tmp_path)
    url = f"/api/tasks/{task['id']}/feedback"
    assert client.post(url, json=dict(mode="replay", status="evaluated", note="需核查", execution={"evaluation_status":"inconclusive"})).status_code == 422
    assert client.post(url, json=dict(mode="replay", status="accepted", note="  ")).status_code == 422


def test_evaluated_task_requires_concluded_new_execution_record(tmp_path):
    client, task = client_and_task(tmp_path)
    url = f"/api/tasks/{task['id']}/feedback"
    response = client.post(url, json=dict(mode="replay", status="evaluated", note="复盘", execution={}))
    assert response.status_code == 422
    assert client.get("/api/overview?mode=replay").json()["tasks"][0]["status"] == "pending"
