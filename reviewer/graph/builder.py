"""LangGraph workflow assembly.

Wires the seven nodes:

    START → fetch_pr → plan_review →┐
                                    ├─→ Send(security_agent)  ─┐
                                    ├─→ Send(quality_agent)    ├─→ aggregate → END
                                    ├─→ Send(test_mapping)     ┘
                                    └─→ Send(docs_agent)

``dispatch_specialists`` is a pure router that returns a list of ``Send``
objects pointing at agents named in ``state["agents_to_run"]``. Node names
must match ``add_node`` keys exactly.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from reviewer.agents.correctness import correctness_agent_node
from reviewer.agents.docs import docs_agent_node
from reviewer.agents.quality import quality_agent_node
from reviewer.agents.security import security_agent_node
from reviewer.agents.test_mapping import test_mapping_agent_node
from reviewer.graph.aggregator import aggregate_findings_node
from reviewer.graph.planner import plan_review_node
from reviewer.graph.state import AGENT_NODES, ReviewState


async def fetch_pr_node(state: ReviewState) -> dict[str, Any]:
    """Default fetch node — passes through assumed pre-populated state.

    The CLI / API entrypoints normally call ``reviewer.tools.github_client.fetch_pr``
    *outside* the graph and seed ``ReviewState`` directly with the PR bundle (so
    the cloned tempdir is owned by the entrypoint, not the graph). This node is
    a no-op placeholder that future versions could replace with in-graph fetching.
    """
    return {}


def dispatch_specialists(state: ReviewState) -> list[Send]:
    """Pure router. Returns one ``Send`` per agent in ``state["agents_to_run"]``.

    Must not mutate state. Unknown agent names are silently dropped (defensive
    against typos in custom planners).

    When the planner produced an empty list (e.g. a PR with no Python files in
    v1), we fall through directly to the aggregate node so the graph still
    produces a structured ``ReviewReport`` instead of dead-ending and leaving
    ``final_report`` unset. The aggregator detects this state and writes a
    clear summary explaining that no agents were applicable.
    """
    sends = [
        Send(name, state) for name in state.get("agents_to_run", []) if name in AGENT_NODES
    ]
    if not sends:
        return [Send("aggregate", state)]
    return sends


def build_review_graph(*, fetch_node=None, aggregate_node=None, planner_node=None):
    """Compile the LangGraph workflow.

    Parameters
    ----------
    fetch_node, aggregate_node, planner_node:
        Optional overrides. ``planner_node`` is used by the CLI's ``--only``
        flag to force a specific agent set without the rule-based planner
        expanding/narrowing the user's choice.
    """
    graph = StateGraph(ReviewState)

    graph.add_node("fetch_pr", fetch_node or fetch_pr_node)
    graph.add_node("plan_review", planner_node or plan_review_node)
    graph.add_node("security_agent", security_agent_node)
    graph.add_node("quality_agent", quality_agent_node)
    graph.add_node("test_mapping_agent", test_mapping_agent_node)
    graph.add_node("docs_agent", docs_agent_node)
    graph.add_node("correctness_agent", correctness_agent_node)
    graph.add_node("aggregate", aggregate_node or aggregate_findings_node)

    graph.add_edge(START, "fetch_pr")
    graph.add_edge("fetch_pr", "plan_review")
    graph.add_conditional_edges(
        "plan_review", dispatch_specialists, [*AGENT_NODES, "aggregate"]
    )
    for agent in AGENT_NODES:
        graph.add_edge(agent, "aggregate")
    graph.add_edge("aggregate", END)

    return graph.compile()
