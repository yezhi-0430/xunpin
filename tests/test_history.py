import json
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.ai import Analyst
from backend.app import create_app
from backend.settings import Config
from backend.storage import Store


def archive(tmp_path, content=None, member="item_desc_dataset.txt"):
    path = tmp_path / "fixture.zip"
    text = content if content is not None else "\n".join(f"构造商品{i}\t构造收纳文案{i}，便于整理。" for i in range(40))
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(member, text)
    return path


def imported(tmp_path, ai_allowed=False):
    from backend.history import History
    store = Store(tmp_path / "history.db")
    history = History(store)
    history.import_zip(archive(tmp_path), limit=5, seed=9717, ai_allowed=ai_allowed)
    return store, history


def test_history_import_is_deterministic_traceable_and_isolated(tmp_path):
    store, history = imported(tmp_path)
    first = history.list_samples()
    assert first["total"] == 5
    assert first["dataset"]["valid_rows"] == 40
    assert first["dataset"]["source_observed_at"] is None
    assert first["dataset"]["license_note"]
    ids = [s["id"] for s in first["samples"]]
    sample = history.get(ids[0])
    assert sample["line_number"] >= 1 and len(sample["archive_sha256"]) == 64
    assert not any(k in sample for k in ["product_id", "price_cents", "observed_at"])
    assert store.overview("live")["products"] == store.overview("replay")["products"] == []
    history.save_analysis(ids[0], {"status":"ok", "summary":"构造分析"})
    history.import_zip(tmp_path / "fixture.zip", limit=5, seed=9717)
    assert [s["id"] for s in history.list_samples()["samples"]] == ids
    assert history.get(ids[0])["analysis"]["summary"] == "构造分析"


def test_malformed_and_oversize_rows_are_counted_without_fabricated_fields(tmp_path):
    from backend.history import History
    history = History(Store(tmp_path / "h.db"))
    path = archive(tmp_path, "标题\t真实字段内容\n缺少描述\n\t空标题\n标题\t描述\t第三列\n" + "a" * 25000 + "\t过长\n另一个标题\t另一个描述")
    result = history.import_zip(path, limit=10)
    assert result["valid_rows"] == 2 and result["skipped_rows"] == 4
    assert result["sample_count"] == 2


@pytest.mark.parametrize("member", ["../item_desc_dataset.txt", "other.txt"])
def test_zip_member_is_fixed_and_not_extracted(tmp_path, member):
    from backend.history import History
    with pytest.raises(ValueError, match="member"):
        History(Store(tmp_path / "h.db")).import_zip(archive(tmp_path, member=member))
    assert not (tmp_path / "item_desc_dataset.txt").exists()


def history_result(sample):
    return dict(schema_version="2", summary="文案提到收纳场景，实际适用性待核实。", evidence_ids=[sample["id"]], findings=[dict(category="use_case",observation="文案声称便于整理",citations=[dict(ref="sample:description",quote="便于整理")])], hypotheses=[], recommendations=[], limitations="只有单份历史文案，不能推断当前状态、前后变化或真实效果。")


def test_history_requires_explicit_ai_permission(tmp_path):
    store, history = imported(tmp_path)
    sample = history.get(history.list_samples()["samples"][0]["id"])
    calls=[]
    def handler(req):
        calls.append(req)
        return httpx.Response(200,json={})
    result = Analyst(store,Config(ai_key="test",ai_model="test"),httpx.MockTransport(handler)).analyze_history(sample)
    assert result["status"] == "restricted" and calls == []


@pytest.mark.parametrize("invented", [False, True])
def test_history_model_cites_only_this_sample_and_uses_one_request(tmp_path, invented):
    store, history = imported(tmp_path, ai_allowed=True)
    sample = history.get(history.list_samples()["samples"][0]["id"])
    calls=[]
    output=history_result(sample)
    if invented: output["findings"][0]["citations"][0]["quote"]="不存在的承诺"
    def handler(req):
        calls.append(json.loads(req.content))
        return httpx.Response(200,json={"choices":[{"message":{"content":json.dumps(output)}}]})
    result=Analyst(store,Config(ai_key="test",ai_model="test"),httpx.MockTransport(handler)).analyze_history(sample)
    assert len(calls)==1 and "tools" not in calls[0]
    assert result["status"] == ("failed" if invented else "ok")
    if not invented: assert result["findings"][0]["citations"][0]["snapshot"] == "sample"


