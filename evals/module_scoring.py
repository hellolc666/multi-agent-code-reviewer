"""Blind export and explicit simulated semantic matching for module ablation."""

import argparse
import hashlib
import json
import random
from collections import Counter
from pathlib import Path

from evals.metrics import aligned, operational, quality, score
from evals.module_ablation import ROOT, write_json
from evals.run import load_corpus


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inputs(folder):
    metadata = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    cases, manifest = load_corpus(ROOT / "data")
    if metadata["dataset"]["sha256"] != manifest["sha256"]:
        raise ValueError("Dataset mismatch")
    records = [json.loads(s) for s in (folder / "runs.jsonl").read_text(
        encoding="utf-8").splitlines()]
    expected = {f"{c}/{v}/1" for c in metadata["cases"] for v in metadata["variants"]}
    if len(records) != len(expected) or {r["key"] for r in records} != expected:
        raise ValueError("Incomplete or duplicate runs; do not score selectively")
    return metadata, {c.id: c for c in cases}, records


def export(folder):
    metadata, _, records = inputs(folder)
    entries = [(r["key"], r["case_id"], i, f) for r in records
               for i, f in enumerate(r["report"].get("findings", []))]
    random.Random(20261101).shuffle(entries)
    mapping, blinded, decisions = {}, [], []
    for n, (key, case_id, index, finding) in enumerate(entries, 1):
        bid = f"p{n:04d}"
        mapping[bid] = {"key": key, "prediction_index": index}
        blinded.append({"id": bid, "case_id": case_id, **{
            k: finding.get(k) for k in ("file_path", "line_start", "line_end", "title",
                                        "explanation", "trigger_condition", "evidence_snippet")}})
        decisions.append({"id": bid, "gold_id": None, "reason": ""})
    target = folder / "scoring"
    target.mkdir(exist_ok=False)
    write_json(target / "blind.json", blinded)
    write_json(target / "mapping.json", mapping)
    write_json(target / "decisions_template.json", decisions)
    write_json(target / "integrity.json", {
        "runs": digest(folder / "runs.jsonl"), "blind": digest(target / "blind.json"),
        "mapping": digest(target / "mapping.json"), "live": metadata["live"],
    })
    print(f"Exported {len(blinded)} findings; explicit semantic decisions required")


