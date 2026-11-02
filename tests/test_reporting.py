"""Human report should preserve review status and separate verified from pending."""

from reviewer.reporting import render_markdown


def test_markdown_shows_confirmed_finding_and_incomplete_review():
    confirmed = {
        "id": "f1", "file_path": "shop/checkout.py", "line_start": 21,
        "title": "Exact stock rejected", "severity": "high", "category": "correctness",
        "explanation": "A purchase equal to available stock fails.",
        "trigger_condition": "stock=2, quantity=2",
        "suggestion": "Use a strict less-than check.",
    }
    pending = {"id": "f2", "file_path": "shop/cart.py", "line_start": 8,
               "title": "Possible lost quantity"}
    report = {
        "status": "partial", "execution_mode": "live", "input_mode": "diff",
        "files_changed": 2, "summary": "One confirmed issue.",
        "findings": [confirmed],
        "verified_findings": [
            {"status": "confirmed", "finding": confirmed, "reason": "Confirmed by changed code."},
            {"status": "uncertain", "finding": pending, "reason": "Model repair failed."},
        ],
        "agents_failed": [{"agent": "docs", "error": "StructuredOutputError"}],
    }
    rendered = render_markdown(report)
    assert rendered.startswith("# 代码审查报告\n")
    assert "审查未完整完成" in rendered
    assert "Exact stock rejected" in rendered
    assert "stock=2, quantity=2" in rendered
    assert "Possible lost quantity" in rendered
    assert "Model repair failed" in rendered
    assert "StructuredOutputError" in rendered
    assert "动态执行了测试" in rendered


def test_markdown_does_not_present_mock_or_unjudged_as_confirmed():
    report = {
        "status": "completed", "execution_mode": "mock", "judge_enabled": False,
        "summary": "No findings.", "findings": [{
            "id": "f1", "file_path": "a.py", "title": "Candidate",
            "explanation": "A possible issue.",
        }],
    }
    rendered = render_markdown(report)
    assert "离线 Mock" in rendered
    assert "未经独立复核" in rendered
    assert "## 审查发现" in rendered
    assert "## 已确认的问题" not in rendered


def test_completed_report_still_marks_uncertain_candidates():
    rendered = render_markdown({
        "status": "completed", "execution_mode": "live", "findings": [],
        "verified_findings": [{"status": "uncertain", "finding": {
            "id": "f1", "file_path": "a.py", "title": "Needs evidence"
        }, "reason": "Missing context"}],
    })
    assert "仍有未完成复核" in rendered
    assert "Needs evidence" in rendered


def test_rejected_candidates_show_safe_diagnostic_reasons():
    rendered = render_markdown({
        "status": "partial", "execution_mode": "live", "findings": [],
        "verified_findings": [{"status": "rejected", "finding": {
            "id": "f1", "file_path": "minishop/pricing.py", "line_start": 43,
            "title": "Coupon boundary",
        }, "reason": "evidence_snippet_not_at_anchor"}],
    })
    assert "已驳回的候选（诊断）" in rendered
    assert "minishop/pricing.py:43" in rendered
    assert r"evidence\_snippet\_not\_at\_anchor" in rendered


def test_report_discloses_actual_model_mix_for_evaluation():
    rendered = render_markdown({
        "status": "completed", "execution_mode": "live", "findings": [],
        "usage": {
            "models_used": ["qwen3-coder-flash-2025-07-28", "qwen3.5-27b"],
            "model_failovers": 1,
        },
    })
    assert "qwen3-coder-flash-2025-07-28" in rendered
    assert "qwen3.5-27b" in rendered
    assert "额度切换：** 1 次" in rendered


def test_zero_failovers_and_partial_budget_reason_are_visible():
    rendered = render_markdown({
        "status": "partial", "execution_mode": "live", "findings": [],
        "usage": {"models_used": ["qwen3.5-27b"], "model_failovers": 0},
        "budget_events": [{
            "agent": "correctness", "status": "budget_exhausted", "reason": "tool_limit",
        }],
    })
    assert "额度切换：** 0 次" in rendered
    assert "其他未完成原因" in rendered
    assert r"tool\_limit" in rendered


def test_duplicate_confirmations_are_reported_as_two_unique_issues():
    confirmed = {"id": "coupon", "title": "Coupon boundary", "file_path": "pricing.py"}
    duplicate = {"id": "quality-coupon", "title": "Coupon check", "file_path": "pricing.py"}
    rendered = render_markdown({
        "status": "completed", "execution_mode": "live", "findings": [confirmed],
        "verified_findings": [
            {"status": "confirmed", "finding": confirmed},
            {"status": "confirmed", "finding": duplicate, "duplicate_of": "coupon"},
        ],
    })
    assert "已确认 1 个独立问题（2 条确认候选，合并 1 条重复）" in rendered


def test_report_fields_are_plain_text_not_rendered_markup():
    rendered = render_markdown({
        "status": "completed", "execution_mode": "live", "summary": "<script>alert(1)</script>",
        "findings": [{"id": "f1", "title": "[click](https://bad.example)",
                      "file_path": "a.py", "explanation": "**urgent**"}],
    })
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "[click](https://bad.example)" not in rendered
    assert "**urgent**" not in rendered


def test_inline_code_remains_readable():
    rendered = render_markdown({
        "status": "completed", "execution_mode": "live", "summary": "Use `x // y` here.",
    })
    assert "Use `x // y` here." in rendered