def test_history_api_lists_details_and_saves_status_without_monitor_events(tmp_path):
    store, history=imported(tmp_path)
    client=TestClient(create_app(Config(database_path=store.path),start_scheduler=False))
    response=client.get('/api/history?limit=2&offset=1')
    assert response.status_code==200
    data=response.json()
    assert data["total"]==5 and len(data["samples"])==2
    sample_id=data["samples"][0]["id"]
    detail=client.get('/api/history/'+sample_id)
    assert detail.status_code==200 and detail.json()["description"]
    result=client.post('/api/history/'+sample_id+'/analyze',json={})
    assert result.status_code==200 and result.json()["status"]=="unconfigured"
    assert client.get('/api/history/'+sample_id).json()["analysis"]["status"]=="unconfigured"
    assert client.get('/api/overview?mode=live').json()["events"]==[]
    assert client.get('/api/history?limit=1000').status_code==422
    assert client.get('/api/history/missing').status_code==404


def test_empty_history_has_explicit_empty_state(tmp_path):
    client=TestClient(create_app(Config(database_path=tmp_path/'empty.db'),start_scheduler=False))
    response=client.get('/api/history')
    assert response.status_code==200 and response.json()==dict(dataset=None,total=0,samples=[])


@pytest.mark.parametrize("label", ["product_id", "淘宝商品ID"])
def test_history_record_id_cannot_be_claimed_as_product_id(tmp_path, label):
    store, history = imported(tmp_path, ai_allowed=True)
    sample = history.get(history.list_samples()["samples"][0]["id"])
    output = history_result(sample)
    output["summary"] = f"{label}: {sample['id']}."
    requests = []
    def handler(req):
        requests.append(json.loads(req.content))
        return httpx.Response(200, json={"choices":[{"message":{"content":json.dumps(output)}}]})
    result = Analyst(store, Config(ai_key="test", ai_model="test"), httpx.MockTransport(handler)).analyze_history(sample)
    assert result["status"] == "failed"
    packet = json.loads(requests[0]["messages"][1]["content"])
    assert "product_id" not in packet["event"]
    assert packet["event"]["sample_id"] == sample["id"]


@pytest.mark.parametrize("failure", ["budget", "network"])
def test_failed_retry_retains_success_and_latest_attempt(tmp_path, monkeypatch, failure):
    store, history = imported(tmp_path, ai_allowed=True)
    sample = history.get(history.list_samples()["samples"][0]["id"])
    calls = []
    def handler(req):
        calls.append(req)
        if len(calls) > 1:
            raise httpx.ConnectError("fixture offline")
        return httpx.Response(200, json={"choices":[{"message":{"content":json.dumps(history_result(sample))}}]})
    original_init = Analyst.__init__
    def init(self, store, config, transport=None):
        original_init(self, store, config, httpx.MockTransport(handler))
    monkeypatch.setattr(Analyst, "__init__", init)
    client = TestClient(create_app(Config(database_path=store.path, ai_key="test", ai_model="test", ai_daily_budget=1 if failure == "budget" else 20), start_scheduler=False))
    url = '/api/history/' + sample['id']
    first = client.post(url + '/analyze', json={}).json()
    assert first['status'] == 'ok'
    second = client.post(url + '/analyze', json={}).json()
    assert second['status'] == 'failed'
    saved = client.get(url).json()['analysis']
    assert saved['status'] == 'ok' and saved['findings'] == first['findings']
    assert saved['last_attempt']['status'] == 'failed'
    assert second['preserved_previous'] is True and second['saved_analysis'] == saved
    assert client.get('/api/history').json()['samples'][0]['last_attempt_status'] == 'failed'
    assert len(calls) == (1 if failure == 'budget' else 2)
