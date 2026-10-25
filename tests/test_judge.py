import pytest

from reviewer.agents import judge
from reviewer.agents.base import StructuredOutputError
from reviewer.models.findings import Finding
from reviewer.models.verification import JudgeDecision
from reviewer.tools.diff_input import make_input

PATCH = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,2 +1,2 @@
 def answer():
-    return 1
+    return helper()
"""


@pytest.fixture
def review(tmp_path):
    (tmp_path / "app.py").write_text("def answer():\n    return helper()\n", encoding="utf-8")
    value, diff = make_input(PATCH, mode="diff")
    finding = Finding(
        id="F1",
        agent="correctness",
        category="correctness",
        severity="high",
        file_path="app.py",
        line_start=2,
        line_end=2,
        title="Undefined helper",
        explanation="helper is not defined/imported",
        suggestion="import helper",
        confidence=0.9,
        trigger_condition="call answer()",
        evidence_snippet="return helper()",
    )
    return {
        "repo_path": str(tmp_path),
        "review_input": value.model_dump(),
        "pr_diff": diff,
        "findings": [finding],
    }


@pytest.fixture
def confirmed(monkeypatch):
    async def decide(prompt, _schema, **_kwargs):
        assert "gold_labels" not in prompt
        return JudgeDecision(
            status="confirmed",
            reason="Undefined global raises NameError",
            reproducible_trigger="Call answer()",
            evidence_support=True,
        )

    monkeypatch.setattr(judge, "analyze_with_schema", decide)


async def test_valid_evidence_is_confirmed_and_raw_preserved(review, confirmed):
    review["gold_labels"] = {"secret_evaluation_answer": "never include in prompt"}
    result = await judge.judge_findings_node(review)
    record = result["verified_findings"][0]
    assert record["status"] == "confirmed"
    assert record["can_comment"]
    assert record["finding"]["verification_status"] == "confirmed"
    assert result["raw_findings"][0].verification_status == "candidate"


@pytest.mark.parametrize(
    "update,reason",
    [
        ({"file_path": "fake.py"}, "file_not_in_diff"),
        ({"file_path": "../host.py"}, "invalid_path"),
        ({"line_start": 999, "line_end": 999}, "line_outside_changed_hunks"),
        ({"line_start": 0, "line_end": 0}, "invalid_line_range"),
        ({"evidence_snippet": "imaginary code"}, "evidence_snippet_not_at_anchor"),
    ],
)
async def test_forged_evidence_is_rejected_without_llm(review, monkeypatch, update, reason):
    async def forbidden(*_args, **_kwargs):
        pytest.fail("deterministically invalid findings must not call Judge LLM")

    monkeypatch.setattr(judge, "analyze_with_schema", forbidden)
    review["findings"] = [review["findings"][0].model_copy(update=update)]
    record = (await judge.judge_findings_node(review))["verified_findings"][0]
    assert record["status"] == "rejected"
    assert record["reason"] == reason


async def test_duplicate_defect_merges_agents(review, confirmed):
    review["findings"].append(review["findings"][0].model_copy(update={"agent": "security"}))
    result = await judge.judge_findings_node(review)
    assert len(result["raw_findings"]) == 2
    assert len(result["verified_findings"]) == 1
    assert len(result["verified_findings"][0]["source_agents"]) == 2


async def test_judge_outage_never_confirms(review, monkeypatch):
    async def unavailable(*_args, **_kwargs):
        raise TimeoutError()

    monkeypatch.setattr(judge, "analyze_with_schema", unavailable)
    result = await judge.judge_findings_node(review)
    assert result["verification_incomplete"]
    assert result["verified_findings"][0]["status"] == "uncertain"
    assert len(result["raw_findings"]) == 1


async def test_judge_structured_failure_preserves_partial_record(review, monkeypatch):
    async def invalid(*_args, **_kwargs):
        raise StructuredOutputError("JudgeDecision: invalid output; repair budget exhausted")

    monkeypatch.setattr(judge, "analyze_with_schema", invalid)
    result = await judge.judge_findings_node(review)
    record = result["verified_findings"][0]
    assert result["verification_incomplete"]
    assert record["status"] == "uncertain"
    assert record["verification_incomplete"]
    assert record["reason"] == "judge_error:StructuredOutputError"
    assert not record["can_comment"]
    assert len(result["raw_findings"]) == 1


async def test_judge_prompt_describes_decision_contract(review, monkeypatch):
    async def decide(_prompt, schema, *, system):
        assert schema is JudgeDecision
        for field in (
            "confirmed, rejected, or uncertain",
            "reproducible_trigger",
            "evidence_support",
            "style_only",
            "concrete reproducible condition",
        ):
            assert field in system
        return JudgeDecision(status="uncertain", reason="Insufficient supporting context.")

    monkeypatch.setattr(judge, "analyze_with_schema", decide)
    record = (await judge.judge_findings_node(review))["verified_findings"][0]
    assert record["status"] == "uncertain"
    assert not record["verification_incomplete"]


async def test_missing_snippet_and_low_confidence_are_uncertain(review):
    original = review["findings"][0]
    for update in ({"evidence_snippet": None}, {"confidence": 0.2}):
        review["findings"] = [original.model_copy(update=update)]
        assert (await judge.judge_findings_node(review))["verified_findings"][0][
            "status"
        ] == "uncertain"


async def test_context_anchor_is_not_inline_comment(review, confirmed):
    review["findings"] = [
        review["findings"][0].model_copy(
            update={
                "line_start": 1,
                "line_end": 1,
                "evidence_snippet": "def answer():",
            }
        )
    ]
    record = (await judge.judge_findings_node(review))["verified_findings"][0]
    assert record["status"] == "confirmed"
    assert not record["can_comment"]


async def test_judge_disabled_retains_unverified_candidates(review):
    review["review_options"] = {"judge_enabled": False}
    result = await judge.judge_findings_node(review)
    assert result["verified_findings"][0]["reason"] == "judge_disabled"
    assert result["verified_findings"][0]["status"] == "uncertain"


async def test_clean_diff_does_not_call_judge_llm(review, monkeypatch):
    async def forbidden(*_args, **_kwargs):
        pytest.fail("clean review has no verification requests")

    monkeypatch.setattr(judge, "analyze_with_schema", forbidden)
    review["findings"] = []
    result = await judge.judge_findings_node(review)
    assert result["verified_findings"] == []
    assert not result["verification_incomplete"]


async def test_false_rejection_is_observable_and_conflicts_are_retained(review, monkeypatch):
    choices = iter(["confirmed", "rejected"])

    async def decide(*_args, **_kwargs):
        status = next(choices)
        return JudgeDecision(
            status=status,
            reason="Scripted decision for observability test",
            evidence_support=True,
            reproducible_trigger="call answer()",
        )

    monkeypatch.setattr(judge, "analyze_with_schema", decide)
    review["findings"].append(
        review["findings"][0].model_copy(update={"title": "NameError at call"})
    )
    result = await judge.judge_findings_node(review)
    assert len(result["raw_findings"]) == 2
    assert {r["status"] for r in result["verified_findings"]} == {"confirmed", "rejected"}
    assert result["verification_conflicts"]


def test_confirmation_requires_trigger_and_evidence():
    with pytest.raises(ValueError):
        JudgeDecision(status="confirmed", reason="unsupported assertion")
