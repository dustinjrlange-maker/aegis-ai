# core/llm/backends.py
"""LLM backends behind the router.

LocalBackend wraps ollama.chat. CloudBackend calls the Anthropic Claude API,
enabled only when a key resolves and the anthropic package is importable.
"""
from __future__ import annotations

import ollama

from core.config import CONFIG
from core.llm.config import load_config, resolve_api_key


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


def _anthropic_installed():
    """Return True if the anthropic package can be imported."""
    try:
        import anthropic  # noqa: F401
        return True
    except ImportError:
        return False


class CloudBackend:
    """Anthropic Claude API adapter.

    Enabled only when an API key resolves AND the `anthropic` package is
    importable; otherwise available() is False and the router falls back to
    local. Ignores the passed (local) model and uses the configured cloud model.
    """
    name = "cloud"

    def __init__(self):
        self._client = None  # lazily constructed anthropic.Anthropic

    def available(self):
        if resolve_api_key() is None:
            return False
        return _anthropic_installed()

    def _get_client(self):
        # Client is cached after first construction. available() re-reads the
        # key each call, so a key that appears after startup is picked up on
        # first use; a mid-session key ROTATION isn't (cached client keeps the
        # old key until a new CloudBackend / restart). Fine for single-user local.
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=resolve_api_key())
        return self._client

    def chat(self, messages, *, model=None, options=None, format=None):
        cfg = load_config()
        system, convo = _split_system(messages)
        kwargs = {
            "model": cfg.cloud_model,          # configured cloud model, not `model`
            "max_tokens": cfg.cloud_max_tokens,
            "messages": convo,
        }
        if system:
            kwargs["system"] = system
        response = self._get_client().messages.create(**kwargs)
        # Claude 4-family models (incl. Opus 4.8) return stop_reason="refusal"
        # on a safety-classifier decline (HTTP 200). Treat as a failure so the
        # router falls back to local — matters for firearms-adjacent prompts.
        if getattr(response, "stop_reason", None) == "refusal":
            raise CloudRefusalError("Claude declined the request")
        for block in response.content:
            if getattr(block, "type", None) == "text":
                return block.text
        raise CloudResponseError("No text block in Claude response")


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
