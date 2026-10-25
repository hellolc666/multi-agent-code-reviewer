"""Authoring source for 20 distinct cases. Freeze before any final evaluation.

These are original compact fixtures, not copied public PRs or human-signed labels.
Never call this from the evaluator: changing the corpus requires a new freeze.
"""

# ruff: noqa: E501 -- source fixtures preserve literal line content

import difflib
import hashlib
import json
from pathlib import Path

from evals.schema import Case


def build():
    cases = []

    def add(
        name,
        category,
        path,
        before,
        after,
        anchor=None,
        title="",
        trigger="",
        defect="",
        extra=None,
        note="",
        severity="medium",
    ):
        diff = f"diff --git a/{path} b/{path}\n" + "".join(
            difflib.unified_diff(
                before.splitlines(True),
                after.splitlines(True),
                fromfile="a/" + path,
                tofile="b/" + path,
            )
        )
        gold = (
            []
            if anchor is None
            else [
                {
                    "id": name + "_g1",
                    "file_path": path,
                    "line": after.splitlines().index(anchor) + 1,
                    "evidence": anchor,
                    "title": title,
                    "trigger": trigger,
                    "defect": defect,
                    "category": category,
                    "severity": severity,
                }
            ]
        )
        case = Case(
            id=name,
            split="dev" if len(cases) % 2 == 0 else "test",
            source="Original synthetic fixture authored for this project, MIT, 2026-10-24",
            annotation_status="curated_pending_human_signoff",
            reviewed_by="Codex static inspection",
            review_note=note
            or "Trace the stated input through the changed statement; independent human signoff pending.",
            repo_files={path: after, **(extra or {})},
            diff=diff,
            gold_labels=gold,
        )
        cases.append(case)

    add(
        "logic_last_page",
        "logic",
        "pagination.py",
        "def pages(total, size):\n    return (total + size - 1) // size\n",
        "def pages(total, size):\n    return total // size\n",
        "    return total // size",
        "Partial final page is lost",
        "total=11, size=10",
        "Returns 1 page instead of 2, so the last item is unreachable.",
    )
    add(
        "logic_empty_average",
        "logic",
        "stats.py",
        "def mean(values):\n    return sum(values) / len(values) if values else None\n",
        "def mean(values):\n    return sum(values) / len(values)\n",
        "    return sum(values) / len(values)",
        "Empty input now divides by zero",
        "mean([])",
        "Removed empty guard raises ZeroDivisionError instead of returning None.",
    )
    add(
        "logic_cents_contract",
        "logic",
        "invoice.py",
        "from payments import charge_cents\ndef invoice(amount_dollars):\n    return charge_cents(round(amount_dollars * 100))\n",
        "from payments import charge_cents\ndef invoice(amount_dollars):\n    return charge_cents(round(amount_dollars))\n",
        "    return charge_cents(round(amount_dollars))",
        "Payment crosses unit boundary incorrectly",
        "invoice(12.50)",
        "charges 12 cents instead of 1250 cents; callee contract is cents.",
        extra={
            "payments.py": 'def charge_cents(amount_cents):\n    """Charge integer minor currency units."""\n    return {\'charged_cents\': amount_cents}\n'
        },
    )
    add(
        "logic_retry_exit",
        "logic",
        "retry.py",
        "def send(call):\n    for attempt in range(3):\n        try:\n            return call()\n        except TimeoutError:\n            if attempt == 2:\n                raise\n",
        "def send(call):\n    for attempt in range(3):\n        try:\n            return call()\n        except TimeoutError:\n            return None\n",
        "            return None",
        "First timeout suppresses retry and failure",
        "call times out once then would succeed",
        "Returns None on first timeout without retrying.",
    )
    add(
        "logic_cache_falsy",
        "logic",
        "cache.py",
        "def lookup(cache, key, load):\n    if key in cache:\n        return cache[key]\n    cache[key] = load(key)\n    return cache[key]\n",
        "def lookup(cache, key, load):\n    if cache.get(key):\n        return cache[key]\n    cache[key] = load(key)\n    return cache[key]\n",
        "    if cache.get(key):",
        "Cached zero incorrectly triggers reload",
        "cache={'balance': 0}; load is a metered remote call",
        "Falsy cached values now trigger repeated remote calls.",
    )
    add(
        "logic_generator_consumed",
        "logic",
        "pipeline.py",
        "def validate_then_save(items, validate, save):\n    items = list(items)\n    if all(validate(x) for x in items):\n        save(items)\n",
        "def validate_then_save(items, validate, save):\n    valid = all(validate(x) for x in items)\n    if valid:\n        save(items)\n",
        "    valid = all(validate(x) for x in items)",
        "Validation exhausts one-shot input",
        "items=iter([1,2]); validate always True",
        "save receives exhausted iterator and silently stores no items.",
    )
    add(
        "logic_transaction_return",
        "logic",
        "ledger.py",
        "def debit(db, account, amount):\n    db.begin()\n    db.debit(account, amount)\n    db.commit()\n    return True\n",
        "def debit(db, account, amount):\n    db.begin()\n    db.debit(account, amount)\n    return True\n    db.commit()\n",
        "    return True",
        "Commit became unreachable",
        "successful db.debit call in explicit transaction",
        "Function reports success before commit; closing connection rolls the debit back.",
        severity="high",
    )
    add(
        "logic_alias_defaults",
        "logic",
        "options.py",
        "DEFAULT = {'retries': 3}\ndef options(overrides):\n    result = DEFAULT.copy()\n    result.update(overrides)\n    return result\n",
        "DEFAULT = {'retries': 3}\ndef options(overrides):\n    result = DEFAULT\n    result.update(overrides)\n    return result\n",
        "    result = DEFAULT",
        "Request overrides mutate shared defaults",
        "options({'retries': 0}) followed by options({})",
        "Second request inherits first request's overrides.",
    )
    add(
        "security_sql_filter",
        "security",
        "users.py",
        "def find_user(db, username):\n    return db.execute('SELECT id FROM users WHERE name = ?', (username,)).fetchone()\n",
        "def find_user(db, username):\n    return db.execute(f\"SELECT id FROM users WHERE name = '{username}'\").fetchone()\n",
        "    return db.execute(f\"SELECT id FROM users WHERE name = '{username}'\").fetchone()",
        "User name is interpolated into SQL",
        "username=\"' OR '1'='1\" from request input",
        "Input becomes SQL syntax and bypasses the intended equality filter.",
        severity="high",
    )
    add(
        "security_object_owner",
        "security",
        "api.py",
        "def get_order(store, order_id, user):\n    order = store.get(order_id)\n    if order.owner_id != user.id:\n        raise PermissionError()\n    return order\n",
        "def get_order(store, order_id, user):\n    return store.get(order_id)\n",
        "    return store.get(order_id)",
        "Order read loses ownership authorization",
        "authenticated user requests another user's order ID",
        "Any caller can read another owner's order.",
        severity="high",
    )
    add(
        "security_archive_path",
        "security",
        "extract.py",
        "from pathlib import Path\ndef extract(root, member, data):\n    target = (Path(root) / member).resolve()\n    if not target.is_relative_to(Path(root).resolve()):\n        raise ValueError('outside root')\n    target.write_bytes(data)\n",
        "from pathlib import Path\ndef extract(root, member, data):\n    target = Path(root) / member\n    target.write_bytes(data)\n",
        "    target = Path(root) / member",
        "Archive member escapes extraction root",
        "member='../outside.txt' from untrusted archive",
        "Removed containment check allows writes above root.",
        severity="high",
    )
    add(
        "security_tls_verify",
        "security",
        "network.py",
        "def fetch(client, url):\n    return client.get(url, verify=True)\n",
        "def fetch(client, url):\n    return client.get(url, verify=False)\n",
        "    return client.get(url, verify=False)",
        "TLS certificate verification disabled",
        "request traverses a hostile network with a forged TLS certificate",
        "Client accepts unauthenticated server certificate.",
        severity="high",
    )
    add(
        "regression_truthy_assert",
        "regression",
        "tests/test_total.py",
        "def test_total():\n    from total import total\n    assert total([2, 3]) == 5\n",
        "def test_total():\n    from total import total\n    assert total([2, 3])\n",
        "    assert total([2, 3])",
        "Exact result assertion weakened to truthiness",
        "implementation regresses to return 1 for any input",
        "Changed test passes for incorrect nonzero totals.",
        extra={"total.py": "def total(values):\n    return sum(values)\n"},
    )
    add(
        "regression_exception_swallow",
        "regression",
        "tests/test_divide.py",
        "import pytest\ndef test_zero():\n    from divide import divide\n    with pytest.raises(ZeroDivisionError):\n        divide(1, 0)\n",
        "def test_zero():\n    from divide import divide\n    try:\n        divide(1, 0)\n    except ZeroDivisionError:\n        pass\n",
        "    try:",
        "Exception test no longer requires an exception",
        "divide incorrectly returns 0 instead of raising",
        "The try/except also passes if divide never raises.",
        extra={"divide.py": "def divide(a, b):\n    return a / b\n"},
    )
    add(
        "regression_deselected_test",
        "regression",
        "tests/test_permissions.py",
        "import pytest\ndef test_denies_guest():\n    from access import require_admin\n    with pytest.raises(PermissionError):\n        require_admin('guest')\n",
        "import pytest\ndef denies_guest():\n    from access import require_admin\n    with pytest.raises(PermissionError):\n        require_admin('guest')\n",
        "def denies_guest():",
        "Authorization regression test is no longer collected",
        "pytest default python_functions=test_*",
        "Renaming removes test prefix and silently drops this security assertion.",
        extra={
            "access.py": "def require_admin(role):\n    if role != 'admin':\n        raise PermissionError()\n"
        },
    )
    add(
        "regression_shared_expected",
        "regression",
        "tests/test_sort.py",
        "def test_sort():\n    from sorter import sort_values\n    values = [3, 1, 2]\n    actual = sort_values(values)\n    assert actual == [1, 2, 3]\n",
        "def test_sort():\n    from sorter import sort_values\n    values = [3, 1, 2]\n    actual = sort_values(values)\n    assert actual == values\n",
        "    assert actual == values",
        "Expected sorting result aliases mutated input",
        "sort_values regresses to return input unchanged",
        "Test compares returned input to itself, so unsorted output passes.",
        extra={"sorter.py": "def sort_values(values):\n    values.sort()\n    return values\n"},
    )
    add(
        "clean_parameter_sql",
        "clean",
        "query.py",
        "def get(db, name):\n    return db.execute('SELECT id FROM users WHERE name = ?', (name,))\n",
        "def get(db, name):\n    query = 'SELECT id FROM users WHERE name = ?'\n    return db.execute(query, (name,))\n",
        note="Query text is constant and values remain parameterized. Checked single-element tuple and unchanged return value; SQL keyword alone is not a defect.",
    )
    add(
        "clean_path_guard",
        "clean",
        "paths.py",
        "from pathlib import Path\ndef read(root, name):\n    return (Path(root) / name).read_text(encoding='utf-8')\n",
        "from pathlib import Path\ndef read(root, name):\n    base = Path(root).resolve()\n    target = (base / name).resolve()\n    if not target.is_relative_to(base):\n        raise ValueError('outside root')\n    return target.read_text(encoding='utf-8')\n",
        note="Under immutable local fixture filesystem, resolve+containment rejects parent/absolute/symlink escapes; authorized in-root UTF-8 reads unchanged. Concurrent hostile filesystem mutation is out of this case's threat model.",
    )
    add(
        "clean_boundary_fix",
        "clean",
        "bounds.py",
        "def get(items, index):\n    if index < 0 or index > len(items):\n        return None\n    return items[index]\n",
        "def get(items, index):\n    if index < 0 or index >= len(items):\n        return None\n    return items[index]\n",
        note="Fix checks index equal to length and empty list, preserves valid integer indices and rejects negatives. Existing invalid noninteger type behavior is unchanged.",
    )
    add(
        "clean_readme",
        "clean",
        "README.md",
        "# Example\nUse Python 3.11.\n",
        "# Example\nUse Python 3.11 or 3.12.\nRun the CLI with --help.\n",
        note="Documentation-only supported-version wording; no executable behavior or unsupported performance claims. Reviewer may skip this input; skipped is distinct from reviewed-clean.",
    )
    return cases


if __name__ == "__main__":
    root = Path(__file__).parent / "data"
    root.mkdir(exist_ok=True)
    corpus = "".join(c.model_dump_json() + "\n" for c in build()).encode("utf-8")
    (root / "cases.jsonl").write_bytes(corpus)
    (root / "schema.json").write_text(
        json.dumps(Case.model_json_schema(), indent=2), encoding="utf-8"
    )
    manifest = {
        "version": "v1-20",
        "sha256": hashlib.sha256(corpus).hexdigest(),
        "cases": 20,
        "dev": 10,
        "test": 10,
        "seed": "fixed alternating authored order",
        "annotation_status": "curated_pending_human_signoff",
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
