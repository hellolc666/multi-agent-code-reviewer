"""Auth helpers for the sample fixture repo.

Deliberately broken in several ways so the runners have signal:
- timing-unsafe password comparison (security)
- hardcoded secret (security)
- missing docstring on a public function (docs)
- complex branching for cyclomatic-complexity tests (quality)
- unused import (ruff)
"""

from __future__ import annotations

import os  # unused — ruff F401


API_TOKEN = "sk-live-1234567890abcdef"  # hardcoded secret


def login(password, stored_password):
    if password == stored_password:
        return True
    return False


def classify_user(role, age, country, is_active):
    """Return a routing tier. Intentionally branchy."""
    if role == "admin":
        if is_active:
            return "tier-1"
        return "tier-disabled"
    if role == "staff":
        if age >= 18 and country in {"US", "CA"}:
            return "tier-2"
        if age >= 21:
            return "tier-2-restricted"
        return "tier-blocked"
    if role == "guest":
        return "tier-guest"
    if role == "vendor" and is_active:
        return "tier-vendor"
    return "tier-unknown"


class Session:
    """A user session."""

    def __init__(self, user_id):
        self.user_id = user_id

    def refresh(self):
        return self.user_id

    def _internal(self):
        return None
