# core/llm/backends.py
"""LLM backends behind the router.

LocalBackend wraps ollama.chat (real). CloudBackend is a stub this build:
available() is False so the router never routes to it — the later cloud build
fills in the Anthropic adapter here.
"""
from __future__ import annotations

import ollama

from core.config import CONFIG


class CloudRefusalError(RuntimeError):
    """Claude declined the request (stop_reason == 'refusal')."""


class CloudResponseError(RuntimeError):
    """Cloud response could not be used (no text block, or bad message shape)."""


class LocalBackend:
    """Ollama-backed local inference."""
    name = "local"

    def available(self):
        return True

    def chat(self, messages, *, model=None, options=None, format=None):
        kwargs = {
            "model": model or CONFIG["model"]["chat"],
            "messages": messages,
        }
        if options:
            kwargs["options"] = options
        if format:
            kwargs["format"] = format
        response = ollama.chat(**kwargs)
        return response["message"]["content"]


class CloudBackend:
    """Placeholder for the future Claude API adapter. Not wired this build."""
    name = "cloud"

    def available(self):
        return False

    def chat(self, messages, *, model=None, options=None, format=None):
        raise NotImplementedError("Cloud backend is not wired yet (local-only build)")


def _split_system(messages):
    """Translate ollama-style messages to the Anthropic shape.

    Anthropic takes the system prompt as a top-level `system=` string, not a
    role. Collect all system messages into one string; keep only user/assistant
    messages. The first remaining message must be a user turn.
    """
    system_parts = []
    convo = []
    for m in messages:
        if m.get("role") == "system":
            content = m.get("content", "")
            if content:
                system_parts.append(content)
        else:
            convo.append({"role": m["role"], "content": m["content"]})
    if not convo or convo[0]["role"] != "user":
        raise CloudResponseError("Anthropic requires a leading user message")
    return "\n\n".join(system_parts), convo
