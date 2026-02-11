"""
User Profile — Aegis AI
Maintains running profiles of the human companion with auto-extracted facts.
Protected under Security Protocol — Classified by default.
"""

from datetime import datetime
from pathlib import Path
from core.config import CONFIG, get_path


def get_profile_path(user_name="default", data_dir=None):
    """Get the file path for a user's profile."""
    if data_dir is not None:
        data_dir = Path(data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        return data_dir / "profile.md"
    # Legacy behavior — flat directory
    profile_dir = get_path(CONFIG, "user_profiles")
    profile_dir.mkdir(parents=True, exist_ok=True)
    return profile_dir / f"{user_name.lower()}.md"


def load_profile(user_name="default", data_dir=None):
    """Load a user's profile. Returns None if none exists."""
    filepath = get_profile_path(user_name, data_dir=data_dir)

    if filepath.exists():
        return filepath.read_text(encoding="utf-8")
    return None


def update_profile(new_facts, user_name="default", data_dir=None):
    """Update a user's profile with newly extracted facts."""
    filepath = get_profile_path(user_name, data_dir=data_dir)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    if filepath.exists():
        existing = filepath.read_text(encoding="utf-8")
    else:
        existing = _create_blank_profile(user_name, data_dir=data_dir)

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


def get_profile_summary(user_name="default", data_dir=None):
    """Get a concise text version of the profile for injecting into context."""
    content = load_profile(user_name, data_dir=data_dir)
    if content is None:
        return "No user profile on file."
    return content


def get_profile_facts(user_name="default", data_dir=None):
    """Get structured list of facts from the profile."""
    content = load_profile(user_name, data_dir=data_dir)
    if content is None:
        return []

    facts = []
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("- **") and ":**" in line:
            parts = line.split(":**", 1)
            if len(parts) == 2:
                category = parts[0].lstrip("- *").rstrip("*").strip()
                fact_text = parts[1].strip()
                facts.append({"category": category, "fact": fact_text})
    return facts


def remove_profile_fact(fact_text, user_name="default", data_dir=None):
    """Remove a specific fact from the profile by its text."""
    filepath = get_profile_path(user_name, data_dir=data_dir)
    if not filepath.exists():
        return False

    content = filepath.read_text(encoding="utf-8")
    lines = content.split("\n")
    new_lines = []
    removed = False

    for line in lines:
        if line.strip().startswith("- **") and fact_text in line:
            removed = True
            continue
        new_lines.append(line)

    if removed:
        filepath.write_text("\n".join(new_lines), encoding="utf-8")
    return removed


def _create_blank_profile(user_name, data_dir=None):
    """Create a new blank profile for a user."""
    filepath = get_profile_path(user_name, data_dir=data_dir)
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
