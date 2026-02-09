"""
Aegis AI — Pack CLI
Create, validate, and manage personality/voice/theme packs.

Usage:
    python -m tools.pack_cli init <type> <name>    Create a new pack scaffold
    python -m tools.pack_cli validate <path>       Validate a pack directory
    python -m tools.pack_cli validate-all          Validate all installed packs
    python -m tools.pack_cli list                  List all installed packs
    python -m tools.pack_cli info <type> <name>    Show pack details

Types: personality, voice, theme
"""

import json
import sys
from pathlib import Path

# Resolve project root
TOOLS_DIR = Path(__file__).parent
PROJECT_ROOT = TOOLS_DIR.parent
PACKS_ROOT = PROJECT_ROOT / "packs"

from tools.pack_validator import validate_pack, validate_all_packs


# --- Pack Templates ---

PERSONALITY_MANIFEST = {
    "name": "",
    "character_name": "",
    "author": "",
    "version": "1.0.0",
    "description": "",
    "tags": [],
    "compatibility": ">=1.0.0",
}

PERSONALITY_TXT = """=== CHARACTER ===
You are {name}.

=== RESPONSE STYLE ===
- Keep responses concise (1-3 sentences for casual, more for complex topics)
- Match the user's energy level

=== WHAT YOU DO ===
- Engage in genuine conversation
- Provide practical help with tasks

=== WHAT YOU NEVER DO ===
- Break character without reason
- Provide harmful or dangerous information
"""

PERSONALITY_OVERLAY = {
    "agent_display_name": "",
    "banner": "",
    "terminology": {},
}

PERSONALITY_FILLER = {
    "phrases": [],
    "word_replacements": {},
}

BACKSTORY_TEMPLATE = {
    "description": "Core backstory for this character",
    "memories": [
        {
            "content": "Replace with a character backstory fact.",
            "weight": "secondary",
            "tags": ["backstory", "origin"],
        }
    ],
}

VOICE_MANIFEST = {
    "name": "",
    "author": "",
    "version": "1.0.0",
    "description": "",
    "compatibility": ">=1.0.0",
}

VOICE_CONFIG = {
    "language": "en",
    "speed": 1.0,
    "notes": "Describe the voice character here.",
}

THEME_MANIFEST = {
    "name": "",
    "author": "",
    "version": "1.0.0",
    "description": "",
    "compatibility": ">=1.0.0",
}

THEME_JSON = {
    "name": "",
    "colors": {
        "primary": "#2563eb",
        "secondary": "#1e40af",
        "background": "#0f172a",
        "surface": "#1e293b",
        "text": "#f1f5f9",
        "accent": "#38bdf8",
        "success": "#22c55e",
        "warning": "#f59e0b",
        "error": "#ef4444",
    },
    "fonts": {
        "primary": "Inter, system-ui, sans-serif",
        "mono": "JetBrains Mono, Consolas, monospace",
    },
    "terminology": {
        "agent_label": "",
        "companion_label": "You",
        "status_header": "SYSTEMS STATUS",
    },
}


# --- Commands ---

def cmd_init(pack_type, name):
    """Scaffold a new pack."""
    type_map = {
        "personality": "personalities",
        "voice": "voices",
        "theme": "themes",
    }

    if pack_type not in type_map:
        print(f"  Unknown type: {pack_type}. Must be: personality, voice, theme")
        return 1

    pack_dir = PACKS_ROOT / type_map[pack_type] / name

    if pack_dir.exists():
        print(f"  Pack already exists: {pack_dir}")
        return 1

    pack_dir.mkdir(parents=True, exist_ok=True)
    print(f"  Creating {pack_type} pack: {name}")

    if pack_type == "personality":
        _init_personality(pack_dir, name)
    elif pack_type == "voice":
        _init_voice(pack_dir, name)
    elif pack_type == "theme":
        _init_theme(pack_dir, name)

    print(f"  Pack created at: {pack_dir}")
    print(f"  Next steps:")
    print(f"    1. Edit manifest.json with your pack details")

    if pack_type == "personality":
        print(f"    2. Write your character in personality.txt")
        print(f"    3. Add character memories in memories/*.json")
        print(f"    4. Optionally add filler_phrases.json and config_overlay.json")
    elif pack_type == "voice":
        print(f"    2. Add a reference.wav (10-20s of clear speech)")
        print(f"    3. Adjust voice_config.json settings")
    elif pack_type == "theme":
        print(f"    2. Customize colors and fonts in theme.json")

    print(f"    Run: python -m tools.pack_cli validate {pack_dir}")
    return 0


