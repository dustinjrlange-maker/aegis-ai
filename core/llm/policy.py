# core/llm/policy.py
"""Pure routing-decision logic for the hybrid LLM router.

No I/O, no Ollama — a lookup over (sensitivity, config, task). Kept separate
from router.py so the whole policy is unit-testable without a model.
"""
from __future__ import annotations

from dataclasses import dataclass

VALID_SENSITIVITIES = ("private", "personal", "public")


@dataclass(frozen=True)
class RouteDecision:
    """The router's choice for one call."""
    backend: str            # "local" | "cloud"
    reason: str
    would_send_cloud: bool  # True when policy picked cloud (drives the preview log)


def decide(sensitivity, cfg, *, task=None, offline=False):
    """Choose a backend for one LLM call.

    cfg must expose .cloud_enabled (bool) and .cloud_opt_in_features (iterable
    of task tags). `task` is used only as the per-feature opt-in key for the
    `private` tier — it does NOT drive tier escalation this build.
    """
    if sensitivity not in VALID_SENSITIVITIES:
        raise ValueError(
            f"Unknown sensitivity {sensitivity!r}; expected one of {VALID_SENSITIVITIES}"
        )
    if not cfg.cloud_enabled:
        return RouteDecision("local", "cloud_disabled", False)
    if offline:
        return RouteDecision("local", "offline", False)
    if sensitivity == "private" and task not in cfg.cloud_opt_in_features:
        return RouteDecision("local", "private_local_default", False)
    return RouteDecision("cloud", "cloud_eligible", True)
