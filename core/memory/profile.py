"""
User Profile — Aegis AI
Maintains running profiles of the human companion with auto-extracted facts.
Protected under Security Protocol — Classified by default.
"""

from datetime import datetime
from pathlib import Path
from core.config import CONFIG, get_path


def get_profile_path(user_name="dustin"):
    """Get the file path for a user's profile."""
    profile_dir = get_path(CONFIG, "user_profiles")
    profile_dir.mkdir(parents=True, exist_ok=True)
    return profile_dir / f"{user_name.lower()}.md"


def load_profile(user_name="dustin"):
    """Load a user's profile. Returns None if none exists."""
    filepath = get_profile_path(user_name)

    if filepath.exists():
        return filepath.read_text(encoding="utf-8")
    return None


def update_profile(new_facts, user_name="dustin"):
    """Update a user's profile with newly extracted facts."""
    filepath = get_profile_path(user_name)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    if filepath.exists():
        existing = filepath.read_text(encoding="utf-8")
    else:
        existing = _create_blank_profile(user_name)

    if not new_facts:
        return filepath

    # Organize facts by category
    categorized = {}
    for fact_entry in new_facts:
        cat = fact_entry["category"]
        fact = fact_entry["fact"]
        if cat not in categorized:
            categorized[cat] = []
        categorized[cat].append(fact)

    # Load existing facts to check for duplicates
    existing_facts = _parse_existing_facts(existing)

    # Append new unique facts
    additions = []
    for cat, facts in categorized.items():
        for fact in facts:
            if not _is_duplicate(fact, existing_facts):
                additions.append({"category": cat, "fact": fact})

    if not additions:
        return filepath

    # Append to the profile file
    new_lines = [f"\n### Update — {now}"]
    for entry in additions:
        new_lines.append(f"- **{entry['category']}:** {entry['fact']}")

    updated_content = existing + "\n" + "\n".join(new_lines) + "\n"
    filepath.write_text(updated_content, encoding="utf-8")

    return filepath


def get_profile_summary(user_name="dustin"):
    """Get a concise text version of the profile for injecting into context."""
    content = load_profile(user_name)
    if content is None:
        return "No user profile on file."
    return content


def _create_blank_profile(user_name):
    """Create a new blank profile for a user."""
    filepath = get_profile_path(user_name)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    agent_name = CONFIG.get("agent_name", "Aegis")

    content = f"""# User Profile — {user_name.title()}
**Classification:** CONFIDENTIAL — {agent_name} Eyes Only
**Created:** {now}

> This profile is maintained by {agent_name} and protected under
> Security Protocol. Contents are never shared with external agents
> or services without explicit authorization from the user.

---

## Known Facts
"""
    filepath.write_text(content, encoding="utf-8")
    return content


def _parse_existing_facts(profile_text):
    """Extract existing fact strings from a profile for duplicate checking."""
    facts = []
    for line in profile_text.split("\n"):
        line = line.strip()
        if line.startswith("- **") and ":**" in line:
            fact_part = line.split(":**", 1)
            if len(fact_part) == 2:
                facts.append(fact_part[1].strip().lower())
    return facts


def _is_duplicate(new_fact, existing_facts):
    """Check if a fact is already recorded (simple substring match)."""
    new_lower = new_fact.lower().strip()
    for existing in existing_facts:
        if new_lower in existing or existing in new_lower:
            return True
    return False