def _init_personality(pack_dir, name):
    """Create personality pack scaffold."""
    display_name = name.replace("-", " ").replace("_", " ").title()

    manifest = PERSONALITY_MANIFEST.copy()
    manifest["name"] = display_name
    manifest["character_name"] = display_name
    manifest["author"] = "Your Name"
    manifest["description"] = f"A custom personality pack for Aegis AI."

    _write_json(pack_dir / "manifest.json", manifest)
    _write_text(pack_dir / "personality.txt", PERSONALITY_TXT.format(name=display_name))

    overlay = PERSONALITY_OVERLAY.copy()
    overlay["agent_display_name"] = display_name
    _write_json(pack_dir / "config_overlay.json", overlay)
    _write_json(pack_dir / "filler_phrases.json", PERSONALITY_FILLER)

    memories_dir = pack_dir / "memories"
    memories_dir.mkdir(exist_ok=True)
    _write_json(memories_dir / "backstory.json", BACKSTORY_TEMPLATE)

    print(f"    Created: manifest.json")
    print(f"    Created: personality.txt")
    print(f"    Created: config_overlay.json")
    print(f"    Created: filler_phrases.json")
    print(f"    Created: memories/backstory.json")


def _init_voice(pack_dir, name):
    """Create voice pack scaffold."""
    display_name = name.replace("-", " ").replace("_", " ").title()

    manifest = VOICE_MANIFEST.copy()
    manifest["name"] = f"{display_name} Voice"
    manifest["author"] = "Your Name"
    manifest["description"] = f"Voice pack for {display_name}."

    _write_json(pack_dir / "manifest.json", manifest)
    _write_json(pack_dir / "voice_config.json", VOICE_CONFIG)

    print(f"    Created: manifest.json")
    print(f"    Created: voice_config.json")
    print(f"    Note: Add a reference.wav file (10-20s clear speech sample)")


def _init_theme(pack_dir, name):
    """Create theme pack scaffold."""
    display_name = name.replace("-", " ").replace("_", " ").title()

    manifest = THEME_MANIFEST.copy()
    manifest["name"] = display_name
    manifest["author"] = "Your Name"
    manifest["description"] = f"A custom theme for Aegis AI."

    theme = THEME_JSON.copy()
    theme["name"] = display_name
    theme["terminology"]["agent_label"] = display_name

    _write_json(pack_dir / "manifest.json", manifest)
    _write_json(pack_dir / "theme.json", theme)

    print(f"    Created: manifest.json")
    print(f"    Created: theme.json")


def cmd_validate(path):
    """Validate a single pack."""
    pack_path = Path(path)
    if not pack_path.is_absolute():
        pack_path = Path.cwd() / pack_path

    print(f"\n  Validating: {pack_path.name}")
    result = validate_pack(pack_path)
    print(result.summary())
    return 0 if result.valid else 1


def cmd_validate_all():
    """Validate all installed packs."""
    print(f"\n  Validating all packs in: {PACKS_ROOT}\n")
    results = validate_all_packs(PACKS_ROOT)

    all_valid = True
    for key, result in results.items():
        print(f"  --- {key} ---")
        print(result.summary())
        print()
        if not result.valid:
            all_valid = False

    total = len(results)
    valid = sum(1 for r in results.values() if r.valid)
    print(f"  Summary: {valid}/{total} packs valid.")
    return 0 if all_valid else 1


