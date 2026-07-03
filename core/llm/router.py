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

from core.llm import policy as _policy
from core.llm.backends import CloudBackend, LocalBackend
from core.llm.config import load_config

logger = logging.getLogger(__name__)

_BACKENDS = {"local": LocalBackend(), "cloud": CloudBackend()}


def chat(messages, *, sensitivity, task=None, model=None, options=None, format=None) -> str:
    """Route one LLM call and return the response content string.

    sensitivity: "private" | "personal" | "public" (required — every site tags).
    task: opt-in / intent tag, logged; inert for tier escalation this build.
    model/options/format: passthrough to the backend (ollama semantics).
    Config is re-read each call so the cloud toggle can change at runtime
    without restart.
    """
    cfg = load_config()
    decision = _policy.decide(sensitivity, cfg, task=task)
    backend = _BACKENDS[decision.backend]

    if decision.backend == "cloud":
        if backend.available():
            try:
                return backend.chat(messages, model=model, options=options, format=format)
            except Exception as e:
                logger.warning(
                    "[llm-router] cloud call failed (%s: %s) sensitivity=%s task=%s "
                    "— falling back to local",
                    type(e).__name__, e, sensitivity, task,
                    exc_info=True,
                )
                backend = _BACKENDS["local"]
        else:
            logger.info(
                "[llm-router] cloud escalation preview: sensitivity=%s task=%s "
                "policy_reason=%s — cloud unavailable, executing locally",
                sensitivity, task, decision.reason,
            )
            backend = _BACKENDS["local"]

    return backend.chat(messages, model=model, options=options, format=format)
