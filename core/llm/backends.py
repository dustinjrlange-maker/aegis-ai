# core/llm/backends.py
"""LLM backends behind the router.

LocalBackend wraps ollama.chat (real). CloudBackend is a stub this build:
available() is False so the router never routes to it — the later cloud build
fills in the Anthropic adapter here.
"""
from __future__ import annotations

import ollama

from core.config import CONFIG


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
