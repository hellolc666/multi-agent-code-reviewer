"""Misc utilities for the sample fixture repo."""

from __future__ import annotations


def slugify(text: str) -> str:
    """Lowercase and dash-join a string."""
    return "-".join(text.lower().split())


def reverse(text):
    return text[::-1]


def _normalize(text):
    return text.strip()
