"""
Migration Script — Single-User to Multi-User
Moves existing data from flat data/ structure into per-user directories.
Run once after upgrading to multi-user Aegis.
"""

import json
import shutil
import sys
from getpass import getpass
from pathlib import Path

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DATA_DIR = PROJECT_ROOT / "data"
USERS_DIR = DATA_DIR / "users"
USERS_FILE = DATA_DIR / "users.json"
MIGRATED_MARKER = DATA_DIR / "MIGRATED.txt"


def migrate():
    """Run the migration from single-user to multi-user data layout."""
    print()
    print("=" * 50)
    print("  Aegis AI — Multi-User Migration")
    print("=" * 50)
    print()

    # Check if already migrated
    if MIGRATED_MARKER.exists():
        print("  Already migrated. Remove data/MIGRATED.txt to re-run.")
        return

    # Get username
    username = input("  Enter username for existing data [dustin]: ").strip().lower()
    if not username:
        username = "dustin"
    if not username.isalnum():
        print("  Error: Username must be alphanumeric.")
        return

    # Get display name
    display_name = input(f"  Display name [{username.title()}]: ").strip()
    if not display_name:
        display_name = username.title()

    # Get passcode
    passcode = getpass("  Set a passcode (min 4 chars): ")
    if len(passcode) < 4:
        print("  Error: Passcode must be at least 4 characters.")
        return
    passcode_confirm = getpass("  Confirm passcode: ")
    if passcode != passcode_confirm:
        print("  Error: Passcodes don't match.")
        return

    print()
    print(f"  Migrating data for user '{username}'...")

    # Create user directory
    user_dir = USERS_DIR / username
    subdirs = [
        "conversation_logs",
        "session_journals",
        "knowledge_base",
        "security_protocols",
    ]
    for subdir in subdirs:
        (user_dir / subdir).mkdir(parents=True, exist_ok=True)

    # Move profile
    old_profile = DATA_DIR / "user_profiles" / f"{username}.md"
    new_profile = user_dir / "profile.md"
    if old_profile.exists():
        shutil.copy2(str(old_profile), str(new_profile))
        print(f"  -- Copied profile: {old_profile.name} -> users/{username}/profile.md")

    # Move conversation logs
    old_logs = DATA_DIR / "conversation_logs"
    if old_logs.exists():
        for f in old_logs.glob("*.md"):
            shutil.copy2(str(f), str(user_dir / "conversation_logs" / f.name))
        count = len(list(old_logs.glob("*.md")))
        print(f"  -- Copied {count} conversation logs")

    # Move session journals
    old_journals = DATA_DIR / "session_journals"
    if old_journals.exists():
        for f in old_journals.glob("*.md"):
            shutil.copy2(str(f), str(user_dir / "session_journals" / f.name))
        count = len(list(old_journals.glob("*.md")))
        print(f"  -- Copied {count} session journals")

    # Move knowledge base
    old_kb = DATA_DIR / "knowledge_base"
    if old_kb.exists():
        new_kb = user_dir / "knowledge_base"
        # ChromaDB uses internal file structure, copy the whole directory
        for item in old_kb.iterdir():
            dest = new_kb / item.name
            if item.is_dir():
                if dest.exists():
                    shutil.rmtree(str(dest))
                shutil.copytree(str(item), str(dest))
            else:
                shutil.copy2(str(item), str(dest))
        print(f"  -- Copied knowledge base")

    # Move tasks
    old_tasks = DATA_DIR / "tasks.json"
    if old_tasks.exists():
        shutil.copy2(str(old_tasks), str(user_dir / "tasks.json"))
        print(f"  -- Copied tasks.json")

    # Move recurring
    old_recurring = DATA_DIR / "recurring.json"
    if old_recurring.exists():
        shutil.copy2(str(old_recurring), str(user_dir / "recurring.json"))
        print(f"  -- Copied recurring.json")

    # Move security protocols
    old_security = DATA_DIR / "security_protocols"
    if old_security.exists():
        for f in old_security.iterdir():
            if f.is_file():
                shutil.copy2(str(f), str(user_dir / "security_protocols" / f.name))
        print(f"  -- Copied security protocols")

    # Create users.json with hashed passcode
    import bcrypt as _bcrypt
    from datetime import datetime

    # Read pack preferences from existing config
    config_path = PROJECT_ROOT / "core" / "config" / "core_config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    packs = config.get("packs", {})

    users = {
        username: {
            "display_name": display_name,
            "passcode_hash": _bcrypt.hashpw(passcode.encode("utf-8"), _bcrypt.gensalt()).decode("utf-8"),
            "created": datetime.now().isoformat(),
            "preferences": {
                "active_personality": packs.get("active_personality", "default"),
                "active_voice": packs.get("active_voice", "default"),
                "active_theme": packs.get("active_theme", "default"),
            },
        }
    }
    USERS_FILE.write_text(
        json.dumps(users, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"  -- Created users.json")

    # Generate initial summary.json
    summary = {
        "user": username,
        "display_name": display_name,
        "generated": datetime.now().isoformat(),
        "key_facts": [],
        "recent_topics": [],
        "relationship_stage": "established" if new_profile.exists() else "new",
        "stats": {
            "total_sessions": len(list((user_dir / "conversation_logs").glob("*.md"))),
            "total_facts": 0,
        },
    }

    # Parse facts from profile if it exists
    if new_profile.exists():
        profile_text = new_profile.read_text(encoding="utf-8")
        facts = []
        for line in profile_text.split("\n"):
            line = line.strip()
            if line.startswith("- **") and ":**" in line:
                parts = line.split(":**", 1)
                if len(parts) == 2:
                    facts.append(parts[1].strip())
        summary["key_facts"] = facts[:20]
        summary["stats"]["total_facts"] = len(facts)

    (user_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"  -- Generated summary.json")

    # Write migration marker
    MIGRATED_MARKER.write_text(
        f"Migrated to multi-user on {datetime.now().isoformat()}\n"
        f"Primary user: {username}\n",
        encoding="utf-8",
    )

    print()
    print("  Migration complete.")
    print(f"  User '{username}' created with data at: data/users/{username}/")
    print()
    print("  NOTE: Original data files are preserved (copied, not moved).")
    print("  You can delete the old directories after verifying everything works.")
    print()


if __name__ == "__main__":
    migrate()
