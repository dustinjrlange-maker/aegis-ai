"""
Web Access Protocol — Aegis AI
Provides web search, page fetching, and news retrieval via DuckDuckGo
and trafilatura.

Supports explicit slash commands (/search, /fetch, /news) and auto-detection
of natural language search and news intent.

News requests are intercepted and handled directly — we build a structured
briefing instead of relying on the LLM to format headlines.
"""

import re
import time
import logging

from core.protocols.base import Protocol
from core.protocols.web_tools import search_web, fetch_page, fetch_page_rich
from core.memory.news_service import NewsService
from core.config import CONFIG

logger = logging.getLogger(__name__)


class WebProtocol(Protocol):
    """Web search, page fetching, and news retrieval."""

    # Patterns that indicate the user wants to look something up.
    SEARCH_PATTERNS = [
        r"(?:search|look\s+up|find\s+info(?:rmation)?\s+(?:about|on))\s+(.+)",
        r"(?:can you (?:find|search|look up))\s+(.+)",
        r"what(?:'s| is) the latest (?:on|about|with)\s+(.+)",
        r"^who is\s+(.{4,})",
        r"^what is (?:a |an |the )?(.{4,})",
        r"^how (?:do|does|can|to)\s+(.{6,})",
    ]

    # News intent patterns.  Each tuple is (regex, type).
    # type is "location" (group 1 = place), "topic" (group 1 = subject),
    # "scope" (group 1 = scope word), or "general" (no capture needed).
    NEWS_PATTERNS = [
        # --- Location-based (in/from/around/for + place) ---
        # "what's the news in burnaby"
        (r"what(?:['\u2019]?s| is) (?:the )?news (?:in|from|around|for)\s+(.+)", "location"),
        # "any news in burnaby"
        (r"(?:any|got any|is there(?: any)?)\s+news\s+(?:in|from|around|for)\s+(.+)", "location"),
        # "what's happening in burnaby"
        (r"what(?:['\u2019]?s| is) happening (?:in|around)\s+(.+)", "location"),
        # "what's going on in burnaby"
        (r"what(?:['\u2019]?s| is) going on (?:in|around)\s+(.+)", "location"),
        # "tell me the news in burnaby"
        (r"tell me (?:the |about (?:the )?)?news\s+(?:in|from|around|for)\s+(.+)", "location"),
        # "news in burnaby" (bare)
        (r"^news (?:in|from|around|for)\s+(.+)", "location"),

        # --- Topic-based (about/on/with/regarding + subject) ---
        # "what's the news about ukraine", "news on AI", "news with the iran war"
        (r"what(?:['\u2019]?s| is) (?:the )?(?:latest )?news (?:about|on|with|regarding)\s+(.+)", "topic"),
        # "what's in the news about/with X"
        (r"what(?:['\u2019]?s| is) in the news (?:about|on|with|regarding)\s+(.+)", "topic"),
        # "any news about ukraine"
        (r"(?:any|got any|is there(?: any)?)\s+news\s+(?:about|on|with|regarding)\s+(.+)", "topic"),
        # "what's happening with ukraine"
        (r"what(?:['\u2019]?s| is) happening (?:with|regarding)\s+(.+)", "topic"),
        # "what's going on with the election"
        (r"what(?:['\u2019]?s| is) going on (?:with|regarding)\s+(.+)", "topic"),
        # "tell me the news about ukraine"
        (r"tell me (?:the |about (?:the )?)?news\s+(?:about|on|with|regarding)\s+(.+)", "topic"),
        # "news about ukraine" (bare)
        (r"^news (?:about|on|with|regarding)\s+(.+)", "topic"),

        # --- Scope-based (group 1 = scope word) ---
        # "what's the local/national/international news"
        (r"what(?:['\u2019]?s| is) (?:the )?(local|national|regional|international|world|global)\s+news", "scope"),
        # "give me local/national/international news"
        (r"(?:give|get|show) me (?:the |some )?(local|national|regional|international|world|global)\s+news", "scope"),
        # "what's the news globally/internationally", "what's in the news globally"
        (r"what(?:['\u2019]?s| is) (?:the |in the )?news\s+(globally|internationally|worldwide)", "scope"),
        # "what's happening globally/internationally"
        (r"what(?:['\u2019]?s| is) (?:happening|going on)\s+(globally|internationally|worldwide)", "scope"),

        # --- General (no capture) ---
        # "what's the news", "tell me the news", "any news today"
        (r"(?:what(?:['\u2019]?s| is) the news|tell me (?:the )?news|give me (?:the |a )?news(?: update)?|any news(?:\s+today)?)\s*[?.!]?\s*$", "general"),
        # "whats the news" (no apostrophe)
        (r"whats (?:the )?news", "general"),
        # "update me on the news", "catch me up on news"
        (r"(?:update|catch|fill) me (?:up )?(?:on|in on) (?:the\s+)?news", "general"),
        # "news" or "news?" alone
        (r"^news\s*[?.!]?\s*$", "general"),
    ]

    # Conversational patterns to skip
    PERSONAL_PATTERNS = [
        r"(?:tell me about|what is) your ",
        r"how (?:are|were|have) you",
        r"how was your",
        r"what(?:'s| is) (?:your|my) ",
        r"who are you",
        r"do you (?:like|want|feel|think|remember)",
    ]

    _SCOPE_MAP = {
        "local": ("local", ""),
        "national": ("local", ""),
        "regional": ("local", ""),
        "international": ("international", ""),
        "world": ("international", ""),
        "global": ("international", ""),
        "globally": ("international", ""),
        "internationally": ("international", ""),
        "worldwide": ("international", ""),
    }

    def __init__(self):
        web_cfg = CONFIG.get("web", {})
        super().__init__(
            name="web",
            description="Web search, page fetching, and news",
            priority=Protocol.PRIORITY_NORMAL + 5,
        )
        self._max_results = web_cfg.get("search_max_results", 5)
        self._max_chars = web_cfg.get("fetch_max_chars", 1500)
        self._timeout = web_cfg.get("request_timeout", 10)
        self._auto_detect = web_cfg.get("auto_detect", True)
        self._last_search_time = 0.0
        self._rate_limit_seconds = 2.0
        self._news_service = NewsService()
        # Store last briefing articles so user can say "tell me more about #3"
        self._last_briefing_articles = []

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

        # Skip personal/conversational questions
        for pat in self.PERSONAL_PATTERNS:
            if re.search(pat, lower):
                return result

        # --- "Tell me more about #N" ---
        article_idx = self._parse_tell_me_more(lower)
        if article_idx is not None and self._last_briefing_articles:
            logger.info("'Tell me more' matched article index %d (have %d articles)",
                        article_idx, len(self._last_briefing_articles))
            if 0 <= article_idx < len(self._last_briefing_articles):
                article = self._last_briefing_articles[article_idx]
                response = self._build_article_detail(article)
                result["intercept"] = True
                result["response"] = response
                return result
            else:
                logger.warning("Article index %d out of range (have %d)",
                               article_idx, len(self._last_briefing_articles))

        # --- News intent ---
        news_result = self._detect_news_intent(lower, context)
        if news_result is not None:
            category, location = news_result
            logger.info("News intent detected: category=%s, location=%r", category, location)

            now = time.time()
            if now - self._last_search_time < self._rate_limit_seconds:
                logger.info("News request rate-limited, skipping")
                return result

            # Build a full multi-scope briefing
            briefing = self._build_news_briefing(category, location, context)
            self._last_search_time = time.time()

            if briefing:
                result["intercept"] = True
                result["response"] = briefing
            return result

        # --- General web search intent ---
        query = None
        for pat in self.SEARCH_PATTERNS:
            match = re.search(pat, lower)
            if match:
                query = match.group(1).strip().rstrip("?.!")
                break

        if not query or len(query) < 3:
            return result

        now = time.time()
        if now - self._last_search_time < self._rate_limit_seconds:
            return result

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
            {"command": "news", "description": "Fetch current news", "handler": "cmd_news"},
        ]

    def cmd_search(self, args=""):
        """Handle /search <query>."""
        query = args.strip()
        if not query:
            return "  Usage: /search <query>"

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

    def cmd_news(self, args=""):
        """Handle /news [location]."""
        args = args.strip().lower()

        if args in ("international", "world", "global", ""):
            category, location = "international", ""
        else:
            category, location = "local", args

        return self._build_news_briefing(category, location, {}) or "  No news articles found."

    # ------------------------------------------------------------------
    # News briefing builder
    # ------------------------------------------------------------------

    def _build_news_briefing(self, requested_category, location, context):
        """Fetch news from multiple scopes and build a formatted Markdown briefing.

        For a general request, fetches international + national + local.
        For a scoped request, fetches just that scope.
        Returns a Markdown string with clickable article links.
        """
        sections = []
        all_articles = []
        seen_titles = set()

        def _dedup_articles(articles):
            """Remove duplicates by normalized title."""
            unique = []
            for a in articles:
                norm = re.sub(r'\s+', ' ', a.get("title", "").lower().strip())
                if norm and norm not in seen_titles:
                    seen_titles.add(norm)
                    unique.append(a)
            return unique

        def _fetch_section(label, category, loc, max_items=4):
            articles = self._news_service.get_news(
                source="ddgs", category=category, location=loc,
            )
            articles = _dedup_articles(articles)[:max_items]
            if articles:
                all_articles.extend(articles)
                sections.append((label, articles))

        # Determine what to fetch based on request
        user_location = self._location_from_context("local", context)
        user_country = self._location_from_context("national", context)

        if requested_category == "topic" and location:
            # Topic-specific: "news about ukraine", "news on AI"
            _fetch_section(f"News about {location.title()}", "topic", location, 6)
        elif requested_category == "international" and not location:
            # General "what's the news" — fetch all scopes
            _fetch_section("International", "international", "", 4)
            if user_country:
                _fetch_section(user_country, "local", user_country, 3)
            if user_location and user_location != user_country:
                _fetch_section(user_location, "local", user_location, 3)
        elif requested_category == "local" and location:
            # Specific location request
            _fetch_section(f"News for {location.title()}", "local", location, 6)
        elif requested_category == "international":
            _fetch_section("International News", "international", "", 6)
        else:
            _fetch_section("News", requested_category, location, 6)

        if not sections:
            return ""

        # Store for "tell me more" follow-ups
        self._last_briefing_articles = all_articles

        # Build Markdown response
        lines = ["**News Briefing**\n"]
        article_num = 0

        for label, articles in sections:
            lines.append(f"**{label}**\n")
            for a in articles:
                article_num += 1
                title = a.get("title", "Untitled")
                source = a.get("source", "")
                snippet = a.get("snippet", "")
                url = a.get("url", "")

                if len(snippet) > 180:
                    snippet = snippet[:180] + "..."

                # Article number + title (with link for the article reader)
                if url:
                    lines.append(f"**{article_num}.** [{title}]({url})")
                else:
                    lines.append(f"**{article_num}.** {title}")

                # Source + snippet
                meta = []
                if source:
                    meta.append(f"*{source}*")
                if snippet:
                    meta.append(snippet)
                if meta:
                    lines.append("  " + " - ".join(meta))
                lines.append("")

        lines.append('*Say "tell me more about #N" for details on any story, or click a headline to read the full article.*')

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # News intent detection
    # ------------------------------------------------------------------

    def _detect_news_intent(self, text, context):
        """Check if text is a news query.

        Returns (category, location_or_topic) if news intent detected, else None.
        category is one of: "local", "international", "topic".
        """
        for pat, kind in self.NEWS_PATTERNS:
            match = re.search(pat, text)
            if not match:
                continue

            if kind == "location":
                location = match.group(1).strip().rstrip("?.!")
                # "the world" / "the globe" → international scope
                if location.lower() in ("the world", "the globe", "world"):
                    return ("international", "")
                return ("local", location)

            if kind == "topic":
                topic = match.group(1).strip().rstrip("?.!")
                return ("topic", topic)

            if kind == "scope":
                scope_word = match.group(1).strip().lower()
                category, location = self._SCOPE_MAP.get(
                    scope_word, ("international", ""),
                )
                if category == "local" and not location:
                    location = self._location_from_context(scope_word, context)
                return (category, location)

            if kind == "general":
                return ("international", "")

        return None

    def _location_from_context(self, scope_word, context):
        """Try to extract a location from the user's profile data in context."""
        profile = ""
        if isinstance(context, dict):
            profile = context.get("profile", "") or ""
            if not profile:
                profile = context.get("user_profile", "") or ""

        if not profile:
            return ""

        for label in ("location", "city", "lives in", "hometown", "based in"):
            pat = re.compile(
                rf"{label}\s*[:=]\s*(.+?)(?:\n|$)", re.IGNORECASE,
            )
            m = pat.search(profile)
            if m:
                loc = m.group(1).strip().rstrip(",.")
                if scope_word == "national":
                    parts = [p.strip() for p in loc.split(",")]
                    return parts[-1] if parts else loc
                return loc

        return ""

    # ------------------------------------------------------------------
    # "Tell me more" parsing & article detail
    # ------------------------------------------------------------------

    # Word-to-number map for "tell me more about the first article"
    _WORD_NUMBERS = {
        "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
        "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
        "1st": 1, "2nd": 2, "3rd": 3, "4th": 4, "5th": 5,
        "6th": 6, "7th": 7, "8th": 8, "9th": 9, "10th": 10,
        "last": -1,
    }

    def _parse_tell_me_more(self, text):
        """Parse a 'tell me more about #N' request.

        Returns 0-based article index, or None if no match.
        """
        # Pattern 1: digit-based — "tell me more about 3", "more about #3",
        # "expand on 3", "details on #3", "summarize 3"
        m = re.search(
            r"(?:tell me more|more|expand|details?|elaborate|info|summarize|summary of)\s*"
            r"(?:about|on|for)?\s*"
            r"(?:(?:article|story|headline|number|item|news)\s*)?(?:#\s*)?(\d+)",
            text,
        )
        if m:
            return int(m.group(1)) - 1

        # Pattern 2: word-based — "tell me more about the first article",
        # "more about the third one", "summarize the last story"
        m = re.search(
            r"(?:tell me more|more|expand|details?|elaborate|info|summarize|summary of)\s*"
            r"(?:about|on|for)?\s*(?:the\s+)?"
            r"(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth"
            r"|1st|2nd|3rd|4th|5th|6th|7th|8th|9th|10th|last)"
            r"(?:\s+(?:article|story|headline|one|news))?",
            text,
        )
        if m:
            word = m.group(1).lower()
            idx = self._WORD_NUMBERS.get(word)
            if idx is not None:
                if idx == -1:
                    return len(self._last_briefing_articles) - 1
                return idx - 1

        return None

    # Domains that are JS-rendered or return garbage content
    _UNFETCHABLE_DOMAINS = {
        "msn.com", "www.msn.com",
        "reddit.com", "www.reddit.com", "old.reddit.com",
        "twitter.com", "x.com",
        "facebook.com", "www.facebook.com",
        "instagram.com", "www.instagram.com",
    }

    @staticmethod
    def _is_quality_content(text, title=""):
        """Check if extracted text looks like real article content vs nav junk."""
        lower = text.lower()
        # Count sentences (rough: sequences ending with . ! ?)
        sentences = len(re.findall(r'[.!?]\s', text))
        # Count "junk" indicators
        junk_phrases = sum(1 for p in (
            "skip to main", "log in", "sign up", "cookie", "privacy policy",
            "terms of service", "subscribe", "advertisement", "get the app",
            "expand navigation", "collapse navigation", "user agreement",
            "best advent calendars", "foolproof gifts", "shopping trends",
        ) if p in lower)

        if sentences < 3 or junk_phrases >= 3:
            return False

        # If we have a title, check that key title words appear
        # in the text (prevents getting unrelated article content)
        if title:
            stop_words = {"the", "a", "an", "in", "on", "at", "to", "for",
                          "of", "and", "or", "is", "are", "was", "were",
                          "by", "with", "from", "about", "after", "has",
                          "have", "been", "its", "it", "this", "that", "be",
                          "new", "says", "say", "more", "how", "why", "what"}
            title_words = [w for w in re.findall(r'[a-z]+', title.lower())
                           if w not in stop_words and len(w) > 3]
            if title_words:
                matches = sum(1 for w in title_words if w in lower)
                # At least 40% of significant title words should appear
                if matches < max(2, len(title_words) * 0.4):
                    return False

        return True

    def _fetch_article_text(self, url, title):
        """Fetch article text, with search-based fallback for unfetchable URLs.

        If the primary URL returns < 100 chars (common with MSN/Yahoo
        aggregators that are JS-rendered), searches for the article title
        and tries fetching from an alternative source.
        """
        from urllib.parse import urlparse

        full_text = ""

        # Skip known-unfetchable domains — go straight to search fallback
        skip_primary = False
        if url:
            domain = urlparse(url).netloc.lower()
            if any(d in domain for d in self._UNFETCHABLE_DOMAINS):
                logger.info("Skipping unfetchable domain %s, will search instead", domain)
                skip_primary = True

        # Try primary URL first (unless unfetchable domain)
        if url and not skip_primary:
            logger.info("Fetching full article: %s", url)
            page = fetch_page_rich(url, max_chars=12000, timeout=15)
            if page.get("success"):
                full_text = page.get("text", "") or ""
                if not full_text and page.get("html"):
                    full_text = re.sub(r'<[^>]+>', ' ', page["html"])
                    full_text = re.sub(r'\s+', ' ', full_text).strip()
                logger.info("Primary fetch: %d chars", len(full_text))

        # If too short, try search fallback — find the same story on a
        # different (fetchable) site.  Use DDG news search first (more
        # likely to return news articles), then fall back to web search.
        if len(full_text) < 100 and title:
            logger.info("Primary fetch insufficient (%d chars), searching for '%s'",
                        len(full_text), title[:60])

            # Look up the source from the original article for a better query
            source_name = ""
            if hasattr(self, '_last_briefing_articles'):
                for a in self._last_briefing_articles:
                    if a.get("url") == url:
                        source_name = a.get("source", "")
                        break

            # Try DDG news search first, then general search
            all_results = []
            try:
                from ddgs import DDGS
                query = title[:100]
                if source_name:
                    query += f" {source_name}"
                with DDGS() as ddgs:
                    news_results = list(ddgs.news(query, max_results=5))
                for nr in news_results:
                    all_results.append({
                        "url": nr.get("url", nr.get("href", "")),
                        "title": nr.get("title", ""),
                    })
                logger.info("DDG news search returned %d results", len(all_results))
            except Exception as e:
                logger.warning("DDG news search failed: %s", e)

            # Also try general web search if news search returned few results
            if len(all_results) < 3:
                web_results = search_web(title[:100], max_results=5, timeout=10)
                all_results.extend(web_results)

            for sr in all_results:
                alt_url = sr.get("url", "")
                if not alt_url:
                    continue
                alt_domain = urlparse(alt_url).netloc.lower()
                # Skip the same URL, unfetchable domains, and Wikipedia
                if alt_url == url:
                    continue
                if any(d in alt_domain for d in self._UNFETCHABLE_DOMAINS):
                    continue
                if "wikipedia.org" in alt_domain:
                    continue
                logger.info("Trying alternative source: %s", alt_url)
                alt_page = fetch_page_rich(alt_url, max_chars=12000, timeout=15)
                if alt_page.get("success"):
                    alt_text = alt_page.get("text", "") or ""
                    if not alt_text and alt_page.get("html"):
                        alt_text = re.sub(r'<[^>]+>', ' ', alt_page["html"])
                        alt_text = re.sub(r'\s+', ' ', alt_text).strip()
                    if len(alt_text) > 200 and self._is_quality_content(alt_text, title):
                        logger.info("Alternative source succeeded: %d chars from %s",
                                    len(alt_text), alt_domain)
                        full_text = alt_text
                        break

        if not full_text or len(full_text) < 20:
            snippet = ""
            # Last resort: use the original snippet from the news listing
            if hasattr(self, '_last_briefing_articles'):
                for a in self._last_briefing_articles:
                    if a.get("url") == url:
                        snippet = a.get("snippet", "")
                        break
            full_text = snippet or "Article content could not be loaded from this source."
            logger.warning("All fetch attempts failed for: %s", url)

        return full_text

    def _build_article_detail(self, article):
        """Fetch article, summarize via Ollama, and return as intercepted response.

        Fetches the full article text (with search fallback for MSN etc.),
        then sends it to the local LLM with a focused summarization prompt
        to produce a clear summary with all key facts and figures.
        """
        url = article.get("url", "")
        title = article.get("title", "Untitled")
        source = article.get("source", "")

        full_text = self._fetch_article_text(url, title)

        # Summarize via Ollama
        summary = self._summarize_article(title, full_text)

        # Build formatted response
        lines = [f"**{title}**\n"]
        meta = []
        if source:
            meta.append(f"*{source}*")
        if url:
            meta.append(f"[Read full article]({url})")
        if meta:
            lines.append(" | ".join(meta) + "\n")
        lines.append("---\n")
        lines.append(summary)
        lines.append("\n\n---")
        lines.append('*Ask about another article by number, or say "what\'s the news" for fresh headlines.*')

        return "\n".join(lines)

    def _summarize_article(self, title, article_text):
        """Send article text to Ollama for a focused summary."""
        import ollama

        prompt = (
            "/no_think\n"
            "Summarize this news article as 4-6 bullet points.\n"
            "Each bullet: one concise factual sentence, max 25 words.\n"
            "Include key names, numbers, dates, and dollar amounts.\n"
            "Lead with the most important fact. No filler, no commentary.\n"
            "Do NOT start with 'This article' or 'The article'. Just state facts.\n\n"
            f"Article:\n{article_text[:5000]}\n\n"
            "Summary:"
        )

        try:
            model = CONFIG.get("model", {}).get("chat", "qwen3:8b")
            logger.info("Summarizing article '%s' (%d chars) via %s",
                        title[:50], len(article_text), model)
            response = ollama.chat(
                model=model,
                messages=[{"role": "user", "content": prompt}],
            )
            summary = response["message"]["content"]

            # Strip <think> tags from qwen3
            summary = re.sub(r'<think>.*?</think>', '', summary, flags=re.DOTALL).strip()

            if len(summary) < 50:
                logger.warning("Summary too short (%d chars), returning article text", len(summary))
                return article_text

            logger.info("Summary generated: %d chars", len(summary))
            return summary

        except Exception as e:
            logger.error("Ollama summarization failed: %s", e)
            return article_text

    # ------------------------------------------------------------------
    # Search formatting
    # ------------------------------------------------------------------

    def _format_search_results(self, query, results):
        """Format search results for context injection."""
        parts = [
            "Your web search subsystem just fetched these results from the internet.",
            "Use them to answer the user. Do NOT say you cannot access the web.",
            "",
            f"=== Search results for '{query}' ===",
        ]
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
