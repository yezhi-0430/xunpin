import json

import httpx
import pytest

from backend.analysis_contract import Analysis, build_packet, validate_analysis
from backend.ai import Analyst
from backend.service import MonitorService
from backend.settings import Config
from backend.storage import Store


def content_evidence(tmp_path):
    store = Store(tmp_path / "analysis.db")
    service = MonitorService(store, Config(ai_key="test", ai_model="test"))
    service.replay("content-change")
    event = next(e for e in store.overview("replay")["events"] if e["kind"] == "reviews" and e["product_id"] == "2003")
    return store, service.config, store.evidence(event["id"], "replay")


def valid_result(evidence):
    return dict(
        summary="新增评价样本提到旅行分装与防漏需求。",
        evidence_ids=[evidence["event"]["id"]],
        findings=[dict(category="review_theme", observation="样本提出分装说明与防漏顾虑", citations=[dict(ref="after:reviews:2003-r1", quote="携带时担心漏液")])],
        hypotheses=["本店可能需要补充携带说明，应先核实商品适用场景。"],
        recommendations=[dict(action="核查本店分装说明是否清晰", owner="商品运营", metric="相关咨询量", conditions="确认实物与本店说明", evaluation_plan="先记录咨询基线，再在运营确认的窗口复查")],
        limitations="仅代表本次新增样本，不能推断全体消费者。",
    )


def test_valid_citations_are_bound_to_new_reviews_and_counted_by_program(tmp_path):
    _, _, evidence = content_evidence(tmp_path)
    packet = build_packet(evidence)
    result = validate_analysis(Analysis(**valid_result(evidence)).model_dump(), packet)
    finding = result["findings"][0]
    assert finding["cited_review_count"] == 1
    assert finding["citations"][0]["snapshot"] == "after"
    assert finding["citations"][0]["review_id"] == "2003-r1"
    assert result["review_sample_total"] == 1


@pytest.mark.parametrize("citation", [
    {"ref": "after:reviews:2003-r1", "quote": "所有用户都认为携带方便"},
    {"ref": "before:reviews:2003-r1", "quote": "携带时担心漏液"},
    {"ref": "after:reviews:1001-r1", "quote": "能否加一张分装与收纳示意图"},
])
def test_invented_quote_wrong_snapshot_or_other_product_cannot_publish(tmp_path, citation):
    store, config, evidence = content_evidence(tmp_path)
    response = valid_result(evidence)
    response["findings"][0]["citations"] = [citation]
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(response)}}]}))
    result = Analyst(store, config, transport).analyze(evidence["event"]["id"], "replay")
    assert result["status"] == "failed" and result["engine"] == "rules"
    assert result["findings"] == []


def test_packet_is_bounded_and_only_sent_excerpts_can_be_cited(tmp_path):
    _, _, evidence = content_evidence(tmp_path)
    evidence["after"]["description"] = "开头" + "长描述" * 5000 + "未发送的尾部承诺"
    packet = build_packet(evidence)
    assert len(json.dumps(packet, ensure_ascii=False)) <= 30000
    assert "未发送的尾部承诺" not in json.dumps(packet, ensure_ascii=False)
    result = valid_result(evidence)
    result["findings"] = [dict(category="service_promise", observation="发现尾部承诺", citations=[dict(ref="after:description", quote="未发送的尾部承诺")])]
    with pytest.raises(ValueError, match="citation"):
        validate_analysis(Analysis(**result).model_dump(), packet)
    assert packet["truncated"]


def test_model_can_abstain_without_inventing_a_theme(tmp_path):
    store, config, evidence = content_evidence(tmp_path)
    response = valid_result(evidence)
    response.update(findings=[], hypotheses=[], recommendations=[], summary="样本不足，暂不形成语义结论。")
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(response)}}]}))
    result = Analyst(store, config, transport).analyze(evidence["event"]["id"], "replay")
    assert result["engine"] == "llm" and result["findings"] == []


def test_unavailable_model_keeps_structured_sections_empty(tmp_path):
    store, _, evidence = content_evidence(tmp_path)
    result = Analyst(store, Config()).analyze(evidence["event"]["id"], "replay")
    assert result["status"] == "unconfigured"
    assert result["findings"] == result["hypotheses"] == []


def test_repeated_citations_do_not_inflate_review_sample_count(tmp_path):
    _, _, evidence = content_evidence(tmp_path)
    result = valid_result(evidence)
    result["findings"][0]["citations"] *= 2
    validated = validate_analysis(Analysis(**result).model_dump(), build_packet(evidence))
    assert validated["findings"][0]["cited_review_count"] == 1


def test_deepseek_preset_bounds_output_and_uses_documented_parameters(tmp_path):
    store, _, evidence = content_evidence(tmp_path)
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(valid_result(evidence))}}]})

    config = Config(ai_key="test", ai_model="deepseek-flash", ai_base_url="https://api.deepseek.com")
    result = Analyst(store, config, httpx.MockTransport(handler)).analyze(evidence["event"]["id"], "replay")
    assert result["status"] == "ok"
    payload = json.loads(requests[0].content)
    assert str(requests[0].url) == "https://api.deepseek.com/chat/completions"
    assert payload["model"] == "deepseek-flash"
    assert payload["max_tokens"] == 4096 and payload["stream"] is False
    assert payload["thinking"] == {"type": "disabled"}
    assert "parallel_tool_calls" not in payload


@pytest.mark.parametrize("summary", ["新增2003条评价样本。", "新增500条评价样本。", "新增2条评价样本。"])
def test_metadata_and_product_capacity_cannot_supply_review_counts(tmp_path, summary):
    _, _, evidence = content_evidence(tmp_path)
    result = valid_result(evidence)
    result.update(summary=summary, findings=[], hypotheses=[], recommendations=[])
    with pytest.raises(ValueError, match="sample count|numeric claim"):
        validate_analysis(result, build_packet(evidence))


@pytest.mark.parametrize("summary, accepted", [
    ("标价由99元变为89元，券额由30元变为20元。", True),
    ("标价由99元变为88元，券额由30元变为20元。", False),
])
def test_promotion_amounts_use_explicit_money_fields(tmp_path, summary, accepted):
    store = Store(tmp_path / "promotion.db")
    MonitorService(store, Config()).replay("coupon-offset")
    event = next(e for e in store.overview("replay")["events"] if e["kind"] == "promotion")
    evidence = store.evidence(event["id"], "replay")
    result = valid_result(evidence)
    result.update(summary=summary, findings=[], hypotheses=[], recommendations=[])
    if accepted:
        assert validate_analysis(result, build_packet(evidence))["summary"] == summary
    else:
        with pytest.raises(ValueError, match="numeric claim"):
            validate_analysis(result, build_packet(evidence))


def test_explicit_identity_mentions_are_not_treated_as_quantities(tmp_path):
    _, _, evidence = content_evidence(tmp_path)
    result = valid_result(evidence)
    result["summary"] = f"事件 {evidence['event']['id']}，商品 ID 2003（product_id 2003）新增1条评价样本。"
    assert validate_analysis(result, build_packet(evidence))["review_sample_total"] == 1
