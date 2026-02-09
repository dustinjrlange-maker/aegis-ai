"""
Aegis AI — Pack Validator
Validates pack structure, manifests, and content files.
Used by pack_cli.py and can be imported directly.
"""

import json
from pathlib import Path

# Required manifest fields by pack type
MANIFEST_SCHEMAS = {
    "personality": {
        "required": ["name", "character_name", "author", "version", "description"],
        "optional": ["disclaimer", "tags", "compatibility"],
    },
    "voice": {
        "required": ["name", "author", "version", "description"],
        "optional": ["disclaimer", "compatibility"],
    },
    "theme": {
        "required": ["name", "author", "version", "description"],
        "optional": ["compatibility"],
    },
}

# Expected files by pack type
EXPECTED_FILES = {
    "personality": {
        "required": ["manifest.json", "personality.txt"],
        "optional": ["config_overlay.json", "filler_phrases.json"],
        "dirs": ["memories"],
    },
    "voice": {
        "required": ["manifest.json"],
        "optional": ["voice_config.json", "reference.wav"],
        "dirs": [],
    },
    "theme": {
        "required": ["manifest.json", "theme.json"],
        "optional": [],
        "dirs": [],
    },
}

# Valid memory types for personality packs
VALID_MEMORY_TYPES = ["backstory", "relationships", "knowledge"]
VALID_MEMORY_WEIGHTS = ["core", "secondary"]


class ValidationResult:
    """Collects validation errors and warnings."""

    def __init__(self, pack_path):
        self.pack_path = pack_path
        self.errors = []
        self.warnings = []

    def error(self, msg):
        self.errors.append(msg)

    def warn(self, msg):
        self.warnings.append(msg)

    @property
    def valid(self):
        return len(self.errors) == 0

    def summary(self):
        lines = []
        if self.errors:
            lines.append(f"  ERRORS ({len(self.errors)}):")
            for e in self.errors:
                lines.append(f"    X {e}")
        if self.warnings:
            lines.append(f"  WARNINGS ({len(self.warnings)}):")
            for w in self.warnings:
                lines.append(f"    ! {w}")
        if self.valid and not self.warnings:
            lines.append("  Pack is valid.")
        elif self.valid:
            lines.append(f"  Pack is valid with {len(self.warnings)} warning(s).")
        else:
            lines.append(f"  Pack is INVALID — {len(self.errors)} error(s).")
        return "\n".join(lines)


def detect_pack_type(pack_path):
    """Detect pack type from its parent directory name."""
    parent = pack_path.parent.name
    type_map = {
        "personalities": "personality",
        "voices": "voice",
        "themes": "theme",
    }
    return type_map.get(parent)


def validate_pack(pack_path, pack_type=None):
    """Validate a pack directory. Returns a ValidationResult."""
    pack_path = Path(pack_path)
    result = ValidationResult(pack_path)

    if not pack_path.exists():
        result.error(f"Pack directory does not exist: {pack_path}")
        return result

    if not pack_path.is_dir():
        result.error(f"Not a directory: {pack_path}")
        return result

    # Detect type if not specified
    if pack_type is None:
        pack_type = detect_pack_type(pack_path)
    if pack_type not in MANIFEST_SCHEMAS:
        result.error(f"Unknown pack type: {pack_type}. Must be: personality, voice, theme")
        return result

    # Check required files
    expected = EXPECTED_FILES[pack_type]
    for fname in expected["required"]:
        if not (pack_path / fname).exists():
            result.error(f"Missing required file: {fname}")

    # Check optional files — just note if present
    for fname in expected["optional"]:
        if (pack_path / fname).exists():
            pass  # Good, it's there

    # Validate manifest
    manifest_path = pack_path / "manifest.json"
    if manifest_path.exists():
        _validate_manifest(manifest_path, pack_type, result)

    # Type-specific validation
    if pack_type == "personality":
        _validate_personality(pack_path, result)
    elif pack_type == "voice":
        _validate_voice(pack_path, result)
    elif pack_type == "theme":
        _validate_theme(pack_path, result)

    return result


def _validate_manifest(manifest_path, pack_type, result):
    """Validate manifest.json content."""
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except json.JSONDecodeError as e:
        result.error(f"manifest.json is not valid JSON: {e}")
        return
    except IOError as e:
        result.error(f"Cannot read manifest.json: {e}")
        return

    schema = MANIFEST_SCHEMAS[pack_type]
    for field in schema["required"]:
        if field not in manifest:
            result.error(f"manifest.json missing required field: {field}")
        elif not isinstance(manifest[field], str) or not manifest[field].strip():
            result.error(f"manifest.json field '{field}' must be a non-empty string")

    # Version format check
    version = manifest.get("version", "")
    if version and not _is_semver(version):
        result.warn(f"Version '{version}' doesn't follow semver (x.y.z)")

    # Tags should be a list of strings
    if "tags" in manifest:
        if not isinstance(manifest["tags"], list):
            result.error("manifest.json 'tags' must be a list")
        elif not all(isinstance(t, str) for t in manifest["tags"]):
            result.error("manifest.json 'tags' must contain only strings")


