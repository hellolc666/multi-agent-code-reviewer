import pytest

from reviewer.graph import planner
from reviewer.models.plan import ReviewPlan


def state(path, content="", **options):
    return {
        "changed_files": [{"path": path, "content": content, "change_type": "modified"}],
        "pr_diff": content,
        "review_options": options,
    }


@pytest.mark.parametrize(
    "path,content,security",
    [
        ("math.py", "def add(a,b):\n    return a+b", False),
        ("db.py", 'cursor.execute("SELECT * FROM users WHERE id=" + user)', True),
        ("auth.py", "return current_user.is_admin", True),
        ("store.py", "with transaction():\n    commit()", True),
    ],
)
async def test_dynamic_routing_risk_floor(path, content, security):
    result = await planner.plan_review_node(state(path, content))
    plan = ReviewPlan.model_validate(result["review_plan"])
    assert ("security" in plan.selected_agents) is security
    assert "correctness" in plan.selected_agents
    assert plan.routing_reasons["correctness"]


async def test_readme_and_tests_have_different_routes():
    readme = await planner.plan_review_node(state("README.md", "# docs"))
    tests = await planner.plan_review_node(state("tests/test_db.py", "def test_sql(): pass"))
    assert readme["agents_to_run"] == []
    assert set(tests["agents_to_run"]) == {"correctness_agent", "quality_agent"}


async def test_large_change_and_reviewer_budget():
    result = await planner.plan_review_node(state("logic.py", "x = 1\n" * 3000, max_reviewers=1))
    assert result["review_plan"]["risk"] == "medium"
    assert result["agents_to_run"] == ["correctness_agent"]
    assert result["review_plan"]["budget"]["omitted_agents"]


async def test_invalid_hybrid_output_falls_back_to_rules(monkeypatch):
    async def invalid(*_args, **_kwargs):
        return ReviewPlan(selected_agents=["shell_agent"])

    monkeypatch.setattr(planner, "analyze_with_schema", invalid)
    result = await planner.plan_review_node(state("auth.py", "", planner_mode="hybrid"))
    assert "security_agent" in result["agents_to_run"]
    assert result["planner_errors"][0]["fallback"] == "rules"


async def test_hybrid_cannot_remove_deterministic_security(monkeypatch):
    async def empty(*_args, **_kwargs):
        return ReviewPlan(selected_agents=[])

    monkeypatch.setattr(planner, "analyze_with_schema", empty)
    result = await planner.plan_review_node(state("auth.py", "", planner_mode="hybrid"))
    assert "security_agent" in result["agents_to_run"]


async def test_invalid_planner_mode_is_loud():
    with pytest.raises(ValueError):
        await planner.plan_review_node(state("a.py", "", planner_mode="bad"))
