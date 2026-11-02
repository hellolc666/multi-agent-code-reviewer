"""Codex's frozen simulated-human semantic decisions on blinded findings.

This script intentionally has no access to mapping_sealed.json or run variants.
The accepted IDs were selected after reading every title and the full candidate
explanations/triggers/evidence for non-obvious claims in blind_predictions.jsonl.
"""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"

# A semantic match needs the same defect and a supported, feasible trigger.
# Localization and duplicate selection happen in score_ablation.py after unblinding.
ACCEPT = {
    "p0011", "p0021", "p0023", "p0026", "p0027", "p0031", "p0038", "p0040",
    "p0041", "p0042", "p0044", "p0045", "p0046", "p0047", "p0048", "p0050",
    "p0051", "p0053", "p0054", "p0065", "p0070", "p0073", "p0077", "p0079",
    "p0084", "p0094", "p0096", "p0103", "p0104", "p0108", "p0113",
}

REJECT = {
    "p0004": "把顺序修改误说成并发竞争；并发竞态并非本次变更新增，未提到后续商品失败后前项已扣库存。",
    "p0006": "只说代码移动没有明显理由，没有指出 quote 失败时真实库存已被扣减。",
    "p0009": "只说计算顺序可能改变，没有提出报价校验失败后库存被错误扣减的具体路径。",
    "p0022": "Product 是仓库定义的普通 dataclass，没有 falsy 的有效商品实例；字符串和零不是该目录的合法产品。",
    "p0030": "折扣百分比受 1 到 100 约束，且新旧算式数学等价；假设折扣超过小计不成立。",
    "p0032": "仅报告原有构造函数的复杂度数值，与本次商品可购性变更无关。",
    "p0055": "只是假设目录会存入 falsy 非 Product 对象，和仓库的类型及有效数据不符。",
    "p0067": "Python 的 int 没有固定最大值，所述整数溢出条件不成立。",
    "p0080": "虽然提到漏乘数量，但触发条件称调用仓库不存在的 calculate_total，且声称任意商品数都算错；没有可执行的触发路径。",
    "p0091": "Product 类型的有效实例恒为 truthy；列举的字符串、列表等均非此仓库合法商品。",
    "p0093": "描述新增功能和对应测试，没有提出缺陷。",
}


def main():
    labels = {c["id"]: c["gold_labels"] for c in json.loads((DATA / "labels_simulated.json").read_text(encoding="utf-8"))}
    predictions = [json.loads(s) for s in (HERE / "blind_predictions.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(predictions) == 113
    assert ACCEPT.isdisjoint(REJECT)
    decisions = []
    for p in predictions:
        bid, title = p["blind_id"], p["title"].lower()
        if bid in ACCEPT:
            gold = labels[p["case_id"]]
            assert len(gold) == 1
            gid = gold[0]["id"]
            reason = "候选解释了同一变更根因和其可行触发条件；具体行号及重复报告另由评分脚本检查。"
        elif bid in REJECT:
            gid, reason = None, REJECT[bid]
        elif (title.startswith("missing docstring") or title.startswith("uncovered:")
              or "is not covered by tests" in title):
            gid = None
            reason = "仅提出文档字符串或静态测试关联提示，未说明本次变更引入的可复现缺陷。"
        else:
            raise AssertionError(f"Unreviewed blind finding: {bid} {p['title']}")
        decisions.append({"blind_id": bid, "gold_id": gid, "reason": reason})
    assert {p["blind_id"] for p in predictions} == {d["blind_id"] for d in decisions}
    target = HERE / "decisions_simulated.json"
    assert not target.exists(), "Do not silently overwrite an annotation round"
    target.write_text(json.dumps(decisions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Reviewed {len(decisions)} blinded findings; {len(ACCEPT)} semantic candidates")


if __name__ == "__main__":
    main()
