from unittest.mock import AsyncMock

import pytest

from evals.module_ablation import GROUPS, ROOT, construct
from evals.run import load_corpus, run_case
from reviewer.agents import quality, security
from reviewer.agents.base import FindingList


def test_multi_file_corpus_matches_frozen_and_labels_not_exposed():
    cases, checks, _ = construct()
    frozen, _ = load_corpus(ROOT / "data")
    assert cases == frozen
    assert len(cases) == 6
    assert sorted(len(c.gold_labels) for c in cases) == [0, 0, 2, 2, 2, 2]
    for case in cases:
        assert case.diff.count("diff --git") >= 3
        assert set(case.reviewer_input()) == {"repo_files", "diff"}
        assert len(checks[case.id]) == 3


@pytest.mark.parametrize("variant,expected", [
    ("D_no_judge", {"options"}), ("D_no_evidence", {"context_tools"}),
    ("D_no_routing", {"only"}), ("D_no_static", {"options"}),
])
def test_module_groups_change_only_one_factor(variant, expected):
    assert {k for k in GROUPS["D"] if GROUPS["D"][k] != GROUPS[variant][k]} == expected
    if "options" in expected:
        before, after = GROUPS["D"]["options"], GROUPS[variant]["options"]
        assert len({k for k in before.keys() | after.keys()
                    if before.get(k) != after.get(k)}) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("module,tool", [(quality, "run_ruff"), (security, "run_semgrep")])
@pytest.mark.parametrize("enabled", [True, False])
async def test_static_ablation_keeps_llm_review(monkeypatch, tmp_path, module, tool, enabled):
    scan = AsyncMock(return_value=[])
    llm = AsyncMock(return_value=FindingList(findings=[]))
    monkeypatch.setattr(module, tool, scan)
    monkeypatch.setattr(module, "analyze_with_schema", llm)
    if module is security:
        monkeypatch.setattr(module, "collect_evidence", AsyncMock(return_value=([], [])))
    result = await module._run({
        "repo_path": str(tmp_path), "pr_diff": "sample",
        "changed_files": [{"path": "a.py", "content": "def f(): return 1\n"}],
        "review_options": {"static_tools_enabled": enabled},
    })
    assert scan.await_count == int(enabled)
    assert llm.await_count == 1
    assert result["agents_succeeded"]


@pytest.mark.asyncio
async def test_custom_variant_options_reach_runner_without_gold(monkeypatch):
    case = construct()[0][0]
    async def inspect(patch, root, **kwargs):
        assert kwargs["options"]["static_tools_enabled"] is False
        assert kwargs["options"]["judge_enabled"] is True
        assert not (root / "probes.json").exists()
        assert set(p.name for p in root.iterdir()).isdisjoint({"labels_simulated.json"})
        return {"status": "completed"}
    monkeypatch.setattr("evals.run.run_review_diff", inspect)
    await run_case(case, "D_no_static", False, {}, variants=GROUPS)
