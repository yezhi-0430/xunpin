import pytest

from evaluations.run import evaluate, score


def test_score_counts_extra_labels_missing_labels_and_wrong_facts():
    rows = [
        {"id": "one", "group": "a", "expected": ["price"], "actual": ["price", "promotion"], "fact_errors": []},
        {"id": "two", "group": "a", "expected": ["title"], "actual": [], "fact_errors": []},
        {"id": "three", "group": "b", "expected": ["price"], "actual": ["price"], "fact_errors": ["wrong amount"]},
        {"id": "four", "group": "b", "expected": [], "actual": [], "fact_errors": []},
    ]
    result = score(rows)
    assert result["micro"] == {"tp": 2, "fp": 1, "fn": 1, "precision": 2 / 3, "recall": 2 / 3}
    assert result["cases_passed"] == 1 and result["case_count"] == 4
    assert result["fact_failed_cases"] == 1
    assert result["per_kind"]["promotion"]["fp"] == 1
    assert result["per_kind"]["title"]["fn"] == 1


def test_no_positive_cases_have_undefined_precision_and_recall():
    result = score([{"id": "one", "group": "a", "expected": [], "actual": [], "fact_errors": []}])
    assert result["micro"]["precision"] is None
    assert result["micro"]["recall"] is None
    with pytest.raises(ValueError, match="empty"):
        score([])


def test_evaluation_checks_numeric_facts_and_rejects_duplicate_ids():
    base = dict(product_id="eval", title="样例", role="competitor", source_id="evaluation", observed_at="2026-10-21T10:00:00+08:00", spec="500ml", conditions="单件", price_cents=1000, coupon_cents=0, price_confirmed=True)
    case = dict(id="one", group="price", note="deliberately wrong fact", before={}, after={"price_cents": 900}, expected=["price"], facts={"price": {"difference_cents": -200}})
    result = evaluate({"base": base, "cases": [case], "version": "test", "scope": "test"})
    assert result["cases_passed"] == 0
    assert result["rows"][0]["fact_errors"]
    with pytest.raises(ValueError, match="duplicate"):
        evaluate({"base": base, "cases": [case, case]})