def _validate_personality(pack_path, result):
    """Validate personality-specific files."""
    # Check personality.txt content
    personality_txt = pack_path / "personality.txt"
    if personality_txt.exists():
        content = personality_txt.read_text(encoding="utf-8").strip()
        if len(content) < 50:
            result.warn("personality.txt is very short (< 50 chars)")

    # Check config_overlay.json
    overlay_path = pack_path / "config_overlay.json"
    if overlay_path.exists():
        try:
            with open(overlay_path, "r", encoding="utf-8") as f:
                overlay = json.load(f)
            if not isinstance(overlay, dict):
                result.error("config_overlay.json must be a JSON object")
        except json.JSONDecodeError as e:
            result.error(f"config_overlay.json is not valid JSON: {e}")

    # Check filler_phrases.json
    filler_path = pack_path / "filler_phrases.json"
    if filler_path.exists():
        try:
            with open(filler_path, "r", encoding="utf-8") as f:
                filler = json.load(f)
            if isinstance(filler, dict):
                if "phrases" in filler and not isinstance(filler["phrases"], list):
                    result.error("filler_phrases.json 'phrases' must be a list")
                if "word_replacements" in filler and not isinstance(filler["word_replacements"], dict):
                    result.error("filler_phrases.json 'word_replacements' must be an object")
            elif not isinstance(filler, list):
                result.error("filler_phrases.json must be a list or object with 'phrases'")
        except json.JSONDecodeError as e:
            result.error(f"filler_phrases.json is not valid JSON: {e}")

    # Check memories directory
    memories_dir = pack_path / "memories"
    if memories_dir.exists() and memories_dir.is_dir():
        for mem_file in memories_dir.glob("*.json"):
            mem_type = mem_file.stem
            if mem_type not in VALID_MEMORY_TYPES:
                result.warn(f"Unusual memory type: {mem_type} (expected: {', '.join(VALID_MEMORY_TYPES)})")
            _validate_memory_file(mem_file, result)


def _validate_memory_file(mem_file, result):
    """Validate a character memory JSON file."""
    try:
        with open(mem_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        result.error(f"{mem_file.name} is not valid JSON: {e}")
        return

    if not isinstance(data, dict):
        result.error(f"{mem_file.name} must be a JSON object")
        return

    if "memories" not in data:
        result.error(f"{mem_file.name} missing 'memories' array")
        return

    if not isinstance(data["memories"], list):
        result.error(f"{mem_file.name} 'memories' must be a list")
        return

    for i, mem in enumerate(data["memories"]):
        if not isinstance(mem, dict):
            result.error(f"{mem_file.name} memory [{i}] must be an object")
            continue

        if "text" not in mem and "content" not in mem:
            result.error(f"{mem_file.name} memory [{i}] missing 'text' or 'content' field")

        weight = mem.get("weight", "secondary")
        if weight not in VALID_MEMORY_WEIGHTS:
            result.error(f"{mem_file.name} memory [{i}] invalid weight: {weight}")

        tags = mem.get("tags", [])
        if not isinstance(tags, list):
            result.error(f"{mem_file.name} memory [{i}] 'tags' must be a list")

    core_count = sum(1 for m in data["memories"] if m.get("weight") == "core")
    if core_count > 8:
        result.warn(
            f"{mem_file.name} has {core_count} core memories. "
            "Too many core memories bloat the system prompt. Consider making some secondary."
        )


def _validate_voice(pack_path, result):
    """Validate voice-specific files."""
    config_path = pack_path / "voice_config.json"
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            if not isinstance(config, dict):
                result.error("voice_config.json must be a JSON object")
        except json.JSONDecodeError as e:
            result.error(f"voice_config.json is not valid JSON: {e}")

    # Check for reference audio
    ref_wav = pack_path / "reference.wav"
    if not ref_wav.exists():
        result.warn("No reference.wav found. Voice cloning won't be available for this pack.")


def _validate_theme(pack_path, result):
    """Validate theme-specific files."""
    theme_path = pack_path / "theme.json"
    if theme_path.exists():
        try:
            with open(theme_path, "r", encoding="utf-8") as f:
                theme = json.load(f)
            if not isinstance(theme, dict):
                result.error("theme.json must be a JSON object")
                return

            # Check for color definitions
            if "colors" not in theme:
                result.warn("theme.json has no 'colors' section")
            elif not isinstance(theme["colors"], dict):
                result.error("theme.json 'colors' must be an object")

        except json.JSONDecodeError as e:
            result.error(f"theme.json is not valid JSON: {e}")


def _is_semver(version):
    """Check if version string follows semver pattern."""
    parts = version.split(".")
    if len(parts) != 3:
        return False
    return all(p.isdigit() for p in parts)


def validate_all_packs(packs_root):
    """Validate all installed packs. Returns dict of results."""
    packs_root = Path(packs_root)
    results = {}

    for type_dir_name in ["personalities", "voices", "themes"]:
        type_dir = packs_root / type_dir_name
        if not type_dir.exists():
            continue
        for pack_dir in sorted(type_dir.iterdir()):
            if pack_dir.is_dir():
                key = f"{type_dir_name}/{pack_dir.name}"
                results[key] = validate_pack(pack_dir)

    return results
