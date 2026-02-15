"""
Web Access Protocol — Aegis AI
Provides web search and page fetching via DuckDuckGo and trafilatura.

Supports explicit slash commands (/search, /fetch) and auto-detection
of natural language search intent. Results are injected as context so
the LLM can reason over them naturally.
"""

import re
import time
import logging

from core.protocols.base import Protocol
from core.protocols.web_tools import search_web, fetch_page
from core.config import CONFIG

logger = logging.getLogger(__name__)


class WebProtocol(Protocol):
    """Web search and page fetching."""

    # Patterns that indicate the user wants to look something up.
    # Ordered from most specific to least to reduce false positives.
    SEARCH_PATTERNS = [
        r"(?:search|look\s+up|find\s+info(?:rmation)?\s+(?:about|on))\s+(.+)",
        r"(?:can you (?:find|search|look up))\s+(.+)",
        r"what(?:'s| is) the latest (?:on|about|with)\s+(.+)",
        r"^who is\s+(.{4,})",
        r"^what is (?:a |an |the )?(.{4,})",
        r"^how (?:do|does|can|to)\s+(.{6,})",
    ]

    # Patterns that look like search intent but are actually personal/conversational.
    # These suppress auto-detection to avoid false positives.
    PERSONAL_PATTERNS = [
        r"(?:tell me about|what is) your ",
        r"how (?:are|were|have) you",
        r"how was your",
        r"what(?:'s| is) (?:your|my) ",
        r"who are you",
        r"do you (?:like|want|feel|think|remember)",
    ]

    def __init__(self):
        web_cfg = CONFIG.get("web", {})
        super().__init__(
            name="web",
            description="Web search and page fetching",
            priority=Protocol.PRIORITY_NORMAL + 5,
        )
        self._max_results = web_cfg.get("search_max_results", 5)
        self._max_chars = web_cfg.get("fetch_max_chars", 1500)
        self._timeout = web_cfg.get("request_timeout", 10)
        self._auto_detect = web_cfg.get("auto_detect", True)
        self._last_search_time = 0.0
        self._rate_limit_seconds = 2.0

        if not web_cfg.get("enabled", True):
            self.disable()

    # ------------------------------------------------------------------
    # Protocol interface
    # ------------------------------------------------------------------

    def process_input(self, user_input, context):
        result = {
            "input": user_input,
            "context_injection": "",
            "intercept": False,
            "response": "",
        }

        if not self._enabled or not self._auto_detect:
            return result

        lower = user_input.lower().strip()

        # Skip if this looks like a personal/conversational question
        for pat in self.PERSONAL_PATTERNS:
            if re.search(pat, lower):
                return result

        # Try to extract a search query from natural language
        query = None
        for pat in self.SEARCH_PATTERNS:
            match = re.search(pat, lower)
            if match:
                query = match.group(1).strip().rstrip("?.!")
                break

        if not query or len(query) < 3:
            return result

        # Rate limiting
        now = time.time()
        if now - self._last_search_time < self._rate_limit_seconds:
            return result

        # Run the search (top 3 for auto-detect to keep context small)
        results = search_web(query, max_results=3, timeout=self._timeout)
        self._last_search_time = time.time()

        if results:
            result["context_injection"] = self._format_search_results(query, results)

        return result

    def process_output(self, response, context):
        return {"response": response, "suppress": False, "append": ""}

    # ------------------------------------------------------------------
    # Slash commands
    # ------------------------------------------------------------------

    def get_commands(self):
        return [
            {"command": "search", "description": "Search the web", "handler": "cmd_search"},
            {"command": "fetch", "description": "Fetch a web page", "handler": "cmd_fetch"},
        ]

    def cmd_search(self, args=""):
        """Handle /search <query>."""
        query = args.strip()
        if not query:
            return "  Usage: /search <query>"

        # Rate limiting
        now = time.time()
        wait = self._rate_limit_seconds - (now - self._last_search_time)
        if wait > 0:
            time.sleep(wait)

        results = search_web(query, max_results=self._max_results, timeout=self._timeout)
        self._last_search_time = time.time()

        if not results:
            return "  No results found (search may have timed out)."

        lines = [f"  Web search: {query}", ""]
        for i, r in enumerate(results, 1):
            lines.append(f"  {i}. {r['title']}")
            lines.append(f"     {r['url']}")
            snippet = r["snippet"]
            if len(snippet) > 200:
                snippet = snippet[:200] + "..."
            lines.append(f"     {snippet}")
            lines.append("")
        return "\n".join(lines)

    def cmd_fetch(self, args=""):
        """Handle /fetch <url>."""
        url = args.strip()
        if not url:
            return "  Usage: /fetch <url>"

        # Add scheme if missing
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        result = fetch_page(url, max_chars=self._max_chars, timeout=self._timeout)

        if not result["success"]:
            return f"  Failed to fetch: {result['text']}"

        lines = []
        if result["title"]:
            lines.append(f"  Page: {result['title']}")
        lines.append(f"  URL: {result['url']}")
        lines.append("")
        lines.append(result["text"])
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------

    def _format_search_results(self, query, results):
        """Format search results for context injection."""
        parts = [f"Web search results for '{query}':"]
        for i, r in enumerate(results, 1):
            snippet = r["snippet"]
            if len(snippet) > 200:
                snippet = snippet[:200] + "..."
            parts.append(f"{i}. {r['title']} ({r['url']}): {snippet}")
        return "\n".join(parts)

    def get_status(self):
        status = super().get_status()
        status["auto_detect"] = self._auto_detect
        status["max_results"] = self._max_results
        return status
