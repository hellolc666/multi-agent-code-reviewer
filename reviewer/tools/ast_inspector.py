"""Stdlib AST helpers for the Quality, Test Mapping, and Docs agents.

All helpers are synchronous (CPU-bound, sub-millisecond on typical files) and
return plain dataclasses so callers can pass them straight into LLM prompts or
serialize for state. ``ast.parse`` raises ``SyntaxError`` on invalid Python; we
let it propagate — callers should record the failure as an agent error.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class FunctionInfo:
    """A function or method definition surfaced from a module's AST."""

    name: str
    qualname: str  # "Class.method" for methods; "func" for top-level
    line_start: int
    line_end: int
    args: tuple[str, ...]
    has_docstring: bool
    is_public: bool  # not leading-underscore prefixed
    is_method: bool
    is_async: bool
    complexity: int  # McCabe cyclomatic complexity


@dataclass(frozen=True)
class ClassInfo:
    """A top-level class definition surfaced from a module's AST."""

    name: str
    line_start: int
    line_end: int
    has_docstring: bool
    is_public: bool
    methods: tuple[FunctionInfo, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ReferenceInfo:
    """A reference to a target name found inside a source file.

    ``kind`` is one of:
        - ``"import_from"`` : ``from m import name``
        - ``"import_module"``: ``import name`` / ``import name.sub``
        - ``"call"``        : ``name(...)`` or ``obj.name(...)``
        - ``"attribute"``   : ``obj.name`` (not the function of a Call)
        - ``"name"``        : bare ``Name`` Load reference (not a Call's func)
        - ``"string"``      : appears as a token inside a string literal
                              (e.g. ``mock.patch("module.name")``)
    """

    name: str
    line: int
    col: int
    kind: str


# --------------------------------------------------------------------------- #
# Module-level helpers                                                        #
# --------------------------------------------------------------------------- #


def parse_module(source: str) -> ast.Module:
    """Parse ``source`` into an ``ast.Module``. Raises ``SyntaxError`` on bad input."""
    return ast.parse(source)


def has_docstring(node: ast.AST) -> bool:
    """True if ``node`` is a definition node with a non-empty docstring."""
    if not isinstance(
        node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    ):
        return False
    doc = ast.get_docstring(node)
    return bool(doc and doc.strip())


def cyclomatic_complexity(node: ast.AST) -> int:
    """McCabe cyclomatic complexity: 1 + decision points within ``node``.

    Counts: ``if``/``elif``, ``for``, ``while``, ``except`` handlers,
    each ``and``/``or`` operand beyond the first, comprehensions, ``IfExp``
    (ternary), ``assert``, and each ``match`` case. Does **not** count
    ``with`` blocks (they are not branches).
    """
    count = 1
    for child in ast.walk(node):
        if isinstance(
            child,
            (
                ast.If,
                ast.For,
                ast.AsyncFor,
                ast.While,
                ast.ExceptHandler,
                ast.Assert,
                ast.IfExp,
                ast.comprehension,
                ast.match_case,
            ),
        ):
            count += 1
        elif isinstance(child, ast.BoolOp):
            count += max(0, len(child.values) - 1)
    return count


# --------------------------------------------------------------------------- #
# Function / class extraction                                                 #
# --------------------------------------------------------------------------- #


def _arg_names(func: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[str, ...]:
    args = func.args
    names = [a.arg for a in args.posonlyargs] + [a.arg for a in args.args]
    if args.vararg:
        names.append("*" + args.vararg.arg)
    names += [a.arg for a in args.kwonlyargs]
    if args.kwarg:
        names.append("**" + args.kwarg.arg)
    return tuple(names)


def _func_info(
    func: ast.FunctionDef | ast.AsyncFunctionDef, qualname: str, *, is_method: bool
) -> FunctionInfo:
    return FunctionInfo(
        name=func.name,
        qualname=qualname,
        line_start=func.lineno,
        line_end=getattr(func, "end_lineno", func.lineno),
        args=_arg_names(func),
        has_docstring=has_docstring(func),
        is_public=not func.name.startswith("_"),
        is_method=is_method,
        is_async=isinstance(func, ast.AsyncFunctionDef),
        complexity=cyclomatic_complexity(func),
    )


def find_functions(source: str | ast.Module) -> list[FunctionInfo]:
    """Top-level functions and one-level-deep class methods.

    Nested functions (closures) are intentionally omitted: they're rarely part
    of a module's public API and we don't want them in docs/test-mapping signal.
    """
    tree = parse_module(source) if isinstance(source, str) else source
    out: list[FunctionInfo] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append(_func_info(node, node.name, is_method=False))
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out.append(
                        _func_info(sub, f"{node.name}.{sub.name}", is_method=True)
                    )
    return out


def find_classes(source: str | ast.Module) -> list[ClassInfo]:
    """Top-level class definitions with their methods."""
    tree = parse_module(source) if isinstance(source, str) else source
    out: list[ClassInfo] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            methods = tuple(
                _func_info(sub, f"{node.name}.{sub.name}", is_method=True)
                for sub in node.body
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef))
            )
            out.append(
                ClassInfo(
                    name=node.name,
                    line_start=node.lineno,
                    line_end=getattr(node, "end_lineno", node.lineno),
                    has_docstring=has_docstring(node),
                    is_public=not node.name.startswith("_"),
                    methods=methods,
                )
            )
    return out


