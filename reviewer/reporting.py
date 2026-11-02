"""Human-readable, deterministic presentation of a ReviewReport."""

from __future__ import annotations

import html
import re
from collections import Counter
from collections.abc import Mapping
from typing import Any


def _text(value: Any) -> str:
    """Escape active links and HTML while preserving inline code quotations."""
    flattened = " ".join(str(value if value is not None else "").split())
    escaped = html.escape(flattened, quote=False)
    return re.sub(r"([\\*_\[\]#])", r"\\\1", escaped)


def _location(finding: Mapping[str, Any]) -> str:
    path = _text(finding.get("file_path") or "未知文件")
    line = finding.get("line_start")
    return f"{path}:{line}" if isinstance(line, int) and line > 0 else path


def _finding_lines(finding: Mapping[str, Any], reason: str | None = None) -> list[str]:
    severity = _text(finding.get("severity") or "unknown")
    category = _text(finding.get("category") or "unknown")
    title = _text(finding.get("title") or "未命名问题")
    lines = [f"### [{severity}] {title}", "", f"位置：{_location(finding)}　类别：{category}", ""]
    for label, key in (
        ("问题", "explanation"),
        ("触发条件", "trigger_condition"),
        ("修改建议", "suggestion"),
    ):
        value = finding.get(key)
        if value:
            lines.append(f"**{label}：** {_text(value)}")
            lines.append("")
    if reason:
        lines += [f"**复核说明：** {_text(reason)}", ""]
    return lines


def render_markdown(report: Mapping[str, Any]) -> str:
    """Render a readable report without changing Judge decisions or findings."""
    findings = report.get("findings") or []
    verified = report.get("verified_findings") or []
    failures = report.get("agents_failed") or []
    status = str(report.get("status") or "unknown")
    mode = str(report.get("execution_mode") or "unknown")
    judged = bool(report.get("judge_enabled", True))
    uncertain = [item for item in verified if item.get("status") == "uncertain"]
    rejected_items = [item for item in verified if item.get("status") == "rejected"]
    counts = Counter(item.get("status") for item in verified)
    duplicates = sum(bool(item.get("duplicate_of")) for item in verified)
    lines = ["# 代码审查报告", ""]
    title = report.get("pr_title")
    if title:
        lines += [f"**变更：** {_text(title)}", ""]
    if report.get("pr_url"):
        lines += [f"**PR：** {_text(report['pr_url'])}", ""]
    elif report.get("input_mode") == "diff":
        lines += ["**输入：** 本地 diff", ""]
    lines += [
        f"**状态：** {_text(status)}　**执行方式：** {_text(mode)}",
        f"**审查文件：** {_text(report.get('files_changed', 0))}　"
        f"**报告发现：** {len(findings)}　**待复核：** {len(uncertain)}",
        "",
    ]
    if report.get("review_completed_at"):
        lines += [f"**完成时间：** {_text(report['review_completed_at'])}", ""]
    usage = report.get("usage") or {}
    models_used = usage.get("models_used") or []
    if models_used:
        lines += [
            "**实际模型：** " + "、".join(_text(model) for model in models_used),
            f"**额度切换：** {_text(usage.get('model_failovers', 0))} 次",
            "",
        ]
    if status != "completed":
        lines += ["> 审查未完整完成。没有报告的问题不能视为已排除。", ""]
    elif uncertain or report.get("verification_incomplete"):
        lines += ["> 仍有未完成复核的候选；没有报告的问题不能视为已排除。", ""]
    if mode == "mock":
        lines += ["> 此报告来自离线 Mock，仅用于演示流程，不能用于判断代码质量。", ""]
    if not judged:
        lines += ["> Judge 未启用；以下发现未经独立复核。", ""]
    lines += ["## 审查摘要", "", _text(report.get("summary") or "没有返回摘要。"), ""]
    lines += ["## 审查发现" if not judged else "## 已确认的问题", ""]
    if not findings:
        lines += ["没有已确认的问题。" if judged else "没有报告的问题。", ""]
    for finding in findings:
        matched = next(
            (
                item
                for item in verified
                if (item.get("finding") or {}).get("id") == finding.get("id")
            ),
            None,
        )
        lines += _finding_lines(finding, matched.get("reason") if matched else None)
    lines += ["## 复核与覆盖情况", ""]
    if judged:
        lines += [
            f"已确认 {len(findings)} 个独立问题（{counts['confirmed']} 条确认候选，"
            f"合并 {duplicates} 条重复），驳回 {len(rejected_items)} 条，"
            f"待复核 {len(uncertain)} 条。",
            "",
        ]
    else:
        lines += ["Judge 未启用，报告中的发现仍需人工核查。", ""]
    if uncertain:
        lines += ["### 待复核的候选", ""]
        for item in uncertain:
            finding = item.get("finding") or {}
            lines += [
                f"- {_location(finding)}：{_text(finding.get('title') or '未命名问题')}；"
                f"原因：{_text(item.get('reason') or '证据不足')}"
            ]
        lines.append("")
    if rejected_items:
        lines += ["### 已驳回的候选（诊断）", ""]
        for item in rejected_items[:10]:
            finding = item.get("finding") or {}
            lines.append(
                f"- {_location(finding)}：{_text(finding.get('title') or '未命名问题')}；"
                f"原因：{_text(item.get('reason') or '未提供原因')}"
            )
        if len(rejected_items) > 10:
            lines.append(f"- 其余 {len(rejected_items) - 10} 条请查看 JSON 报告。")
        lines.append("")
    dimensions = report.get("unreviewed_dimensions") or []
    if dimensions:
        lines += ["未审查维度：" + "、".join(_text(item) for item in dimensions), ""]
    if failures:
        lines += ["### 未完成的阶段", ""]
        for failure in failures:
            lines.append(
                f"- {_text(failure.get('agent') or 'unknown')}："
                f"{_text(failure.get('error') or 'unknown')}"
            )
        lines.append("")
    if status != "completed":
        reasons = []
        for event in (report.get("budget_events") or [])[:10]:
            reasons.append(
                f"- 取证预算：{_text(event.get('agent') or 'unknown')} / "
                f"{_text(event.get('reason') or event.get('status') or 'unknown')}"
            )
        for item in (report.get("tool_evidence") or []):
            if item.get("ok") is False and len(reasons) < 10:
                reasons.append(
                    f"- 工具调用：{_text(item.get('tool') or 'unknown')} / "
                    f"{_text(item.get('error') or 'unknown')}"
                )
        if report.get("source_errors"):
            reasons.append(f"- 读取源文件失败：{len(report['source_errors'])} 处")
        if report.get("planner_errors"):
            reasons.append(f"- 路由规划错误：{len(report['planner_errors'])} 处")
        if (report.get("coverage") or {}).get("omitted"):
            reasons.append("- 输入内容有省略，覆盖不完整")
        if report.get("verification_incomplete") and not uncertain:
            reasons.append("- Judge 复核未完整完成")
        if reasons:
            lines += ["### 其他未完成原因", "", *reasons, ""]
    lines += ["本报告基于静态代码和模型审查；Judge 的复核不代表动态执行了测试。", ""]
    return "\n".join(lines)
