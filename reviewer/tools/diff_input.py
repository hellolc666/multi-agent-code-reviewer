"""Strict git unified-diff parser, preserving whole hunks and RIGHT line numbers."""

from __future__ import annotations

import hashlib
import re
import shlex
from pathlib import Path

from reviewer.models.inputs import DiffFile, DiffLine, Hunk, ReviewInput
from reviewer.tools.context import CodeContext, safe_relative

HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def parse_diff(diff: str) -> list[DiffFile]:
    files = []
    current = None
    lines = diff.splitlines(keepends=True)
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("diff --git "):
            if re.search(r"\\[0-7]{3}", line):
                raise ValueError("unsupported_git_path_encoding_use_core_quotepath_false")
            tokens = shlex.split(line.strip())
            if len(tokens) != 4 or not tokens[2].startswith("a/") or not tokens[3].startswith("b/"):
                raise ValueError("unsupported_git_path_encoding")
            old, path = safe_relative(tokens[2][2:]), safe_relative(tokens[3][2:])
            current = DiffFile(
                path=path, old_path=old, change_type="renamed" if old != path else "modified"
            )
            files.append(current)
        elif current is not None:
            if line.startswith("--- /dev/null"):
                current.change_type = "added"
            elif line.startswith("+++ /dev/null"):
                current.change_type = "removed"
            elif line.startswith("new file mode"):
                current.change_type = "added"
            elif line.startswith("deleted file mode"):
                current.change_type = "removed"
            elif line.startswith(("Binary files ", "GIT binary patch")):
                current.binary = True
            elif line.startswith("@@"):
                match = HEADER.match(line)
                if not match:
                    raise ValueError("invalid_hunk_header")
                old, old_count, new, new_count = map(
                    int, (match[1], match[2] or "1", match[3], match[4] or "1")
                )
                start = index
                consumed_old = consumed_new = 0
                entries = []
                index += 1
                while index < len(lines):
                    value = lines[index]
                    if value.startswith("\\ No newline at end of file"):
                        index += 1
                        continue
                    if consumed_old == old_count and consumed_new == new_count:
                        break
                    if not value or value[0] not in " +-":
                        raise ValueError("invalid_hunk_body")
                    kind = {"+": "add", "-": "remove", " ": "context"}[value[0]]
                    entries.append(
                        DiffLine(
                            kind=kind,
                            text=value[1:].rstrip("\r\n"),
                            new_line=None if kind == "remove" else new,
                            old_line=None if kind == "add" else old,
                        )
                    )
                    if kind != "remove":
                        new += 1
                        consumed_new += 1
                    if kind != "add":
                        old += 1
                        consumed_old += 1
                    if consumed_new > new_count or consumed_old > old_count:
                        raise ValueError("hunk_count_mismatch")
                    index += 1
                if (consumed_old, consumed_new) != (old_count, new_count):
                    raise ValueError("hunk_count_mismatch")
                current.hunks.append(
                    Hunk(header=line.strip(), text="".join(lines[start:index]), lines=entries)
                )
                continue
        elif line.strip():
            raise ValueError("expected_git_unified_diff")
        index += 1
    if len({f.path for f in files}) != len(files):
        raise ValueError("duplicate_diff_path")
    return files


def make_input(
    diff: str, *, mode: str, head_sha=None, max_chars=24000, fallback_files=()
) -> tuple[ReviewInput, str]:
    parsed = parse_diff(diff)
    # Provider file lists preserve binary/renamed/removed entries when a server
    # omits patches. They must never manufacture commentable lines.
    by_path = {f.path: f for f in parsed}
    for item in fallback_files:
        path = safe_relative(item["path"])
        if path not in by_path:
            by_path[path] = DiffFile(path=path, change_type=item.get("change_type", "modified"))
    parsed = list(by_path.values())
    chunks, included, omitted = [], [], []
    size = 0
    prioritized = sorted(
        parsed,
        key=lambda f: (
            not any(s in f.path.lower() for s in ("auth", "sql", "permission", "transaction")),
            f.path,
        ),
    )
    for file in prioritized:
        header = f"diff --git a/{file.old_path or file.path} b/{file.path}\n"
        for index, hunk in enumerate(file.hunks):
            record = {"path": file.path, "hunk": index, "header": hunk.header}
            block = header + hunk.text
            if size + len(block) <= max_chars:
                chunks.append(block)
                size += len(block)
                included.append(record)
            else:
                omitted.append({**record, "reason": "diff_character_budget"})
        if not file.hunks:
            omitted.append({"path": file.path, "reason": "binary_or_no_text_hunks"})
    value = ReviewInput(
        mode=mode,
        diff_sha256=hashlib.sha256(diff.encode("utf-8")).hexdigest(),
        head_sha=head_sha,
        files=parsed,
        coverage={
            "included_hunks": included,
            "omitted": omitted,
            "input_characters": len(diff),
            "review_characters": size,
        },
    )
    return value, "".join(chunks)


def changed_sources(value: ReviewInput, root: Path | str) -> tuple[list[dict], list[dict]]:
    context = CodeContext(root, value.files)
    sources, errors = [], []
    for file in value.files:
        content = ""
        if file.change_type != "removed" and not file.binary:
            try:
                content = context.text(file.path)
            except (ValueError, OSError) as exc:
                errors.append({"path": file.path, "error": type(exc).__name__})
        sources.append({"path": file.path, "content": content, "change_type": file.change_type})
    return sources, errors
