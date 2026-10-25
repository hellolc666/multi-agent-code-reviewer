"""Human-reviewed semantic pairs, then deterministic file/line alignment."""

import math
import statistics


def aligned(prediction, gold, tolerance=2):
    return (
        prediction["file_path"] == gold["file_path"]
        and isinstance(prediction.get("line_start"), int)
        and abs(prediction["line_start"] - gold["line"]) <= tolerance
    )


def score(predictions, gold, pairs):
    """pairs: manually confirmed semantic equivalence, prediction index -> gold id.

    Never infer semantic equivalence from spatial proximity or severity alone.
    One-to-one mapping leaves duplicate claims as false positives.
    """
    gold_by_id = {g["id"]: g for g in gold}
    used_pred, used_gold = set(), set()
    tp = 0
    for pair in pairs:
        index, gold_id = pair["prediction_index"], pair["gold_id"]
        if (
            not isinstance(index, int)
            or index < 0
            or index >= len(predictions)
            or gold_id not in gold_by_id
            or index in used_pred
            or gold_id in used_gold
        ):
            raise ValueError("invalid or duplicate human match")
        used_pred.add(index)
        used_gold.add(gold_id)
        tp += int(aligned(predictions[index], gold_by_id[gold_id]))
    return {
        "tp": tp,
        "fp": len(predictions) - tp,
        "fn": len(gold) - tp,
        "semantic_pairs": len(pairs),
        "located_pairs": tp,
    }


def quality(counts, prs):
    totals = {
        k: sum(c[k] for c in counts) for k in ("tp", "fp", "fn", "semantic_pairs", "located_pairs")
    }
    tp, fp, fn = (totals[k] for k in ("tp", "fp", "fn"))
    # Undefined denominators stay null, including empty clean-only evaluations.
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    return {
        **totals,
        "precision": precision,
        "recall": recall,
        "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
        "false_positives_per_pr": fp / prs if prs else None,
        "location_accuracy": totals["located_pairs"] / totals["semantic_pairs"]
        if totals["semantic_pairs"]
        else None,
    }


def operational(records):
    times = sorted(r["elapsed_seconds"] for r in records)
    requests = [r["report"].get("usage", {}).get("llm_requests") for r in records]
    result = {
        "runs": len(records),
        "completed": sum(r["report"].get("status") == "completed" for r in records),
        "skipped": sum(r["report"].get("status") == "skipped" for r in records),
        "latency_p50_seconds": statistics.median(times) if times else None,
        "latency_p95_seconds": times[math.ceil(0.95 * len(times)) - 1] if times else None,
        "llm_requests": sum(requests) if all(x is not None for x in requests) else None,
    }
    result["completion_rate"] = result["completed"] / len(records) if records else None
    for key in ("input_tokens", "output_tokens", "estimated_cost"):
        values = [r["report"].get("usage", {}).get(key) for r in records]
        result[key] = sum(values) if values and all(v is not None for v in values) else None
    return result
