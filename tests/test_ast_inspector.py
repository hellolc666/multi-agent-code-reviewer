"""Tests for ``reviewer.tools.ast_inspector`` — pure stdlib, sync, no I/O."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from reviewer.tools.ast_inspector import (
    ClassInfo,
    FunctionInfo,
    ReferenceInfo,
    cyclomatic_complexity,
    find_classes,
    find_functions,
    find_references,
    has_docstring,
    parse_module,
)

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "sample_repo"


# --------------------------------------------------------------------------- #
# has_docstring                                                               #
# --------------------------------------------------------------------------- #


def test_has_docstring_module_function_class():
    src = '''"""Module doc."""
def docked():
    """fn doc"""
    return 1

def undocked():
    return 2

class C:
    """class doc"""
    def m(self):
        return self

class D:
    pass
'''
    tree = parse_module(src)
    assert has_docstring(tree) is True
    fn_docked = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "docked"
    )
    fn_undocked = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "undocked"
    )
    cls_c = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "C")
    cls_d = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "D")
    assert has_docstring(fn_docked) is True
    assert has_docstring(fn_undocked) is False
    assert has_docstring(cls_c) is True
    assert has_docstring(cls_d) is False


def test_has_docstring_rejects_non_definition_nodes():
    src = "x = 1\n"
    tree = parse_module(src)
    assert has_docstring(tree.body[0]) is False  # ast.Assign


def test_has_docstring_empty_string_is_false():
    src = '''def f():
    """   """
'''
    fn = parse_module(src).body[0]
    assert has_docstring(fn) is False


# --------------------------------------------------------------------------- #
# cyclomatic_complexity                                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "source, expected",
    [
        ("def f():\n    return 1\n", 1),
        ("def f(x):\n    if x: return 1\n    return 0\n", 2),
        # if + elif = 2 decisions → complexity 3
        ("def f(x):\n    if x == 1: return 1\n    elif x == 2: return 2\n    return 3\n", 3),
        # for loop adds 1
        ("def f(xs):\n    for x in xs:\n        pass\n", 2),
        # while loop adds 1
        ("def f(x):\n    while x:\n        x -= 1\n", 2),
        # try/except handler adds 1
        ("def f():\n    try:\n        pass\n    except Exception:\n        pass\n", 2),
        # ternary adds 1
        ("def f(x):\n    return 1 if x else 0\n", 2),
        # bool-op: a and b and c → 2 extra
        ("def f(a, b, c):\n    return a and b and c\n", 3),
        # comprehension adds 1
        ("def f(xs):\n    return [x for x in xs]\n", 2),
        # match cases each add 1
        (
            "def f(x):\n    match x:\n        case 1:\n            return 1\n"
            "        case 2:\n            return 2\n        case _:\n            return 0\n",
            4,
        ),
        # with should NOT add complexity
        ("def f(p):\n    with open(p) as fh:\n        return fh.read()\n", 1),
    ],
)
def test_cyclomatic_complexity(source, expected):
    fn = parse_module(source).body[0]
    assert cyclomatic_complexity(fn) == expected


def test_cyclomatic_complexity_on_fixture_classify_user():
    """The fixture's ``classify_user`` is intentionally branchy (>= 5)."""
    src = (FIXTURE_REPO / "app" / "auth.py").read_text()
    fns = find_functions(src)
    classify = next(f for f in fns if f.name == "classify_user")
    assert classify.complexity >= 5


# --------------------------------------------------------------------------- #
# find_functions                                                              #
# --------------------------------------------------------------------------- #


def test_find_functions_top_level_and_methods():
    src = '''def alpha(a, b, *args, **kwargs):
    """alpha doc"""
    return a + b

async def beta():
    return 1

def _hidden():
    return 2

class C:
    def m(self, x):
        return x

    async def am(self):
        return self

    def _priv(self):
        return None

    def nested_excluded(self):
        def inner():
            return 1
        return inner
'''
    fns = find_functions(src)
    by_qual = {f.qualname: f for f in fns}

    # top-level + methods, but NOT the nested `inner`
    assert set(by_qual) == {
        "alpha",
        "beta",
        "_hidden",
        "C.m",
        "C.am",
        "C._priv",
        "C.nested_excluded",
    }
    assert "C.nested_excluded.inner" not in by_qual

    alpha = by_qual["alpha"]
    assert alpha.args == ("a", "b", "*args", "**kwargs")
    assert alpha.has_docstring is True
    assert alpha.is_public is True
    assert alpha.is_method is False
    assert alpha.is_async is False

    beta = by_qual["beta"]
    assert beta.is_async is True

    hidden = by_qual["_hidden"]
    assert hidden.is_public is False

    cm = by_qual["C.m"]
    assert cm.is_method is True
    assert cm.args == ("self", "x")
    cam = by_qual["C.am"]
    assert cam.is_async and cam.is_method


