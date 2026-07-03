# core/llm/router.py
"""The single seam every Aegis LLM call routes through.

chat() picks a backend via policy.decide(), then executes it. When policy
picks cloud, the router runs the cloud backend if it's available and falls back
to local on any failure (network, auth, rate-limit, or a safety refusal); when
cloud is unavailable (no key / package), it logs a transparency preview and
executes locally.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from core.llm import policy as _policy
from core.llm.backends import CloudBackend, LocalBackend
from core.llm.config import load_config

logger = logging.getLogger(__name__)

_BACKENDS = {"local": LocalBackend(), "cloud": CloudBackend()}


@dataclass(frozen=True)
class RouteMeta:
    """Which backend actually served one call (for the ☁ announcement)."""
    backend_used: str        # "local" | "cloud"
    decision_reason: str
    cloud_model: str | None = None


def chat_with_meta(messages, *, sensitivity, task=None, model=None,
                   options=None, format=None) -> tuple[str, RouteMeta]:
    """Route one LLM call; return (content, RouteMeta). Meta reports the backend
    that ACTUALLY answered — a cloud pick that falls back reports local."""
    cfg = load_config()
    decision = _policy.decide(sensitivity, cfg, task=task)
    backend = _BACKENDS[decision.backend]
    reason = decision.reason

    if decision.backend == "cloud":
        if backend.available():
            try:
                content = backend.chat(messages, model=model, options=options, format=format)
                return content, RouteMeta("cloud", reason, cfg.cloud_model)
            except Exception as e:
                logger.warning(
                    "[llm-router] cloud call failed (%s: %s) sensitivity=%s task=%s "
                    "— falling back to local",
                    type(e).__name__, e, sensitivity, task,
                    exc_info=True,
                )
                backend = _BACKENDS["local"]
                reason = "cloud_failed_fallback"
        else:
            logger.info(
                "[llm-router] cloud escalation preview: sensitivity=%s task=%s "
                "policy_reason=%s — cloud unavailable, executing locally",
                sensitivity, task, decision.reason,
            )
            backend = _BACKENDS["local"]
            reason = "cloud_unavailable_fallback"

    content = backend.chat(messages, model=model, options=options, format=format)
    return content, RouteMeta("local", reason, None)


def chat(messages, *, sensitivity, task=None, model=None, options=None, format=None) -> str:
    """Route one LLM call and return the response content string.

    sensitivity: "private" | "personal" | "public" (required — every site tags).
    task: routing tag — for personal chat use chat_task / chat_emotional / chat_casual.
    Config is re-read each call so toggles change at runtime without restart.
    """
    content, _meta = chat_with_meta(
        messages, sensitivity=sensitivity, task=task,
        model=model, options=options, format=format,
    )
    return content
