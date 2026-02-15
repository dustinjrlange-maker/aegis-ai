"""
Web Tools — Aegis AI
Low-level web search and page fetch functions.
Separated from the protocol to keep HTTP operations isolated and testable.
"""

import logging
import re
import ssl
import time
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from ddgs import DDGS

logger = logging.getLogger(__name__)

# Shared session for connection pooling
_session = None
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


class _TLSAdapter(HTTPAdapter):
    """HTTPS adapter that uses the default SSL context (picks up system certs)."""

    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


def _get_session():
    """Lazy-init a requests session with a realistic user-agent."""
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"User-Agent": _USER_AGENT})
        _session.mount("https://", _TLSAdapter())
    return _session


def search_web(query, max_results=5, timeout=10):
    """Search DuckDuckGo and return structured results.

    Returns:
        list of dicts: [{"title": ..., "url": ..., "snippet": ...}, ...]
        Empty list on failure.
    """
    try:
        with DDGS() as ddgs:
            raw = list(ddgs.text(query, max_results=max_results))

        results = []
        for item in raw:
            results.append({
                "title": item.get("title", ""),
                "url": item.get("href", item.get("link", "")),
                "snippet": item.get("body", item.get("snippet", "")),
            })
        return results

    except Exception as e:
        logger.warning("Web search failed for '%s': %s", query, e)
        return []


def fetch_page(url, max_chars=1500, timeout=10):
    """Fetch a web page and extract clean text content.

    Uses trafilatura for content extraction. Falls back to basic
    HTML stripping if trafilatura can't extract content.

    Returns:
        dict: {"url": ..., "title": ..., "text": ..., "success": True/False}
    """
    result = {"url": url, "title": "", "text": "", "success": False}

    # Basic URL validation
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        result["text"] = "Invalid URL."
        return result

    try:
        session = _get_session()
        resp = session.get(url, timeout=timeout, allow_redirects=True)
        resp.raise_for_status()
        html = resp.text

        # Try trafilatura for clean extraction
        try:
            import trafilatura
            extracted = trafilatura.extract(html, include_links=False, include_tables=False)
            if extracted:
                result["text"] = extracted[:max_chars]
                result["success"] = True
        except ImportError:
            logger.warning("trafilatura not installed, using basic extraction")

        # Fallback: basic tag stripping
        if not result["success"]:
            text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r'<[^>]+>', ' ', text)
            text = re.sub(r'\s+', ' ', text).strip()
            if text:
                result["text"] = text[:max_chars]
                result["success"] = True

        # Try to extract title
        title_match = re.search(r'<title[^>]*>(.*?)</title>', html, re.IGNORECASE | re.DOTALL)
        if title_match:
            result["title"] = title_match.group(1).strip()[:200]

    except requests.exceptions.Timeout:
        logger.warning("Timeout fetching '%s'", url)
        result["text"] = "Request timed out."
    except requests.exceptions.RequestException as e:
        logger.warning("Failed to fetch '%s': %s", url, e)
        result["text"] = f"Could not fetch page: {e}"
    except Exception as e:
        logger.warning("Unexpected error fetching '%s': %s", url, e)
        result["text"] = f"Error: {e}"

    return result