# --------------------------------------------------------------------------- #
# Reference extraction (used by Test Mapping agent at Step 5)                 #
# --------------------------------------------------------------------------- #


def _string_match(needle: str, haystack: str) -> bool:
    """Word/dotted-segment match: matches ``needle`` whole inside ``haystack``."""
    return re.search(rf"(^|[.\W]){re.escape(needle)}([.\W]|$)", haystack) is not None


def find_references(
    source: str | ast.Module, names: Iterable[str]
) -> list[ReferenceInfo]:
    """Find references to any of ``names`` inside ``source``.

    Used by the Test Mapping agent: given a set of changed entity names
    (functions / classes), return how each test file references them. The
    confidence heuristic in the agent then maps reference *kinds* to
    covered / partially_covered.
    """
    targets = {n for n in names if n}
    if not targets:
        return []

    tree = parse_module(source) if isinstance(source, str) else source

    # Pre-collect Call.func node ids so we don't double-emit (call + name/attribute).
    call_func_ids = {
        id(n.func)
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, (ast.Name, ast.Attribute))
    }

    out: list[ReferenceInfo] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                local = alias.asname or alias.name
                if alias.name in targets:
                    out.append(
                        ReferenceInfo(alias.name, node.lineno, node.col_offset, "import_from")
                    )
                elif local in targets:
                    out.append(
                        ReferenceInfo(local, node.lineno, node.col_offset, "import_from")
                    )

        elif isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                local = alias.asname or top
                if top in targets:
                    out.append(
                        ReferenceInfo(top, node.lineno, node.col_offset, "import_module")
                    )
                if local != top and local in targets:
                    out.append(
                        ReferenceInfo(local, node.lineno, node.col_offset, "import_module")
                    )

        elif isinstance(node, ast.Call):
            fn = node.func
            base = (
                fn.attr
                if isinstance(fn, ast.Attribute)
                else fn.id
                if isinstance(fn, ast.Name)
                else None
            )
            if base in targets:
                out.append(
                    ReferenceInfo(base, node.lineno, node.col_offset, "call")
                )

        elif isinstance(node, ast.Attribute):
            if id(node) in call_func_ids:
                continue
            if node.attr in targets:
                out.append(
                    ReferenceInfo(node.attr, node.lineno, node.col_offset, "attribute")
                )

        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if id(node) in call_func_ids:
                continue
            if node.id in targets:
                out.append(
                    ReferenceInfo(node.id, node.lineno, node.col_offset, "name")
                )

        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            for n in targets:
                if _string_match(n, node.value):
                    out.append(
                        ReferenceInfo(n, node.lineno, node.col_offset, "string")
                    )

    return out
