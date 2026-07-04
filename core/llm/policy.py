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


def decide(sensitivity, cfg, *, task=None, offline=False, trouble=False):
    """Choose a backend for one LLM call.

    cfg must expose .cloud_enabled (bool), .cloud_opt_in_features (iterable of
    task tags), and .deep_mode (bool). `task` is the per-feature opt-in key for
    the `private` tier AND gates `personal` escalation: personal routes to cloud
    only for task="chat_task" (or "chat_emotional" when cfg.deep_mode is true);
    everything else personal stays local. `public` always escalates when
    cloud is enabled and online.

    `trouble=True` with cfg.cloud_trouble_escalation set escalates non-private
    turns to cloud independently of the main cloud_enabled toggle.
    """
    if sensitivity not in VALID_SENSITIVITIES:
        raise ValueError(
            f"Unknown sensitivity {sensitivity!r}; expected one of {VALID_SENSITIVITIES}"
        )
    if offline:
        return RouteDecision("local", "offline", False)
    # Trouble escalation: independent of cloud_enabled, gated by its own flag.
    # Never fires for the `private` sensitivity tier (protects tool-synthesis /
    # file contents pinned to private). Chat "private content" is gated upstream
    # in the pipeline before trouble=True is ever passed.
    if (trouble and getattr(cfg, "cloud_trouble_escalation", False)
            and sensitivity != "private"):
        return RouteDecision("cloud", "trouble_escalation", True)
    if not cfg.cloud_enabled:
        return RouteDecision("local", "cloud_disabled", False)
    if sensitivity == "private" and task not in cfg.cloud_opt_in_features:
        return RouteDecision("local", "private_local_default", False)
    if sensitivity == "personal":
        if task == "chat_task":
            return RouteDecision("cloud", "cloud_eligible", True)
        if task == "chat_emotional" and getattr(cfg, "deep_mode", False):
            return RouteDecision("cloud", "deep_mode", True)
        return RouteDecision("local", "personal_local_default", False)
    return RouteDecision("cloud", "cloud_eligible", True)
