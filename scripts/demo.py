"""Three scripted mock reviews through the production local-diff runner.

Expected findings are pre-programmed solely for workflow demonstration, never
evidence of model detection accuracy. No network, key or reviewed code runs.
"""

# ruff: noqa: E501 -- literal demo source lines are kept intact

import argparse
import asyncio
import difflib
import json
from pathlib import Path

from reviewer.reliability.harness import current_agent
from reviewer.runner import run_review_diff

CASES = {
    "clean": (
        "bounds.py",
        "def get(items, index):\n    if index < 0 or index > len(items):\n        return None\n    return items[index]\n",
        "def get(items, index):\n    if index < 0 or index >= len(items):\n        return None\n    return items[index]\n",
        None,
    ),
    "logic": (
        "pages.py",
        "def pages(total, size):\n    return (total + size - 1) // size\n",
        "def pages(total, size):\n    return total // size\n",
        {
            "agent": "correctness",
            "category": "correctness",
            "severity": "medium",
            "title": "Partial final page is lost",
            "trigger_condition": "total=11, size=10",
            "explanation": "Integer division returns one page, leaving the last item unreachable.",
            "suggestion": "Restore rounding up.",
            "evidence_snippet": "    return total // size",
        },
    ),
    "security": (
        "users.py",
        "def find(db, name):\n    return db.execute('SELECT id FROM users WHERE name = ?', (name,))\n",
        "def find(db, name):\n    return db.execute(f\"SELECT id FROM users WHERE name = '{name}'\")\n",
        {
            "agent": "security",
            "category": "security",
            "severity": "high",
            "title": "Untrusted name is interpolated into SQL",
            "trigger_condition": "name is a request value containing a SQL quote",
            "explanation": "Input becomes SQL syntax and can bypass the intended filter.",
            "suggestion": "Use a bound parameter.",
            "evidence_snippet": "    return db.execute(f\"SELECT id FROM users WHERE name = '{name}'\")",
        },
    ),
}


class ScriptedDemoModel:
    metadata = {"reviewer_provider": "scripted_mock"}

    def __init__(self, target):
        self.target = target

    def with_structured_output(self, schema):
        target = self.target

        class Response:
            async def ainvoke(self, _messages):
                name = schema.__name__
                if name == "FindingList":
                    if not target or current_agent.get() != target["agent"]:
                        return schema.model_validate({"findings": []})
                    return schema.model_validate(
                        {
                            "findings": [
                                {
                                    **target,
                                    "id": "scripted-demo-candidate",
                                    "file_path": target["file_path"],
                                    "line_start": target["line_start"],
                                    "confidence": 0.95,
                                }
                            ]
                        }
                    )
                if name == "EvidenceTurn":
                    return schema.model_validate({"requests": []})
                if name == "JudgeDecision":
                    return schema.model_validate(
                        {
                            "status": "confirmed",
                            "reason": "Scripted demonstration checks the supplied trigger and exact changed line.",
                            "reproducible_trigger": target["trigger_condition"],
                            "evidence_support": True,
                        }
                    )
                if name == "_SummaryResponse":
                    return schema.model_validate({"summary": "Scripted mock workflow completed."})
                return schema.model_validate({})

        return Response()


async def run_demo(name, output):
    if name not in CASES:
        raise ValueError("unknown demo")
    output = Path(output)
    if output.exists():
        raise ValueError("output already exists")
    file_name, old, new, target = CASES[name]
    root = output.parent / ("demo-" + name + "-repo")
    if root.exists():
        raise ValueError("demo repository already exists")
    root.mkdir(parents=True)
    (root / file_name).write_text(new, encoding="utf-8")
    diff = (
        "diff --git a/"
        + file_name
        + " b/"
        + file_name
        + "\n"
        + "".join(
            difflib.unified_diff(
                old.splitlines(True),
                new.splitlines(True),
                fromfile="a/" + file_name,
                tofile="b/" + file_name,
            )
        )
    )
    patch = output.with_suffix(".diff")
    if patch.exists():
        raise ValueError("demo patch already exists")
    patch.write_text(diff, encoding="utf-8")
    if target:
        target = {
            **target,
            "file_path": file_name,
            "line_start": new.splitlines().index(target["evidence_snippet"]) + 1,
        }
    report = await run_review_diff(patch, root, live=False, model_client=ScriptedDemoModel(target))
    report["demo_model"] = "scripted_mock; findings were programmed, not discovered"
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", choices=CASES)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = asyncio.run(run_demo(args.name, args.output))
    print(
        json.dumps(
            {
                "status": result["status"],
                "confirmed_findings": len(result["findings"]),
                "model": result["demo_model"],
            }
        )
    )
