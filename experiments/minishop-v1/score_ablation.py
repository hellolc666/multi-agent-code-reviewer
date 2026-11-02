"""Export blinded findings, then score explicitly supplied semantic decisions."""

import argparse
import hashlib
import json
import random
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOCAL_PROJECT = HERE.parents[1]
PROJECT = (
    LOCAL_PROJECT
    if (LOCAL_PROJECT / "reviewer").is_dir()
    else Path(r"D:\desktop\AgentProjects\multi-agent-code-reviewer")
)
sys.path.insert(0, str(PROJECT))

from evals.metrics import aligned, operational, quality, score  # noqa: E402
from evals.run import load_corpus  # noqa: E402


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def inputs():
    cases, manifest = load_corpus(HERE / "data")
    cases = {c.id: c.model_dump(mode="json") for c in cases if c.split == "test"}
    records = read_jsonl(HERE / "results/runs.jsonl")
    expected = {f"{case_id}/{variant}/1" for case_id in cases for variant in "ABCD"}
    assert {r["key"] for r in records} == expected, "All 64 runs must finish before scoring"
    assert len(records) == len(expected), "Duplicate run keys"
    return cases, manifest, records


def describe_dataset():
    cases, manifest = load_corpus(HERE / "data")
    lines = [
        "# 扩充版 minishop 评测集说明",
        "",
        f"基线仓库：{manifest['repo_url']}，固定提交 `{manifest['base_commit']}`。",
        "",
        "每个样例都是从同一正常版本独立构造的一次代码变更，包含 unified diff、全部变更后 Python 源码与测试、README 和评测侧标签；仅排除无运行逻辑的 .gitignore。不是从旧片段数据集改名，也没有为每个样例创建 GitHub PR。",
        "",
        "共 20 例：开发集 4 例，正式测试集 16 例（10 个缺陷变更、6 个合理变更）。每个缺陷样例只有一个根因标签；同一根因的重复报告不会重复计分。",
        "",
        "标签由 Codex 模拟人工编写，尚未经真人签字确认。标签、独立断言以及评测结论不提供给 Reviewer。正常仓库原有测试代码作为项目上下文提供给 Reviewer。",
        "",
        "|样例|划分|预期|改动与验证内容|",
        "|---|---|---|---|",
    ]
    labels = []
    for case in cases:
        note = case.review_note.replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"|`{case.id}`|{case.split}|{'有缺陷' if case.gold_labels else '合理改动'}|{note}|"
        )
        labels.append(
            {
                k: v
                for k, v in case.model_dump(mode="json").items()
                if k not in ("repo_files", "diff")
            }
        )
    lines += [
        "",
        "## 标签如何验证",
        "",
        "`validate_dataset.py` 将每个 patch 应用到独立基线，验证应用后文件与样例保存的仓库完全一致；在正常版本和修改版本分别运行评测侧行为断言。缺陷样例应在正常版本通过、在修改版本失败；合理改动同时通过断言及仓库业务测试。测试断言削弱样例通过注入错误回执验证测试是否能拦截错误。",
        "",
        "这些动态断言用于核验评测标签，不代表 Judge 动态执行了代码。Test Mapping 仍然是静态关联分析。",
        "",
        "## 评分",
        "",
        "将最终报告的每条发现隐藏组名并打乱后，模拟人工核对缺陷语义、触发条件和影响。语义匹配之后，还必须满足同文件且位置误差不超过 2 行，才计 TP。每个根因最多匹配一次。未匹配报告为 FP；未检出标签为 FN。",
        "",
        "合理改动用于衡量误报。报告保留失败与 partial 运行；不把没有完成审查解释成没有 Bug。A/B 统计未经 Judge 的最终 findings，C/D 统计 Judge 后的最终 findings。",
        "",
        "当前数据只覆盖这个小商店的逻辑、回归和测试断言问题，没有安全漏洞标签；不能据此宣称通用安全审查能力。",
    ]
    (HERE / "DATASET.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_json(HERE / "data/labels_simulated.json", labels)


def export_blind():
    _, manifest, records = inputs()
    entries = [
        (r["key"], r["case_id"], i, f)
        for r in records
        for i, f in enumerate(r["report"].get("findings", []))
    ]
    random.Random(20261027).shuffle(entries)
    folder = HERE / "scoring"
    folder.mkdir(exist_ok=True)
    blind_path = folder / "blind_predictions.jsonl"
    assert not blind_path.exists(), "Do not replace a blinded annotation round"
    mapping, blank, blinded = {}, [], []
    for n, (key, case_id, index, finding) in enumerate(entries, 1):
        blind_id = f"p{n:04d}"
        mapping[blind_id] = {"key": key, "prediction_index": index}
        blinded.append(
            {
                "blind_id": blind_id,
                "case_id": case_id,
                **{
                    k: finding.get(k)
                    for k in (
                        "file_path",
                        "line_start",
                        "line_end",
                        "title",
                        "explanation",
                        "trigger_condition",
                        "evidence_snippet",
                        "suggestion",
                    )
                },
            }
        )
        blank.append({"blind_id": blind_id, "gold_id": None, "reason": ""})
    blind_path.write_text(
        "".join(json.dumps(p, ensure_ascii=False) + "\n" for p in blinded), encoding="utf-8"
    )
    write_json(folder / "mapping_sealed.json", mapping)
    write_json(folder / "decisions_template.json", blank)
    write_json(
        folder / "export_metadata.json",
        {
            "seed": 20261027,
            "dataset_sha256": manifest["sha256"],
            "predictions": len(entries),
            "runs_sha256": hashlib.sha256((HERE / "results/runs.jsonl").read_bytes()).hexdigest(),
            "blind_sha256": hashlib.sha256(blind_path.read_bytes()).hexdigest(),
            "instruction": "Read blind_predictions only until all semantic decisions are saved; then unblind.",
        },
    )
    print(f"Exported {len(entries)} blinded predictions from {len(records)} runs")


def calculate():
    cases, manifest, records = inputs()
    folder = HERE / "scoring"
    metadata = json.loads((folder / "export_metadata.json").read_text(encoding="utf-8"))
    assert (
        metadata["runs_sha256"]
        == hashlib.sha256((HERE / "results/runs.jsonl").read_bytes()).hexdigest()
    )
    mapping = json.loads((folder / "mapping_sealed.json").read_text(encoding="utf-8"))
    decisions = json.loads((folder / "decisions_simulated.json").read_text(encoding="utf-8"))
    assert len(decisions) == len(mapping)
    assert {d["blind_id"] for d in decisions} == set(mapping)
    assert all(d["reason"].strip() for d in decisions), (
        "Every prediction needs an explicit semantic judgment"
    )
    by_run = {r["key"]: [] for r in records}
    for d in decisions:
        loc = mapping[d["blind_id"]]
        by_run[loc["key"]].append({**d, **loc})
    case_results = []
    for r in records:
        gold = cases[r["case_id"]]["gold_labels"]
        gold_by_id = {g["id"]: g for g in gold}
        preds = r["report"].get("findings", [])
        pairs, used = [], set()
        for d in sorted(by_run[r["key"]], key=lambda d: d["prediction_index"]):
            gid = d["gold_id"]
            if gid is None:
                continue
            assert gid in gold_by_id, "Semantic match references another case"
            # Among equivalent duplicate claims, use the first correctly located claim.
            # If none is located, preserve the first semantic match to measure localization.
            if gid not in used:
                candidates = [x for x in by_run[r["key"]] if x["gold_id"] == gid]
                candidates.sort(
                    key=lambda x: (
                        not aligned(preds[x["prediction_index"]], gold_by_id[gid]),
                        x["prediction_index"],
                    )
                )
                chosen = candidates[0]
                pairs.append({"prediction_index": chosen["prediction_index"], "gold_id": gid})
                used.add(gid)
        counts = score(preds, gold, pairs)
        case_results.append(
            {
                "key": r["key"],
                "case_id": r["case_id"],
                "variant": r["variant"],
                "status": r["report"].get("status"),
                "clean": not gold,
                "prediction_count": len(preds),
                "pairs": pairs,
                **counts,
            }
        )
    metrics = {}
    for variant in "ABCD":
        runs = [r for r in records if r["variant"] == variant]
        scored = [r for r in case_results if r["variant"] == variant]
        clean = [r for r in scored if r["clean"]]
        status = Counter(r["report"].get("status") for r in runs)
        trace = [e for r in runs for e in r["report"].get("trace", [])]
        evidence = [e for r in runs for e in r["report"].get("tool_evidence", [])]
        metrics[variant] = {
            "quality": quality(scored, len(runs)),
            "operational": operational(runs),
            "statuses": dict(status),
            "clean_cases": len(clean),
            "clean_cases_with_false_alarm": sum(r["fp"] > 0 for r in clean),
            "clean_false_alarm_rate": sum(r["fp"] > 0 for r in clean) / len(clean)
            if clean
            else None,
            "judge_enabled_runs": sum(bool(r["report"].get("judge_enabled")) for r in runs),
            "judge_llm_calls": sum(
                e.get("kind") == "llm" and e.get("agent") == "judge" for e in trace
            ),
            "llm_trace_statuses": dict(
                Counter(e.get("status") for e in trace if e.get("kind") == "llm")
            ),
            "static_tool_trace": dict(
                Counter(
                    f"{e.get('tool', e.get('agent'))}:{e.get('status')}"
                    for e in trace
                    if e.get("kind") == "tool"
                )
            ),
            "evidence_tools": dict(Counter(f"{e.get('tool')}:{e.get('ok')}" for e in evidence)),
            "source_errors": [e for r in runs for e in r["report"].get("source_errors", [])],
            "agent_failures": [e for r in runs for e in r["report"].get("agents_failed", [])],
        }
    result = {
        "label_method": "AI-simulated; not independently human-confirmed",
        "human_signed_off": False,
        "dataset": manifest,
        "scoring_inputs": {
            "blind_sha256": metadata["blind_sha256"],
            "decisions_sha256": hashlib.sha256(
                (folder / "decisions_simulated.json").read_bytes()
            ).hexdigest(),
            "runs_sha256": metadata["runs_sha256"],
        },
        "runs": len(records),
        "metrics": metrics,
        "case_results": case_results,
    }
    write_json(HERE / "results/evaluation.json", result)
    print(
        json.dumps(
            {
                k: {
                    "quality": v["quality"],
                    "statuses": v["statuses"],
                    "clean_false_alarm_rate": v["clean_false_alarm_rate"],
                    "operational": v["operational"],
                }
                for k, v in metrics.items()
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("describe", "export", "score"))
    args = parser.parse_args()
    {"describe": describe_dataset, "export": export_blind, "score": calculate}[args.action]()