def test_find_functions_returns_dataclass_instances():
    fns = find_functions("def f():\n    pass\n")
    assert len(fns) == 1
    assert isinstance(fns[0], FunctionInfo)


def test_find_functions_invalid_python_raises():
    with pytest.raises(SyntaxError):
        find_functions("def f(:\n")


# --------------------------------------------------------------------------- #
# find_classes                                                                #
# --------------------------------------------------------------------------- #


def test_find_classes_collects_top_level_with_methods():
    src = '''class Public:
    """has doc"""
    def m(self):
        return 1

class _Private:
    pass
'''
    classes = find_classes(src)
    by_name = {c.name: c for c in classes}
    assert set(by_name) == {"Public", "_Private"}
    pub = by_name["Public"]
    assert isinstance(pub, ClassInfo)
    assert pub.has_docstring is True
    assert pub.is_public is True
    assert [m.qualname for m in pub.methods] == ["Public.m"]

    priv = by_name["_Private"]
    assert priv.is_public is False
    assert priv.has_docstring is False


def test_find_classes_on_fixture_session():
    src = (FIXTURE_REPO / "app" / "auth.py").read_text()
    classes = find_classes(src)
    session = next(c for c in classes if c.name == "Session")
    method_names = {m.name for m in session.methods}
    assert {"__init__", "refresh", "_internal"} <= method_names


# --------------------------------------------------------------------------- #
# find_references                                                             #
# --------------------------------------------------------------------------- #


def test_find_references_empty_targets_returns_empty():
    assert find_references("import x\nx.y()\n", []) == []
    assert find_references("import x\n", set()) == []


def test_find_references_import_from():
    src = "from app.auth import login\nlogin('a', 'b')\n"
    refs = find_references(src, {"login"})
    kinds = {r.kind for r in refs}
    assert "import_from" in kinds
    assert "call" in kinds
    # bare-Name reference should be suppressed because it's the call's func
    assert "name" not in kinds


def test_find_references_import_module_top_segment():
    src = "import app.auth\napp.auth.login('a')\n"
    refs = find_references(src, {"app", "login"})
    by_kind = {(r.name, r.kind) for r in refs}
    assert ("app", "import_module") in by_kind
    assert ("login", "call") in by_kind


def test_find_references_import_aliased():
    src = "import auth as a\na.login('x')\n"
    refs = find_references(src, {"auth"})
    # imported as alias; the original name "auth" still recorded as import_module
    assert any(r.name == "auth" and r.kind == "import_module" for r in refs)


def test_find_references_attribute_access_not_called():
    src = "from m import classify_user\nfn = classify_user\nfn.__name__\n"
    refs = find_references(src, {"classify_user"})
    kinds = {r.kind for r in refs}
    assert "import_from" in kinds
    assert "name" in kinds  # `fn = classify_user` — Name in Load


def test_find_references_string_literal_match():
    src = (
        'import unittest.mock as m\n'
        'm.patch("app.auth.login")\n'
    )
    refs = find_references(src, {"login"})
    assert any(r.kind == "string" and r.name == "login" for r in refs)


def test_find_references_string_word_boundary():
    """``"loginization"`` must NOT match target ``"login"``."""
    src = '"loginization is fine"\n'
    refs = find_references(src, {"login"})
    assert refs == []


def test_find_references_returns_dataclass_instances():
    refs = find_references("from m import x\nx()\n", {"x"})
    assert all(isinstance(r, ReferenceInfo) for r in refs)
    assert all(r.line >= 1 for r in refs)


def test_find_references_on_fixture_test_file():
    """The fixture test file imports + calls ``login`` from app.auth."""
    src = (FIXTURE_REPO / "tests" / "test_auth.py").read_text()
    refs = find_references(src, {"login", "Session"})
    by_kind_name = {(r.name, r.kind) for r in refs}
    assert ("login", "import_from") in by_kind_name
    assert ("login", "call") in by_kind_name
    # Session is not used in the fixture test
    assert not any(r.name == "Session" for r in refs)
