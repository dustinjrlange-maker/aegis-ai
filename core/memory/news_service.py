"""
News Service -- Aegis AI
Aggregates news from multiple sources with in-memory caching.
Sources: DuckDuckGo News, Google News RSS, NewsAPI.org.
"""

import logging
import ssl
import time
from urllib.parse import quote_plus

logger = logging.getLogger(__name__)

# Cache duration in seconds (1 hour)
_CACHE_TTL = 3600

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


class _TLSAdapter:
    """HTTPS adapter using the default SSL context (picks up system certs on Windows)."""

    _instance = None

    @classmethod
    def get_session(cls):
        """Return a requests.Session with TLS adapter mounted."""
        if cls._instance is None:
            import requests
            from requests.adapters import HTTPAdapter

            class _Adapter(HTTPAdapter):
                def init_poolmanager(self, *args, **kwargs):
                    ctx = ssl.create_default_context()
                    kwargs["ssl_context"] = ctx
                    return super().init_poolmanager(*args, **kwargs)

            session = requests.Session()
            session.headers.update({"User-Agent": _USER_AGENT})
            session.mount("https://", _Adapter())
            cls._instance = session
        return cls._instance


class NewsService:
    """Fetches news from multiple sources with per-source+category caching.

    Supported sources:
        - "ddgs"    : DuckDuckGo News (no config required)
        - "rss"     : Google News RSS (no config required)
        - "newsapi" : NewsAPI.org (requires API key)
    """

    def __init__(self, newsapi_key: str | None = None):
        self._newsapi_key = newsapi_key
        # Cache keyed by (source, category, location) -> (timestamp, results)
        self._cache: dict[tuple[str, str, str], tuple[float, list[dict]]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_news(
        self,
        source: str = "ddgs",
        category: str = "international",
        location: str = "",
    ) -> list[dict]:
        """Fetch news articles from the specified source.

        Args:
            source: One of "ddgs", "rss", or "newsapi".
            category: "local" (uses *location*) or "international".
            location: Location string for local news (e.g. "San Francisco").

        Returns:
            List of dicts with keys: title, url, source, published, snippet.
            Returns an empty list if the source fails or is misconfigured.
        """
        cache_key = (source, category, location.lower().strip())
        cached = self._cache.get(cache_key)
        if cached is not None:
            ts, results = cached
            if (time.time() - ts) < _CACHE_TTL:
                return results

        fetcher = {
            "ddgs": self._fetch_ddgs,
            "rss": self._fetch_rss,
            "newsapi": self._fetch_newsapi,
        }.get(source)

        if fetcher is None:
            logger.warning("Unknown news source: %s", source)
            return []

        results = fetcher(category, location)
        self._cache[cache_key] = (time.time(), results)
        return results

    def get_sources(self) -> list[dict]:
        """Return available news sources and their config requirements."""
        return [
            {
                "id": "ddgs",
                "name": "DuckDuckGo News",
                "needs_config": False,
                "configured": True,
            },
            {
                "id": "rss",
                "name": "Google News RSS",
                "needs_config": False,
                "configured": True,
            },
            {
                "id": "newsapi",
                "name": "NewsAPI.org",
                "needs_config": True,
                "configured": self._newsapi_key is not None,
            },
        ]

    def set_newsapi_key(self, key: str) -> None:
        """Set (or update) the NewsAPI.org API key and invalidate newsapi cache."""
        self._newsapi_key = key
        # Purge any cached newsapi results since the key changed
        self._cache = {
            k: v for k, v in self._cache.items() if k[0] != "newsapi"
        }
        logger.info("NewsAPI key updated")

    # ------------------------------------------------------------------
    # Private fetchers
    # ------------------------------------------------------------------

    def _build_query(self, category: str, location: str) -> str:
        """Build a search query string from category and location."""
        if category in ("local", "topic") and location:
            return f"{location} news today"
        return "world news today"

    @staticmethod
    def _is_junk_article(article: dict) -> bool:
        """Filter out generic website descriptions that aren't real articles."""
        title = (article.get("title") or "").lower()
        snippet = (article.get("snippet") or "").lower()
        # Generic homepage / index entries
        junk_titles = [
            "world news - latest",
            "world news |",
            "nprworld",
            "top stories",
            "breaking news, latest news",
            "latest and breaking coverage",
            "subscribe to the",
        ]
        for junk in junk_titles:
            if junk in title:
                return True
        # Very old articles (before current year) are likely index pages
        published = article.get("published", "")
        if published and ("2019" in published or "2020" in published or "2021" in published
                          or "2022" in published or "2023" in published or "2024" in published):
            return True
        # Snippets that are just website descriptions
        if "subscribe to" in snippet[:50] or "stay informed" in snippet[:50]:
            return True
        return False

    def _fetch_ddgs(self, category: str, location: str) -> list[dict]:
        """Fetch news via DuckDuckGo News search."""
        try:
            from ddgs import DDGS

            query = self._build_query(category, location)
            with DDGS() as ddgs:
                raw = list(ddgs.news(query, max_results=15))

            results = []
            for item in raw:
                article = {
                    "title": item.get("title", ""),
                    "url": item.get("url", item.get("link", "")),
                    "source": item.get("source", "DuckDuckGo"),
                    "published": item.get("date", item.get("published", "")),
                    "snippet": item.get("body", item.get("excerpt", "")),
                }
                if not self._is_junk_article(article):
                    results.append(article)
            return results[:10]

        except Exception as e:
            logger.warning("DuckDuckGo news fetch failed: %s", e)
            return []

    def _fetch_rss(self, category: str, location: str) -> list[dict]:
        """Fetch news via Google News RSS feed."""
        try:
            import feedparser

            if category == "local" and location:
                encoded = quote_plus(f"{location} news")
                url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
            else:
                url = "https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRGx1YlY4U0FtVnVHZ0pWVXlnQVAB?hl=en-US&gl=US&ceid=US:en"

            feed = feedparser.parse(url)

            results = []
            for entry in feed.entries[:10]:
                results.append({
                    "title": entry.get("title", ""),
                    "url": entry.get("link", ""),
                    "source": entry.get("source", {}).get("title", "Google News") if isinstance(entry.get("source"), dict) else "Google News",
                    "published": entry.get("published", ""),
                    "snippet": entry.get("summary", ""),
                })
            return results

        except Exception as e:
            logger.warning("Google News RSS fetch failed: %s", e)
            return []

    def _fetch_newsapi(self, category: str, location: str) -> list[dict]:
        """Fetch news via NewsAPI.org."""
        if not self._newsapi_key:
            logger.warning("NewsAPI key not configured")
            return []

        try:
            import requests

            session = _TLSAdapter.get_session()

            if category == "local" and location:
                # Use /everything endpoint with location query for local news
                params = {
                    "q": f"{location}",
                    "sortBy": "publishedAt",
                    "pageSize": 10,
                    "apiKey": self._newsapi_key,
                }
                url = "https://newsapi.org/v2/everything"
            else:
                # Use /top-headlines for international news
                params = {
                    "category": "general",
                    "language": "en",
                    "pageSize": 10,
                    "apiKey": self._newsapi_key,
                }
                url = "https://newsapi.org/v2/top-headlines"

            resp = session.get(url, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            if data.get("status") != "ok":
                logger.warning("NewsAPI returned status: %s", data.get("status"))
                return []

            results = []
            for article in data.get("articles", [])[:10]:
                results.append({
                    "title": article.get("title", ""),
                    "url": article.get("url", ""),
                    "source": article.get("source", {}).get("name", "NewsAPI"),
                    "published": article.get("publishedAt", ""),
                    "snippet": article.get("description", ""),
                })
            return results

        except Exception as e:
            logger.warning("NewsAPI fetch failed: %s", e)
            return []
