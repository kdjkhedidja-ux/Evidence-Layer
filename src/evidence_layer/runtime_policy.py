"""Fail-closed runtime gates for development-only behavior."""

from __future__ import annotations

import os

RUNTIME_MODE_ENV = "EVIDENCE_LAYER_RUNTIME_MODE"
SYNTHETIC_FIXTURE_OPT_IN_ENV = "EVIDENCE_LAYER_ALLOW_SYNTHETIC_FIXTURE"
_ALLOWED_FIXTURE_MODES = frozenset({"development", "test"})


def require_synthetic_fixture_opt_in() -> None:
    """Require a known non-production mode and a separate explicit opt-in."""
    # Runtime policy comes only from the server process environment. Callers
    # cannot substitute an environment mapping through this policy API.
    mode = os.environ.get(RUNTIME_MODE_ENV)
    opted_in = os.environ.get(SYNTHETIC_FIXTURE_OPT_IN_ENV) == "true"
    if mode not in _ALLOWED_FIXTURE_MODES or not opted_in:
        raise RuntimeError("synthetic fixture use is disabled by runtime policy")
