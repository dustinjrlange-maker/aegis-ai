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


def _fetch_html(url, timeout=15):
    """Fetch raw HTML from a URL. Tries trafilatura's fetcher first (better
    bot evasion), then falls back to our requests session with full browser
    headers."""
    # Method 1: trafilatura's own fetcher (handles redirects, retries, TLS)
    try:
        import trafilatura
        html = trafilatura.fetch_url(url)
        if html and len(html) > 200:
            logger.info("[ARTICLE] trafilatura.fetch_url succeeded (%d chars)", len(html))
            return html
        logger.info("[ARTICLE] trafilatura.fetch_url returned empty/short, trying requests")
    except Exception as e:
        logger.info("[ARTICLE] trafilatura.fetch_url failed: %s, trying requests", e)

    # Method 2: requests with full browser-like headers
    session = _get_session()
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
    }
    resp = session.get(url, timeout=timeout, allow_redirects=True, headers=headers)
    resp.raise_for_status()
    logger.info("[ARTICLE] requests.get succeeded (%d chars)", len(resp.text))
    return resp.text


def fetch_page_rich(url, max_chars=50000, timeout=15):
    """Fetch a web page and extract rich HTML content with images.

    Uses trafilatura with favor_recall + HTML output + images for
    article-quality extraction. Falls back to BeautifulSoup extraction.

    Returns:
        dict: {"url": ..., "title": ..., "html": ..., "text": ..., "success": True/False}
    """
    result = {"url": url, "title": "", "html": "", "text": "", "success": False}

    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        result["text"] = "Invalid URL."
        return result

    base_url = f"{parsed.scheme}://{parsed.netloc}"

    try:
        raw_html = _fetch_html(url, timeout=timeout)
        if not raw_html:
            result["text"] = "Could not download page."
            return result

        # Extract title from raw HTML
        title_match = re.search(r'<title[^>]*>(.*?)</title>', raw_html, re.IGNORECASE | re.DOTALL)
        if title_match:
            result["title"] = re.sub(r'<[^>]+>', '', title_match.group(1)).strip()[:200]

        # Try trafilatura with rich HTML output
        try:
            import trafilatura

            # First try: HTML output with images and recall preference
            html_content = trafilatura.extract(
                raw_html,
                url=url,
                output_format="html",
                include_images=True,
                include_links=False,
                include_tables=True,
                favor_recall=True,
            )
            if html_content and len(html_content) > 50:
                # Fix relative image URLs to absolute
                html_content = _fix_image_urls(html_content, base_url, url)
                result["html"] = html_content[:max_chars]
                result["success"] = True
                logger.info("[ARTICLE] trafilatura HTML extraction OK (%d chars)", len(html_content))

            # Also get plain text for fallback/title extraction
            text_content = trafilatura.extract(
                raw_html,
                url=url,
                include_links=False,
                favor_recall=True,
            )
            if text_content:
                result["text"] = text_content[:5000]
                if not result["title"]:
                    first_line = text_content.split("\n")[0].strip()
                    if len(first_line) > 10:
                        result["title"] = first_line[:200]
        except ImportError:
            logger.warning("trafilatura not installed")

        # Fallback: BeautifulSoup extraction
        if not result["success"]:
            logger.info("[ARTICLE] Trying BeautifulSoup fallback")
            try:
                from bs4 import BeautifulSoup

                soup = BeautifulSoup(raw_html, "html.parser")

                # Remove scripts, styles, nav, footer, ads
                for tag in soup.find_all(["script", "style", "nav", "footer",
                                          "aside", "iframe", "noscript"]):
                    tag.decompose()
                for div in soup.find_all(["div", "section"], class_=re.compile(
                        r"(ad[s_-]|social|share|comment|sidebar|popup|modal|cookie|consent|newsletter)",
                        re.IGNORECASE)):
                    div.decompose()

                # Find the main article content
                article = (
                    soup.find("article")
                    or soup.find("main")
                    or soup.find(["div", "section"], class_=re.compile(r"(article|content|story|post)", re.IGNORECASE))
                    or soup.find("body")
                )

                if article:
                    allowed_tags = {"p", "h1", "h2", "h3", "h4", "img", "figure",
                                    "figcaption", "blockquote", "ul", "ol", "li", "br"}
                    parts = []
                    for el in article.find_all(allowed_tags):
                        if el.name == "img":
                            src = el.get("src", el.get("data-src", ""))
                            alt = el.get("alt", "")
                            if src:
                                if src.startswith("//"):
                                    src = "https:" + src
                                elif src.startswith("/"):
                                    src = base_url + src
                                parts.append(
                                    f'<figure style="margin:12px 0;text-align:center">'
                                    f'<img src="{src}" alt="{alt}" style="max-width:100%;border-radius:4px">'
                                    f'</figure>'
                                )
                        else:
                            text = el.get_text(strip=True)
                            if text and len(text) > 20:
                                tag = el.name if el.name in ("h1", "h2", "h3", "h4", "blockquote") else "p"
                                parts.append(f"<{tag}>{text}</{tag}>")

                    if parts:
                        result["html"] = "\n".join(parts)[:max_chars]
                        result["success"] = True
                        logger.info("[ARTICLE] BeautifulSoup extraction OK (%d parts)", len(parts))

                    if not result["title"]:
                        h1 = article.find("h1")
                        if h1:
                            result["title"] = h1.get_text(strip=True)[:200]

            except ImportError:
                logger.warning("bs4 not installed for fallback extraction")

        # Last resort: basic tag stripping
        if not result["success"]:
            logger.info("[ARTICLE] Using basic tag stripping fallback")
            text = re.sub(r'<script[^>]*>.*?</script>', '', raw_html, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r'<[^>]+>', ' ', text)
            text = re.sub(r'\s+', ' ', text).strip()
            if text:
                result["text"] = text[:5000]
                result["html"] = "<p>" + "</p><p>".join(
                    p.strip() for p in text[:max_chars].split(". ") if p.strip()
                ) + "</p>"
                result["success"] = True

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


def _fix_image_urls(html_content, base_url, page_url):
    """Convert relative image src attributes to absolute URLs."""
    from urllib.parse import urljoin

    def replace_src(match):
        src = match.group(1)
        if src.startswith(("http://", "https://", "data:")):
            return match.group(0)
        absolute = urljoin(page_url, src)
        return f'src="{absolute}"'

    html_content = re.sub(r'src="([^"]*)"', replace_src, html_content)
    # trafilatura uses <graphic> instead of <img> — convert them
    html_content = re.sub(
        r'<graphic\s+src="([^"]*)"[^/]*/?>',
        r'<img src="\1" style="max-width:100%;border-radius:4px;margin:8px 0;display:block">',
        html_content,
    )
    return html_content
