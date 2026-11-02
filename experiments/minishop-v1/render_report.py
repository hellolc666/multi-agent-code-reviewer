"""Render measured results without making an architecture-superiority claim."""

import json
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent


def pct(value):
    return "N/A" if value is None else f"{value:.1%}"


def known(value):
    return "未完整记录" if value is None else str(value)


def main():
    result = json.loads((HERE / "results/evaluation.json").read_text(encoding="utf-8"))
    meta = json.loads((HERE / "results/metadata.json").read_text(encoding="utf-8"))
    records = [
        json.loads(x)
        for x in (HERE / "results/runs.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    metrics = result["metrics"]
    lines = [
        "# 扩充版 minishop：四组消融实验结果",
        "",
        "本次已对固定的 16 个测试样例完成 64 次真实 Qwen 审查。标签和报告语义匹配由 Codex 模拟人工完成，尚无独立真人确认。所有失败与 partial 结果均保留在指标分母中。",
        "",
        "## 数据和运行条件",
        "",
        f"基线为 `{result['dataset']['base_commit']}`；数据指纹为 `{result['dataset']['sha256']}`。",
        "",
        "新数据共 20 例：4 例开发集仅用于检查框架，16 例正式测试集包含 10 个缺陷变更和 6 个合理改动。各例是独立 diff 加全部变更后 Python 源码、测试和 README；只排除无运行逻辑的 .gitignore，未分别发布为 GitHub PR。",
        "",
        f"模型 `{meta['model']}`，temperature=0，每组每例一次。按样例轮换组序、串行运行。具体预算、依赖和代码指纹见 `results/metadata.json`。",
        "",
        "|组|实际架构|",
        "|---|---|",
        "|A|单个 General Reviewer，无证据检索循环、无 Judge|",
        "|B|五个 Reviewer，Ruff / Semgrep / AST 静态工具，无 Judge|",
        "|C|与 B 相同的专家和静态工具配置，加 Judge|",
        "|D|规则动态路由、有限次数的证据工具、按路由选用的静态工具和 Judge|",
        "",
        "A 是现有共享适配器的通用基线。A 通过提示词接收 diff，没有额外源码检索工具；其他架构的上下文和工具可见性不同。因此 A 与 B 的差异不能仅归因于 Agent 数量。",
        "",
        "## 缺陷检出指标",
        "",
        "先盲化组别进行语义核对，再要求同文件且与标签锚点相差不超过 2 行。一个根因最多计一个 TP；重复、无根据推测、原有问题和纯风格建议均记为 FP。Precision=TP/(TP+FP)，Recall=TP/(TP+FN)，F1=2TP/(2TP+FP+FN)。",
        "",
        "|组|TP|FP|FN|Precision|Recall|F1|合理改动误报率|",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for group, m in metrics.items():
        q = m["quality"]
        lines.append(
            f"|{group}|{q['tp']}|{q['fp']}|{q['fn']}|{pct(q['precision'])}|{pct(q['recall'])}|{pct(q['f1'])}|{m['clean_cases_with_false_alarm']}/{m['clean_cases']} ({pct(m['clean_false_alarm_rate'])})|"
        )
    lines += [
        "",
        "合理改动误报率指 6 个无缺陷样例中至少产生一个 FP 的比例。A/B 用未经 Judge 的最终 findings 评分；C/D 用 Judge 后的最终 findings 评分。没有把 A/B 候选伪装成 confirmed。",
        "",
        "## 运行稳定性与开销",
        "",
        "|组|completed|partial|failed/其他|完整完成率|耗时中位数 / P95（秒）|LLM 请求数|输入 / 输出 token|",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for group, m in metrics.items():
        op, status = m["operational"], m["statuses"]
        other = op["runs"] - status.get("completed", 0) - status.get("partial", 0)
        lines.append(
            f"|{group}|{status.get('completed', 0)}|{status.get('partial', 0)}|{other}|{pct(op['completion_rate'])}|{op['latency_p50_seconds']:.1f} / {op['latency_p95_seconds']:.1f}|{op['llm_requests']}|{known(op['input_tokens'])} / {known(op['output_tokens'])}|"
        )
    lines += [
        "",
        "耗时包括当前运行环境的工具和模型调用。请求数包含修复等调用。金额费用未验证，保持 null。partial 表示审查未完整完成，不能解读为该变更安全。",
        "",
        "失败归因按每次运行中的 `(agent, error)` 去重，避免同一个错误在多个记录层级被重复计数：",
        "",
        "|组|Agent|错误类别|涉及运行数|",
        "|---|---|---|---:|",
    ]
    for group in "ABCD":
        counts = Counter()
        for r in records:
            if r["variant"] == group:
                counts.update(
                    {
                        (e.get("agent", "unknown"), e.get("error", "unknown"))
                        for e in r["report"].get("agents_failed", [])
                    }
                )
        for (agent, error), count in sorted(counts.items()):
            lines.append(f"|{group}|{agent}|{error}|{count}|")
    lines += [
        "",
        "## 实际调用证据",
        "",
        "|组|启用 Judge 的运行数|Judge LLM 调用数|静态工具 trace|证据工具记录|",
        "|---|---:|---:|---|---|",
    ]
    for group, m in metrics.items():
        lines.append(
            f"|{group}|{m['judge_enabled_runs']}|{m['judge_llm_calls']}|{json.dumps(m['static_tool_trace'], ensure_ascii=False)}|{json.dumps(m['evidence_tools'], ensure_ascii=False)}|"
        )
    lines += [
        "",
        "D 组实际调用了 `get_changed_hunks` 和 `search_references`，其中一次符号关联检索失败。工具记录不能说明已读取完整代码库。D 组未调用 Semgrep，这是路由选择的结果。Judge 启用也不保证每例都产生 Judge 模型调用：没有候选或静态预检直接处理时可能不调用模型。Test Mapping 仍不是动态覆盖率，Judge 仍不是执行测试来证实 Bug。",
        "",
        "## 逐例结果",
        "",
        "每格为 `TP / FP / FN；运行状态`。",
        "",
        "|样例|A|B|C|D|",
        "|---|---|---|---|---|",
    ]
    lookup = {(r["case_id"], r["variant"]): r for r in result["case_results"]}
    for case in dict.fromkeys(r["case_id"] for r in result["case_results"]):
        cells = []
        for group in "ABCD":
            c = lookup[(case, group)]
            cells.append(f"{c['tp']} / {c['fp']} / {c['fn']}; {c['status']}")
        lines.append(f"|`{case}`|" + "|".join(cells) + "|")
    lines += [
        "",
        "## 四组差异与失败原因",
        "",
        "- A→B：TP 都是 8，FP 从 5 增至 85，正式测试中的 16 次 B 运行全部为 partial。B 的大量最终发现是文档字符串和静态测试关联提示，未对应本次变更的缺陷。当前多专家组合没有改善这组题的召回率。",
        "- B→C：FP 从 85 降至 1，TP 也从 8 降至 0。C 的 16 次运行全部 partial，实际只有 1 次 Judge LLM 调用；其余大部分候选在 Judge 的行号/证据片段预检中被拒或置为 uncertain。这反映了端到端候选格式与验证入口之间的适配问题，不能单独归因于 Judge 模型判断。",
        "- C→D：D 组检出 5 个缺陷，仅 1 条 FP，但有 15/16 次 partial。D 的证据工具发生了真实调用，且实际 Judge LLM 调用为 6 次；这说明完整方案部分发挥作用，同时暴露稳定性不足。",
        "- Test Mapping 和 Docs 经常因 `StructuredOutputError` 或超时进入降级；具体次数见上表。运行状态已经计入所有指标，没有按成功样例筛选。",
        "",
        "## 能支持与不能支持的结论",
        "",
        "- A→B 比较通用基线与多专家整体方案；还包含提示词和工具上下文差异。",
        "- B→C 比较启用独立验证后的端到端变化；两组分别调用模型，并非同一批候选的离线重放。",
        "- C→D 同时改变路由与证据工具，不能单独分离这两个因素的效果。",
        "- 这些指标描述当前版本（包括格式错误、超时和静态种子降级）的实际表现。不能把失败样例删掉后宣称架构更好。",
        "- 本轮仅一个小仓库、16 个正式样例、每组一次；没有安全漏洞标签、独立真人标注或重复运行置信区间。不能据此宣称统计显著或工业项目泛化优势。",
        "",
        "## 可复核材料",
        "",
        "`DATASET.md` 和 `data/labels_simulated.json` 列出标注内容；`label_validation.json` 保存正常版/变更版断言执行；`results/runs.jsonl` 保存所有原始报告；`scoring/blind_predictions.jsonl`、`decisions_simulated.json`、`mapping_sealed.json` 保存每条发现的模拟人工判定及组别映射；`results/evaluation.json` 保存指标和逐例计分。",
        "",
        "自动化检查：Reviewer 原有评测测试 7 项通过；新增产物一致性与盲化/去重/定位/失败保留评分测试通过。Ruff 检查使用项目同类规则，长样例字符串不施加行长限制。",
    ]
    (HERE / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