def calculate(folder):
    metadata, cases, records = inputs(folder)
    if not metadata["live"]:
        raise ValueError("Mock runs cannot be used for model quality metrics")
    target = folder / "scoring"
    integrity = json.loads((target / "integrity.json").read_text())
    for key, path in (("runs", folder / "runs.jsonl"), ("blind", target / "blind.json"),
                      ("mapping", target / "mapping.json")):
        if integrity[key] != digest(path):
            raise ValueError("Scoring inputs changed")
    mapping = json.loads((target / "mapping.json").read_text())
    decisions = json.loads((target / "decisions_simulated.json").read_text(encoding="utf-8"))
    if (len(decisions) != len(mapping) or {d["id"] for d in decisions} != set(mapping)
            or any(not d["reason"].strip() for d in decisions)):
        raise ValueError("Every finding requires one explicit semantic decision")
    scored = []
    for r in records:
        preds = r["report"].get("findings", [])
        gold = [g.model_dump() for g in cases[r["case_id"]].gold_labels]
        labels = {g["id"]: g for g in gold}
        candidates = [d for d in decisions if mapping[d["id"]]["key"] == r["key"]]
        pairs, used = [], set()
        for d in candidates:
            gid = d["gold_id"]
            if gid is None:
                continue
            if gid not in labels:
                raise ValueError("Semantic match belongs to another case")
            if gid in used:
                continue
            equivalent = [x for x in candidates if x["gold_id"] == gid]
            chosen = min(equivalent, key=lambda x: (
                not aligned(preds[mapping[x["id"]]["prediction_index"]], labels[gid]),
                mapping[x["id"]]["prediction_index"]))
            pairs.append({"prediction_index": mapping[chosen["id"]]["prediction_index"],
                          "gold_id": gid})
            used.add(gid)
        scored.append({"key": r["key"], "variant": r["variant"], "clean": not gold,
                       **score(preds, gold, pairs)})
    metrics = {}
    for variant in metadata["variants"]:
        runs = [r for r in records if r["variant"] == variant]
        counts = [s for s in scored if s["variant"] == variant]
        clean = [s for s in counts if s["clean"]]
        metrics[variant] = {
            **quality(counts, len(runs)), **operational(runs),
            "clean_false_alarm_rate": sum(s["fp"] > 0 for s in clean) / len(clean)
            if clean else None,
            "statuses": dict(Counter(r["report"].get("status") for r in runs)),
            "models_used": sorted({m for r in runs for m in
                                   r["report"].get("usage", {}).get("models_used", [])}),
            "agent_failures": sum(len(r["report"].get("agents_failed", [])) for r in runs),
        }
    statuses = dict(Counter(r["report"].get("status") for r in records))
    result = {"label_method": "AI simulated; no independent human signoff",
              "human_signed_off": False, "metadata": metadata, "metrics": metrics,
              "benchmark_status": "exploratory_degraded"
              if statuses.get("partial") or statuses.get("failed") else "exploratory",
              "run_statuses": statuses, "causal_claims_supported": False,
              "case_results": scored, "scoring_integrity": integrity,
              "decisions_sha256": digest(target / "decisions_simulated.json"),
              "scorer_sha256": digest(Path(__file__))}
    contrasts = {}
    for reduced in ("D_no_judge", "D_no_evidence", "D_no_routing", "D_no_static"):
        if "D" not in metrics or reduced not in metrics:
            continue
        contrasts[reduced] = {
            k: metrics["D"][k] - metrics[reduced][k]
            if metrics["D"][k] is not None and metrics[reduced][k] is not None else None
            for k in ("tp", "fp", "fn", "precision", "recall", "f1", "completion_rate",
                      "input_tokens", "output_tokens", "llm_requests")
        }
    result["paired_deltas_full_minus_removed"] = contrasts
    write_json(folder / "evaluation.json", result)
    rows = ["# 多模块消融评测结果", "",
            f"运行状态：{statuses}。这是探索性结果，不是经过独立验证的架构优劣结论。",
            "存在失败或 partial 时，质量分数必须与完成率一起读；未检出不能解释为已排除缺陷。",
            "本轮按已植入行为缺陷闭集评分。未匹配的文档、风格、静态测试缺口建议也计入基准 FP，"
            "这不等于全部建议都是错误的。",
            "AI 模拟人工标注与语义匹配，未经独立真人复核。"
            f"{len(metadata['cases'])} 个样例、单次运行，不能作显著性结论。",
            "失败和 partial 保留在分母；未匹配发现及重复发现按 FP 计。位置容差 ±2 行。",
            "",
            "|组|TP|FP|FN|Precision|Recall|F1|完成率|干净样例误报率|P50 秒|",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    def fmt(value):
        return "—" if value is None else f"{value:.3f}"
    for v, m in metrics.items():
        cells = [str(m[k]) for k in ("tp", "fp", "fn")]
        cells += [fmt(m[k]) for k in ("precision", "recall", "f1", "completion_rate",
                                     "clean_false_alarm_rate", "latency_p50_seconds")]
        rows.append(f"|{v}|" + "|".join(cells) + "|")
    rows += ["", "## 单模块对照差值", "",
             "下表均为完整 D 减去移除模块的组；F1/召回差值为正表示本轮完整方案更高，"
             "FP 差值为负表示完整方案误报更少。小样本单次差异不构成统计显著性。", "",
             "|移除模块的组|ΔTP|ΔFP|ΔRecall|ΔF1|Δ完成率|Δ请求数|",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for group, delta in contrasts.items():
        rows.append(f"|{group}|" + "|".join(fmt(delta[k]) for k in
                    ("tp", "fp", "recall", "f1", "completion_rate", "llm_requests")) + "|")
    rows += ["", "各组完整 token、请求数、耗时和失败明细见 evaluation.json 与 runs.jsonl。",
             "费用仅在配置价格且提供用量时统计；未知值保留 null。",
             "D 与各 D_no_* 为单因素对照；A/B/C/D 为架构比较，不能全部解释为单因素效应。",
             "静态工具组仅移除 Ruff、Semgrep、Quality AST 复杂度；保留解析、安全路径及锚点校验。",
             "测试映射是静态关联；Judge 不执行测试。评测侧断言仅用于验证标签。"]
    (folder / "REPORT.md").write_text("\n".join(rows) + "\n", encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["export", "score"])
    parser.add_argument("folder", type=Path)
    args = parser.parse_args()
    {"export": export, "score": calculate}[args.action](args.folder)
