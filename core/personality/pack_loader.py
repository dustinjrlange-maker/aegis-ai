"""
Pack Loader — Aegis AI
Loads and merges personality packs, voice packs, and theme packs
on top of the core Aegis directives.
"""

import json
from pathlib import Path
from core.config import CONFIG, PROJECT_ROOT


def get_pack_dir():
    """Get the root packs directory."""
    pack_dir_name = CONFIG.get("packs", {}).get("pack_directory", "packs")
    return PROJECT_ROOT / pack_dir_name


def load_personality_pack(pack_name=None):
    """Load a personality pack by name.

    Returns a dict with:
        - manifest: pack metadata
        - personality: personality overlay text (or empty string)
        - filler_phrases: list of phrases to filter (or empty list)
        - config_overlay: config overrides (or empty dict)
        - memories: dict of memory files (or empty dict)
    """
    if pack_name is None:
        pack_name = CONFIG.get("packs", {}).get("active_personality", "default")

    pack_dir = get_pack_dir() / "personalities" / pack_name

    result = {
        "name": pack_name,
        "manifest": {},
        "personality": "",
        "filler_phrases": [],
        "config_overlay": {},
        "memories": {},
    }

    if not pack_dir.exists():
        print(f"  [Warning: Personality pack '{pack_name}' not found at {pack_dir}]")
        return result

    # Load manifest
    manifest_path = pack_dir / "manifest.json"
    if manifest_path.exists():
        with open(manifest_path, "r", encoding="utf-8") as f:
            result["manifest"] = json.load(f)

    # Load personality overlay
    personality_path = pack_dir / "personality.txt"
    if personality_path.exists():
        result["personality"] = personality_path.read_text(encoding="utf-8")

    # Load filler phrases
    filler_path = pack_dir / "filler_phrases.json"
    if filler_path.exists():
        with open(filler_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            result["filler_phrases"] = data.get("phrases", data) if isinstance(data, dict) else data

    # Load config overlay
    config_path = pack_dir / "config_overlay.json"
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            result["config_overlay"] = json.load(f)

    # Load character memories
    memories_dir = pack_dir / "memories"
    if memories_dir.exists():
        for mem_file in memories_dir.glob("*.json"):
            with open(mem_file, "r", encoding="utf-8") as f:
                result["memories"][mem_file.stem] = json.load(f)

    return result


def load_voice_pack(pack_name=None):
    """Load a voice pack by name.

    Returns a dict with:
        - manifest: pack metadata
        - reference_path: path to voice reference WAV
        - voice_config: TTS settings overrides
    """
    if pack_name is None:
        pack_name = CONFIG.get("packs", {}).get("active_voice", "default")

    pack_dir = get_pack_dir() / "voices" / pack_name

    result = {
        "name": pack_name,
        "manifest": {},
        "reference_path": None,
        "voice_config": {},
    }

    if not pack_dir.exists():
        return result

    # Load manifest
    manifest_path = pack_dir / "manifest.json"
    if manifest_path.exists():
        with open(manifest_path, "r", encoding="utf-8") as f:
            result["manifest"] = json.load(f)

    # Find voice reference
    ref_path = pack_dir / "reference.wav"
    if ref_path.exists():
        result["reference_path"] = str(ref_path)

    # Load voice config
    config_path = pack_dir / "voice_config.json"
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            result["voice_config"] = json.load(f)

    return result


def load_theme_pack(pack_name=None):
    """Load a theme pack by name.

    Returns a dict with:
        - manifest: pack metadata
        - theme: theme configuration dict
    """
    if pack_name is None:
        pack_name = CONFIG.get("packs", {}).get("active_theme", "default")

    pack_dir = get_pack_dir() / "themes" / pack_name

    result = {
        "name": pack_name,
        "manifest": {},
        "theme": {},
    }

    if not pack_dir.exists():
        return result

    # Load manifest
    manifest_path = pack_dir / "manifest.json"
    if manifest_path.exists():
        with open(manifest_path, "r", encoding="utf-8") as f:
            result["manifest"] = json.load(f)

    # Load theme
    theme_path = pack_dir / "theme.json"
    if theme_path.exists():
        with open(theme_path, "r", encoding="utf-8") as f:
            result["theme"] = json.load(f)

    return result


def build_system_prompt(core_directives, personality_pack):
    """Merge core directives with personality pack overlay into a final system prompt.

    The personality pack overlay is appended AFTER core directives, so pack-specific
    character details layer on top of the base personality without overriding
    the core directives.
    """
    parts = [core_directives]

    if personality_pack.get("personality"):
        parts.append("\n\n=== CHARACTER OVERLAY (from active personality pack) ===")
        parts.append(personality_pack["personality"])

    return "\n".join(parts)


def get_agent_display_name(personality_pack):
    """Get the display name for the agent based on active personality pack."""
    overlay = personality_pack.get("config_overlay", {})
    if overlay.get("agent_display_name"):
        return overlay["agent_display_name"]

    manifest = personality_pack.get("manifest", {})
    if manifest.get("character_name"):
        return manifest["character_name"]

    return CONFIG.get("agent_name", "Aegis")


def get_banner(personality_pack):
    """Get the startup banner text based on active personality pack."""
    overlay = personality_pack.get("config_overlay", {})
    if overlay.get("banner"):
        return overlay["banner"]

    agent_name = get_agent_display_name(personality_pack)
    return f"  {agent_name} — Online"


def list_packs(pack_type="personalities"):
    """List all installed packs of a given type.

    Args:
        pack_type: "personalities", "voices", or "themes"

    Returns:
        List of pack names (directory names).
    """
    pack_dir = get_pack_dir() / pack_type
    if not pack_dir.exists():
        return []

    return [d.name for d in pack_dir.iterdir() if d.is_dir()]
