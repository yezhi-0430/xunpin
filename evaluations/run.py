"""Evaluate the rules on declared synthetic snapshot cases, without network or DB writes."""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from backend.domain import Snapshot, changes


KINDS = ("price", "promotion", "title", "description", "reviews", "availability")
LABELS = dict(price="可比价格", promotion="促销与条件", title="标题", description="描述", reviews="评价样本", availability="字段覆盖")
DEFAULT_CASES = Path(__file__).with_name("change-cases.json")


def counts(tp, fp, fn):
    return dict(tp=tp, fp=fp, fn=fn, precision=tp / (tp + fp) if tp + fp else None, recall=tp / (tp + fn) if tp + fn else None)


def score(rows):
    if not rows:
        raise ValueError("empty evaluation set")
    totals = {kind: Counter(tp=0, fp=0, fn=0) for kind in KINDS}
    passed = 0
    groups = {}
    for row in rows:
        expected, actual = Counter(row["expected"]), Counter(row["actual"])
        unknown = (expected.keys() | actual.keys()) - set(KINDS)
        if unknown:
            raise ValueError(f"unknown labels: {sorted(unknown)}")
        for kind in KINDS:
            totals[kind]["tp"] += min(expected[kind], actual[kind])
            totals[kind]["fp"] += max(0, actual[kind] - expected[kind])
            totals[kind]["fn"] += max(0, expected[kind] - actual[kind])
        matched = expected == actual and not row["fact_errors"]
        passed += int(matched)
        group = groups.setdefault(row["group"], dict(total=0, passed=0))
        group["total"] += 1
        group["passed"] += int(matched)
    return dict(
        case_count=len(rows),
        cases_passed=passed,
        exact_match_rate=passed / len(rows),
        fact_failed_cases=sum(bool(row["fact_errors"]) for row in rows),
        micro=counts(*(sum(value[key] for value in totals.values()) for key in ("tp", "fp", "fn"))),
        per_kind={kind: counts(**value) for kind, value in totals.items()},
        groups=groups,
        rows=rows,
    )


def evaluate(dataset):
    cases = dataset["cases"]
    ids = [case["id"] for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate case IDs")
    rows = []
    for case in cases:
        before = {**dataset["base"], **case["before"]}
        after = {**before, **case["after"]}
        events = changes(Snapshot(**before), Snapshot(**after))
        errors = []
        for kind, expected_facts in case.get("facts", {}).items():
            matches = [event for event in events if event["kind"] == kind]
            if len(matches) != 1:
                errors.append(f"{kind}: expected one event, received {len(matches)}")
                continue
            for key, expected in expected_facts.items():
                actual = matches[0]["facts"].get(key)
                if actual != expected:
                    errors.append(f"{kind}.{key}: expected {expected!r}, received {actual!r}")
        rows.append(dict(id=case["id"], group=case["group"], note=case["note"], expected=case["expected"], actual=[event["kind"] for event in events], fact_errors=errors))
    result = score(rows)
    result.update(dataset_version=dataset.get("version", "unknown"), scope=dataset.get("scope", "unspecified"))
    return result


def percent(value):
    return "不可计算（零分母）" if value is None else f"{value:.1%}"


def markdown(report):
    lines = [
        "# 变化识别离线评估",
        "",
        f"数据集版本：{report['dataset_version']}；SHA-256：`{report['dataset_sha256']}`。",
        "",
        report["scope"],
        "",
        "评估对象是 `backend.domain.changes`，未调用商品 API 或大模型。标签与程序由同一项目编写，存在覆盖偏差；高分不能证明真实数据泛化能力。持久化去重、来源状态机和模型语义质量不由本报告评分，另见自动测试和后续接入验收。",
        "",
        f"案例完全匹配：**{report['cases_passed']} / {report['case_count']}**（{percent(report['exact_match_rate'])}）；事实断言失败案例：{report['fact_failed_cases']}。",
        "",
        f"事件标签微平均：精确率 {percent(report['micro']['precision'])}，召回率 {percent(report['micro']['recall'])}。",
        "",
        "| 类型 | TP | FP | FN | 精确率 | 召回率 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for kind, values in report["per_kind"].items():
        lines.append(f"| {LABELS[kind]} | {values['tp']} | {values['fp']} | {values['fn']} | {percent(values['precision'])} | {percent(values['recall'])} |")
    lines += ["", "TP 为正确事件标签，FP 为多报，FN 为漏报。同一案例可能包含多个事件；金额等事实错误会让案例失败，即使事件标签正确。无变化案例通过完全匹配率和分组统计体现，避免仅报事件精确率遗漏正常样本。", "", "## 分组覆盖", "", "| 分组 | 通过 | 总数 |", "| --- | ---: | ---: |"]
    for name, values in report["groups"].items():
        lines.append(f"| {name} | {values['passed']} | {values['total']} |")
    lines += ["", "## 逐案例结果", "", "| 案例 | 说明 | 期望事件 | 实际事件 | 结果 |", "| --- | --- | --- | --- | --- |"]
    for row in report["rows"]:
        matched = Counter(row["expected"]) == Counter(row["actual"]) and not row["fact_errors"]
        expected = "、".join(row["expected"]) or "无"
        actual = "、".join(row["actual"]) or "无"
        lines.append(f"| {row['id']} | {row['note']} | {expected} | {actual} | {'通过' if matched else '失败'} |")
        for error in row["fact_errors"]:
            lines.append(f"\n事实错误 `{row['id']}`：{error}\n")
    lines += ["", "## 重跑", "", "```powershell", "uv run python -m evaluations.run --check --json artifacts/evaluation.json --markdown artifacts/evaluation.md", "```", "", "`--check` 在案例或事实不匹配时返回非零退出码。评估只读本地案例，不读取 .env、不修改工作台数据库，也不请求网络。"]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--markdown", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    raw = args.cases.read_bytes()
    result = evaluate(json.loads(raw.decode("utf-8")))
    result["dataset_sha256"] = hashlib.sha256(raw).hexdigest()
    for path, content in ((args.json, json.dumps(result, ensure_ascii=False, indent=2) + "\n"), (args.markdown, markdown(result))):
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
    print(f"Synthetic rule evaluation: {result['cases_passed']}/{result['case_count']} exact cases; TP={result['micro']['tp']} FP={result['micro']['fp']} FN={result['micro']['fn']}; fact-failed cases={result['fact_failed_cases']}")
    for row in result["rows"]:
        if Counter(row["expected"]) != Counter(row["actual"]) or row["fact_errors"]:
            print(f"FAIL {row['id']}: expected={row['expected']} actual={row['actual']} facts={row['fact_errors']}")
    return int(args.check and result["cases_passed"] != result["case_count"])


if __name__ == "__main__":
    raise SystemExit(main())
