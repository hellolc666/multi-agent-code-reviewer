"""Bounded read-only text sandbox. No imports/evaluation of reviewed source."""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path, PurePosixPath, PureWindowsPath

BLOCKED = {".git", ".venv", "venv", "node_modules", "__pycache__", ".env"}


def safe_relative(path: str) -> str:
    if not isinstance(path, str) or not path or "\x00" in path or ":" in path:
        raise ValueError("invalid_path")
    value = path.replace("\\", "/")
    parts = PurePosixPath(value).parts
    if (
        PurePosixPath(value).is_absolute()
        or PureWindowsPath(path).is_absolute()
        or ".." in parts
        or any(p in BLOCKED or p.startswith(".env.") for p in parts)
    ):
        raise ValueError("path_denied")
    if not parts or parts[0].startswith("-"):
        raise ValueError("path_denied")
    return PurePosixPath(value).as_posix()


class CodeContext:
    def __init__(self, root: Path | str, files=(), *, max_output=8192, max_file=1048576):
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("repo_not_directory")
        self.files = files
        self.max_output = max_output
        self.max_file = max_file

    def resolve(self, path: str) -> Path:
        relative = safe_relative(path)
        current = self.root
        for part in PurePosixPath(relative).parts:
            current = current / part
            # Reject even in-root symlinks and Windows junctions. Review roots
            # must remain immutable while reviewing (documented TOCTOU limit).
            if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
                raise ValueError("symlink_denied")
        resolved = current.resolve()
        if not resolved.is_relative_to(self.root):
            raise ValueError("path_escape")
        return resolved

    def text(self, path: str) -> str:
        target = self.resolve(path)
        if not target.is_file():
            raise ValueError("file_missing")
        with target.open("rb") as stream:
            data = stream.read(self.max_file + 1)
        if len(data) > self.max_file:
            raise ValueError("file_too_large")
        if b"\x00" in data:
            raise ValueError("binary_file")
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("not_utf8") from None

    def read_file(self, path: str, start_line: int = 1, end_line: int = 100) -> dict:
        if not 1 <= start_line <= end_line or end_line - start_line >= 500:
            raise ValueError("invalid_line_range")
        lines = self.text(path).splitlines()
        selected = []
        size = 0
        for number in range(start_line, min(end_line, len(lines)) + 1):
            entry = {"line": number, "text": lines[number - 1]}
            size += len(entry["text"]) + 32
            if size > self.max_output:
                break
            selected.append(entry)
        return {
            "path": safe_relative(path),
            "lines": selected,
            "truncated": len(selected) < max(0, min(end_line, len(lines)) - start_line + 1),
        }

    def _sources(self):
        count = 0
        for directory, dirs, names in os.walk(self.root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in BLOCKED and not d.startswith("."))
            for name in sorted(names):
                if not name.endswith(".py"):
                    continue
                count += 1
                if count > 200:
                    return
                relative = (Path(directory) / name).relative_to(self.root).as_posix()
                try:
                    yield relative, self.text(relative)
                except (ValueError, OSError):
                    continue

    def search_symbol(self, symbol: str) -> dict:
        return self._search(symbol, definitions=True)

    def search_references(self, symbol: str) -> dict:
        return self._search(symbol, definitions=False)

    def _search(self, symbol: str, *, definitions: bool) -> dict:
        if not re.fullmatch(r"[A-Za-z_]\w{0,99}", symbol):
            raise ValueError("invalid_symbol")
        results = []
        for path, text in self._sources():
            try:
                nodes = ast.walk(ast.parse(text))
                for node in nodes:
                    found = (
                        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                        and node.name == symbol
                        if definitions
                        else isinstance(node, ast.Name)
                        and node.id == symbol
                        or isinstance(node, ast.Attribute)
                        and node.attr == symbol
                        or isinstance(node, ast.ImportFrom)
                        and any(a.name == symbol for a in node.names)
                    )
                    if found:
                        results.append(
                            {
                                "path": path,
                                "line": node.lineno,
                                "text": text.splitlines()[node.lineno - 1][:160],
                            }
                        )
                        if len(results) >= 20:
                            return {"matches": results, "limited": True}
            except (SyntaxError, RecursionError):
                continue
        return {"matches": results, "limited": True, "scope": "at most 200 Python files"}

    def get_changed_hunks(self, file_path: str) -> dict:
        path = safe_relative(file_path)
        for item in self.files:
            if item.path == path:
                result = []
                size = 0
                for hunk in item.hunks:
                    size += len(hunk.text)
                    if size > self.max_output:
                        break
                    result.append(hunk.model_dump(mode="json"))
                return {"path": path, "hunks": result, "truncated": len(result) < len(item.hunks)}
        raise ValueError("file_not_in_diff")

    def call(self, name: str, arguments: dict) -> dict:
        functions = {
            "read_file": self.read_file,
            "search_symbol": self.search_symbol,
            "search_references": self.search_references,
            "get_changed_hunks": self.get_changed_hunks,
        }
        if name not in functions:
            return {"ok": False, "error": "unknown_tool"}
        try:
            return {"ok": True, "result": functions[name](**arguments)}
        except (ValueError, TypeError, OSError, RecursionError) as exc:
            # OSError/TypeError can echo host paths/arguments. Expose only codes.
            code = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            return {"ok": False, "error": code}