def cmd_list():
    """List all installed packs."""
    print("\n  Installed Packs:")
    for type_name in ["personalities", "voices", "themes"]:
        type_dir = PACKS_ROOT / type_name
        if not type_dir.exists():
            continue
        print(f"\n  {type_name.upper()}:")
        for pack_dir in sorted(type_dir.iterdir()):
            if pack_dir.is_dir():
                manifest_path = pack_dir / "manifest.json"
                if manifest_path.exists():
                    try:
                        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                        name = manifest.get("name", pack_dir.name)
                        version = manifest.get("version", "?")
                        desc = manifest.get("description", "")
                        print(f"    {pack_dir.name:20s} v{version:8s} {name} — {desc[:50]}")
                    except Exception:
                        print(f"    {pack_dir.name:20s} (invalid manifest)")
                else:
                    print(f"    {pack_dir.name:20s} (no manifest)")
    print()
    return 0


def cmd_info(pack_type, name):
    """Show detailed pack info."""
    type_map = {
        "personality": "personalities",
        "voice": "voices",
        "theme": "themes",
    }

    if pack_type not in type_map:
        print(f"  Unknown type: {pack_type}")
        return 1

    pack_dir = PACKS_ROOT / type_map[pack_type] / name
    if not pack_dir.exists():
        print(f"  Pack not found: {pack_dir}")
        return 1

    manifest_path = pack_dir / "manifest.json"
    if not manifest_path.exists():
        print(f"  No manifest.json found.")
        return 1

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    print(f"\n  Pack: {manifest.get('name', name)}")
    print(f"  Type: {pack_type}")
    print(f"  Author: {manifest.get('author', 'Unknown')}")
    print(f"  Version: {manifest.get('version', '?')}")
    print(f"  Description: {manifest.get('description', 'None')}")

    if manifest.get("disclaimer"):
        print(f"  Disclaimer: {manifest['disclaimer']}")
    if manifest.get("tags"):
        print(f"  Tags: {', '.join(manifest['tags'])}")

    print(f"\n  Files:")
    for f in sorted(pack_dir.rglob("*")):
        if f.is_file():
            rel = f.relative_to(pack_dir)
            size = f.stat().st_size
            print(f"    {str(rel):30s} ({size:,d} bytes)")

    # Memory stats for personality packs
    if pack_type == "personality":
        memories_dir = pack_dir / "memories"
        if memories_dir.exists():
            total_memories = 0
            core_memories = 0
            for mem_file in memories_dir.glob("*.json"):
                try:
                    data = json.loads(mem_file.read_text(encoding="utf-8"))
                    mems = data.get("memories", [])
                    total_memories += len(mems)
                    core_memories += sum(1 for m in mems if m.get("weight") == "core")
                except Exception:
                    pass
            print(f"\n  Character Memories: {total_memories} total, {core_memories} core")

    print()

    # Run validation
    result = validate_pack(pack_dir)
    print(f"  Validation:")
    print(result.summary())
    print()
    return 0


def _write_json(path, data):
    """Write JSON file with consistent formatting."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
        f.write("\n")


def _write_text(path, text):
    """Write text file."""
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 0

    cmd = args[0].lower()

    if cmd == "init" and len(args) >= 3:
        return cmd_init(args[1].lower(), args[2])
    elif cmd == "validate" and len(args) >= 2:
        return cmd_validate(args[1])
    elif cmd == "validate-all":
        return cmd_validate_all()
    elif cmd == "list":
        return cmd_list()
    elif cmd == "info" and len(args) >= 3:
        return cmd_info(args[1].lower(), args[2])
    else:
        print(__doc__)
        return 1


if __name__ == "__main__":
    sys.exit(main() or 0)
