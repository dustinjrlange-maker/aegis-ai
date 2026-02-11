"""
Fact Store — Aegis AI
Structured, deduplicated fact storage with contradiction detection.
Replaces the append-only profile.md system with keyed facts that
can be updated, merged, and corrected over time.
"""

import json
import re
from datetime import datetime
from pathlib import Path
from difflib import SequenceMatcher


# Fields where a new value should automatically replace the old one.
# These are inherently temporal — "current job" changes over time.
TEMPORAL_FIELDS = {
    "occupation.current", "location.current", "emotional_state",
    "life_events.current", "goals.current",
}

# Fields that are additive — new values get merged in, not replaced.
# e.g., liking "chicken fried rice" AND "caesar salad" are both valid.
ADDITIVE_PREFIXES = {
    "preferences.", "relationships.", "goals.",
}

# Fields that are permanent or rarely change — flag conflicts for review.
IDENTITY_FIELDS = {
    "identity.name", "identity.age", "identity.nationality",
}

# Categories that should NOT be stored as long-term profile facts.
TRANSIENT_CATEGORIES = {"EMOTIONAL_STATE"}


class FactStore:
    """Manages a structured JSON fact store for a single user."""

    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.facts_path = self.data_dir / "facts.json"
        self._data = None
        self._load()

    def _load(self):
        """Load fact store from disk."""
        if self.facts_path.exists():
            try:
                self._data = json.loads(
                    self.facts_path.read_text(encoding="utf-8")
                )
            except (json.JSONDecodeError, IOError):
                self._data = self._blank()
        else:
            self._data = self._blank()

    def _blank(self):
        return {
            "version": 1,
            "facts": {},
            "pending_review": [],
            "metadata": {
                "last_updated": None,
                "total_ingestions": 0,
            },
        }

    def _save(self):
        """Persist to disk."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._data["metadata"]["last_updated"] = datetime.now().isoformat()
        self.facts_path.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------
    # Ingestion — the core of the hygiene system
    # ------------------------------------------------------------------

    def ingest(self, keyed_facts, session_id=None):
        """Ingest a list of keyed facts from the extractor.

        Each fact is a dict: {"key": "identity.name", "value": "Dustin"}
        Returns a report dict: {added, updated, duplicates, conflicts}
        """
        now = datetime.now().isoformat()
        if session_id is None:
            session_id = datetime.now().strftime("%Y-%m-%d_%H%M%S")

        report = {"added": 0, "updated": 0, "duplicates": 0, "conflicts": 0}
        self._data["metadata"]["total_ingestions"] += 1

        for fact in keyed_facts:
            key = fact.get("key", "").strip().lower()
            value = fact.get("value", "").strip()

            if not key or not value:
                continue

            # Skip transient emotional states as profile facts
            category = key.split(".")[0].upper() if "." in key else key.upper()
            if category in TRANSIENT_CATEGORIES:
                continue

            # Filter out meta-observations (LLM analyzing itself)
            if self._is_meta_observation(value):
                continue

            existing = self._data["facts"].get(key)
            is_additive = any(key.startswith(p) for p in ADDITIVE_PREFIXES)

            if existing is None:
                # Brand new fact
                self._data["facts"][key] = {
                    "value": value,
                    "confidence": 1,
                    "first_seen": now,
                    "last_confirmed": now,
                    "source_sessions": [session_id],
                    "history": [],
                }
                report["added"] += 1

            elif self._is_same_fact(existing["value"], value):
                # Duplicate — boost confidence
                existing["confidence"] += 1
                existing["last_confirmed"] = now
                if session_id not in existing["source_sessions"]:
                    existing["source_sessions"].append(session_id)
                report["duplicates"] += 1

            elif is_additive:
                # Additive field — merge new info into existing value
                merged = self._merge_additive(existing["value"], value)
                if merged != existing["value"]:
                    existing["value"] = merged
                    existing["confidence"] += 1
                    existing["last_confirmed"] = now
                    if session_id not in existing["source_sessions"]:
                        existing["source_sessions"].append(session_id)
                    report["updated"] += 1
                else:
                    report["duplicates"] += 1

            elif key in TEMPORAL_FIELDS:
                # Temporal field — auto-update, archive old value
                existing["history"].append({
                    "value": existing["value"],
                    "replaced": now,
                    "session": session_id,
                })
                existing["value"] = value
                existing["confidence"] = 1
                existing["last_confirmed"] = now
                if session_id not in existing["source_sessions"]:
                    existing["source_sessions"].append(session_id)
                report["updated"] += 1

            elif key == "identity.name":
                # Name field — handle aliases (e.g., "Dustin" and "Switch")
                self._handle_name_update(existing, value, now, session_id)
                report["updated"] += 1

            else:
                # Potential conflict — flag for review
                self._data["pending_review"].append({
                    "key": key,
                    "existing_value": existing["value"],
                    "new_value": value,
                    "detected": now,
                    "session": session_id,
                    "resolved": False,
                })
                report["conflicts"] += 1

        self._save()
        return report

    def _is_meta_observation(self, value):
        """Detect LLM meta-observations that aren't real facts."""
        meta_patterns = [
            r"not explicitly (?:named|stated|mentioned)",
            r"referred to as",
            r"no (?:family|friends|relationships) (?:are )?mentioned",
            r"none of the .* mentioned",
            r"crew member (?:is|works)",
            r"likely related to",
        ]
        lower = value.lower()
        for pattern in meta_patterns:
            if re.search(pattern, lower):
                return True
        return False

    def _is_same_fact(self, a, b):
        """Check if two fact values are semantically the same."""
        a_lower = a.lower().strip().rstrip(".")
        b_lower = b.lower().strip().rstrip(".")

        # Exact match
        if a_lower == b_lower:
            return True

        # One contains the other
        if a_lower in b_lower or b_lower in a_lower:
            return True

        # High string similarity
        ratio = SequenceMatcher(None, a_lower, b_lower).ratio()
        if ratio > 0.80:
            return True

        # Entity-based matching: extract key entities (names, numbers) and compare
        a_entities = self._extract_entities(a_lower)
        b_entities = self._extract_entities(b_lower)
        if a_entities and b_entities and a_entities == b_entities:
            return True

        return False

    def _extract_entities(self, text):
        """Extract key entities (proper nouns, numbers) from text for comparison."""
        # Find capitalized words (proper nouns) and numbers
        entities = set()
        # Names: words that start with uppercase in the original
        for word in text.split():
            word = word.strip(".,;:!?\"'()-")
            if not word:
                continue
            # Numbers
            if word.isdigit():
                entities.add(word)
            # Short significant words (likely names)
            elif len(word) > 2 and word.isalpha():
                entities.add(word)
        return entities

    def _merge_additive(self, existing_value, new_value):
        """Merge new information into an additive field.

        Appends genuinely new content while avoiding redundancy.
        """
        existing_lower = existing_value.lower()
        new_lower = new_value.lower()

        # If the new value is already contained in existing, no change
        if new_lower in existing_lower:
            return existing_value

        # Extract individual items from new value (split on ; or ,)
        new_items = re.split(r'[;,]', new_value)
        new_items = [item.strip() for item in new_items if item.strip()]

        additions = []
        for item in new_items:
            item_lower = item.lower()
            # Check if this specific item is already in existing
            if item_lower not in existing_lower:
                # Also check word overlap — if 80%+ of words match, skip
                item_words = set(item_lower.split())
                existing_words = set(existing_lower.split())
                if item_words and len(item_words & existing_words) / len(item_words) < 0.7:
                    additions.append(item)

        if not additions:
            return existing_value

        return existing_value + "; " + "; ".join(additions)

    def _handle_name_update(self, existing, new_value, now, session_id):
        """Handle identity.name updates — store aliases instead of conflicting."""
        existing_name = existing["value"].lower().strip()
        new_name = new_value.lower().strip()

        # Strip common prefixes from LLM output
        for prefix in ["companion's name is ", "name is ", "name: ", "goes by "]:
            if new_name.startswith(prefix):
                new_name = new_name[len(prefix):].strip()

        # If it's a genuinely different name, treat as alias
        aliases = existing.get("aliases", [])
        clean_name = new_name.strip().title()  # Store cleaned name, not raw LLM output
        if new_name != existing_name and new_name not in [a.lower() for a in aliases]:
            aliases.append(clean_name)
            existing["aliases"] = aliases
        existing["last_confirmed"] = now
        if session_id not in existing["source_sessions"]:
            existing["source_sessions"].append(session_id)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get_all_facts(self):
        """Get all current facts as a list."""
        result = []
        for key, data in self._data["facts"].items():
            result.append({
                "key": key,
                "value": data["value"],
                "confidence": data["confidence"],
                "first_seen": data["first_seen"],
                "last_confirmed": data["last_confirmed"],
            })
        return result

    def get_fact(self, key):
        """Get a specific fact by key."""
        return self._data["facts"].get(key)

    def get_pending_review(self):
        """Get unresolved contradictions."""
        return [r for r in self._data["pending_review"] if not r["resolved"]]

    def resolve_conflict(self, index, keep="new"):
        """Resolve a pending conflict.

        keep="new" — accept the new value
        keep="existing" — keep the old value
        keep="both" — store new value under a different key suffix
        """
        if index >= len(self._data["pending_review"]):
            return False

        conflict = self._data["pending_review"][index]
        key = conflict["key"]
        existing = self._data["facts"].get(key)

        if keep == "new" and existing:
            existing["history"].append({
                "value": existing["value"],
                "replaced": datetime.now().isoformat(),
                "session": conflict["session"],
                "reason": "conflict_resolved_new",
            })
            existing["value"] = conflict["new_value"]
            existing["confidence"] = 1
            existing["last_confirmed"] = datetime.now().isoformat()
        # "existing" — just mark resolved, keep current value

        conflict["resolved"] = True
        conflict["resolution"] = keep
        self._save()
        return True

    # ------------------------------------------------------------------
    # Profile rendering — generate clean context for the LLM
    # ------------------------------------------------------------------

    # Categories ordered by importance for context injection.
    # Identity/biographical facts are essential; hobbies/dreams are not.
    PROFILE_PRIORITY = [
        "identity", "relationships", "location", "occupation",
        "goals", "preferences", "life_events", "other",
    ]

    def render_profile(self, companion_name="They", max_facts=12):
        """Render the fact store as a concise profile string for context injection.

        Uses explicit companion attribution so the model never confuses
        the companion's facts with its own character traits.
        Prioritizes biographical facts over preferences/hobbies.
        Capped at 12 to avoid overwhelming 8B models.
        """
        if not self._data["facts"]:
            return ""

        # Group facts by category priority
        by_category = {}
        for key, data in self._data["facts"].items():
            category = key.split(".")[0] if "." in key else key
            if category not in by_category:
                by_category[category] = []
            by_category[category].append({
                "key": key,
                "value": data["value"],
                "confidence": data["confidence"],
            })

        # Build ordered list: priority categories first, then by confidence
        facts_list = []
        for cat in self.PROFILE_PRIORITY:
            cat_facts = by_category.pop(cat, [])
            cat_facts.sort(key=lambda f: f["confidence"], reverse=True)
            facts_list.extend(cat_facts)
        # Any remaining categories
        for cat_facts in by_category.values():
            cat_facts.sort(key=lambda f: f["confidence"], reverse=True)
            facts_list.extend(cat_facts)

        facts_list = facts_list[:max_facts]

        # Render with companion-attributed natural language
        name = companion_name
        lines = []
        for f in facts_list:
            key = f["key"]
            val = f["value"]
            # Truncate long values
            if len(val) > 80:
                val = val[:77] + "..."
            line = self._attribute_fact(key, val, name)
            if line:
                lines.append(f"- {line}")

        # Add name aliases if any
        name_fact = self._data["facts"].get("identity.name")
        if name_fact and name_fact.get("aliases"):
            lines.append(f"- {name} also goes by: {', '.join(name_fact['aliases'])}")

        return "\n".join(lines)

    def _attribute_fact(self, key, value, name):
        """Convert a keyed fact into a companion-attributed sentence."""
        prefix_map = {
            "identity.name": f"{name}'s name is",
            "identity.age": f"{name} is",
            "identity.nationality": f"{name}'s nationality:",
            "location.current": f"{name} lives in",
            "occupation.current": f"{name} works on",
            "occupation.title": f"{name}'s job title:",
            "occupation.project": f"{name}'s work project:",
            "relationships.partner": f"{name}'s partner:",
            "relationships.family": f"{name}'s family:",
            "relationships.roommates": f"{name}'s roommates:",
            "relationships.pets": f"{name}'s pets:",
            "preferences.food": f"{name} likes to eat",
            "preferences.hobbies": f"{name}'s hobbies/interests:",
            "preferences.tech": f"{name}'s tech interests:",
            "preferences.books": f"{name} reads",
            "goals.project": f"{name}'s project goals:",
            "goals.financial": f"{name}'s money goals:",
            "goals.relocation": f"{name}'s moving plans:",
            "goals.streaming": f"{name}'s streaming goals:",
        }

        # Check exact key match first
        prefix = prefix_map.get(key)
        if prefix:
            return f"{prefix} {value}"

        # Fall back to category-based prefix
        category = key.split(".")[0] if "." in key else key
        category_map = {
            "identity": f"{name}'s identity:",
            "location": f"{name}'s location:",
            "occupation": f"{name}'s work:",
            "relationships": f"{name}'s relationships:",
            "preferences": f"{name} likes",
            "goals": f"{name}'s goals:",
            "life_events": f"{name}'s life:",
        }

        prefix = category_map.get(category, f"About {name}:")
        return f"{prefix} {value}"

    def render_profile_markdown(self):
        """Render a full markdown profile (for vault display)."""
        if not self._data["facts"]:
            return "No facts recorded yet."

        by_category = {}
        for key, data in self._data["facts"].items():
            category = key.split(".")[0].upper() if "." in key else key.upper()
            if category not in by_category:
                by_category[category] = []
            by_category[category].append({
                "key": key,
                "value": data["value"],
                "confidence": data["confidence"],
                "first_seen": data.get("first_seen", ""),
            })

        lines = ["# Companion Profile\n"]
        for cat, facts in sorted(by_category.items()):
            lines.append(f"## {cat}")
            for f in facts:
                conf_stars = min(f["confidence"], 5)
                conf_indicator = "*" * conf_stars
                lines.append(f"- {f['value']} ({conf_indicator})")
            lines.append("")

        pending = self.get_pending_review()
        if pending:
            lines.append("## Needs Review")
            for p in pending:
                lines.append(
                    f"- **{p['key']}**: \"{p['existing_value']}\" vs \"{p['new_value']}\""
                )
            lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Migration — import from legacy profile.md
    # ------------------------------------------------------------------

    def migrate_from_profile_md(self, profile_path):
        """Parse an existing profile.md and import unique facts."""
        profile_path = Path(profile_path)
        if not profile_path.exists():
            return {"imported": 0, "skipped": 0}

        content = profile_path.read_text(encoding="utf-8")
        facts = []

        for line in content.split("\n"):
            line = line.strip()
            if not line.startswith("- **") or ":**" not in line:
                continue

            parts = line.split(":**", 1)
            if len(parts) != 2:
                continue

            category = parts[0].lstrip("- *").rstrip("*").strip().upper()
            value = parts[1].strip()

            if not value:
                continue

            # Map old category to keyed format
            key = self._category_to_key(category, value)
            facts.append({"key": key, "value": value})

        report = self.ingest(facts, session_id="migration")
        return {
            "imported": report["added"] + report["updated"],
            "skipped": report["duplicates"],
        }

    def _category_to_key(self, category, value):
        """Map legacy flat categories to hierarchical keys."""
        category = category.upper()
        value_lower = value.lower()

        mapping = {
            "NAME": "identity.name",
            "IDENTITY": "identity.name",
            "AGE": "identity.age",
            "LOCATION": "location.current",
            "NATIONALITY": "identity.nationality",
            "OCCUPATION": "occupation.current",
            "JOB TITLE": "occupation.title",
            "JOB_TITLE": "occupation.title",
            "PROJECT/SEASON": "occupation.project",
            "RELATIONSHIPS": "relationships.info",
            "PREFERENCES": "preferences.general",
            "FOOD": "preferences.food",
            "LIFE EVENTS": "life_events.current",
            "LIFE_EVENTS": "life_events.current",
            "GOALS": "goals.current",
            "EMOTIONAL STATE": "emotional_state",
            "EMOTIONAL_STATE": "emotional_state",
            "MOVES": "life_events.moves",
            "ROOMMATES": "relationships.roommates",
        }

        # Try to find more specific keys based on content
        if category in ("PREFERENCES", "FOOD"):
            if any(w in value_lower for w in ["food", "eat", "meal", "lunch", "dinner", "breakfast", "salad", "rice"]):
                return "preferences.food"
            if any(w in value_lower for w in ["hobby", "interest", "enjoy", "like"]):
                return "preferences.hobbies"
            if any(w in value_lower for w in ["book", "read", "scifi", "sci-fi"]):
                return "preferences.books"
            if any(w in value_lower for w in ["program", "python", "code"]):
                return "preferences.tech"

        if category == "RELATIONSHIPS":
            if "partner" in value_lower or "tyler" in value_lower:
                return "relationships.partner"
            if "roommate" in value_lower:
                return "relationships.roommates"
            if "cousin" in value_lower:
                return "relationships.family"
            if "dog" in value_lower or "pet" in value_lower or "kallie" in value_lower:
                return "relationships.pets"

        if category == "GOALS":
            # Create unique keys for different goals
            if "stream" in value_lower or "twitch" in value_lower:
                return "goals.streaming"
            if "move" in value_lower or "america" in value_lower or "usa" in value_lower:
                return "goals.relocation"
            if "save" in value_lower or "money" in value_lower:
                return "goals.financial"
            if "aegis" in value_lower or "project" in value_lower or "pike" in value_lower:
                return "goals.project"

        base = mapping.get(category, f"other.{category.lower()}")

        # For categories that can have multiple entries, add a content-based suffix
        if base in ("preferences.general", "life_events.current", "relationships.info"):
            # Use first few significant words as a suffix
            words = [w for w in value_lower.split()[:3] if len(w) > 3]
            if words:
                suffix = "_".join(words[:2])
                suffix = re.sub(r'[^a-z0-9_]', '', suffix)
                return f"{base}.{suffix}"

        return base
