"""Atomic JSON persistence helpers.

Two silent data-loss modes this module closes (2026-07-09 audit):
- Torn writes: open(path, "w") truncates first, so a crash mid-dump destroys
  the previous state. Writes here go to a tmp file then os.replace.
- Corrupt-then-overwrite: a corrupt file that silently reads as empty gets
  overwritten by the next save, destroying the recoverable original. Corrupt
  files are copied to <name>.corrupt (and logged at ERROR) before the caller
  falls back to a default.
"""

import json
import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


def write_json_atomic(path, data, **dump_kwargs):
    """Write JSON to *path* via tmp + os.replace so a crash mid-write can
    never truncate the existing file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, **dump_kwargs)
    os.replace(tmp, path)


def read_json_safe(path, default, label=None):
    """Read JSON from *path*; on corruption back the file up to
    <name>.corrupt, log at ERROR, and return *default* (a missing file
    returns *default* silently — that's a normal first run)."""
    path = Path(path)
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        backup = path.with_name(path.name + ".corrupt")
        note = ""
        try:
            shutil.copy2(path, backup)
            note = f" — original preserved at {backup.name}"
        except Exception:
            logger.error("Could not back up corrupt file %s", path)
        logger.error("%s is corrupt (%s); treating as empty%s",
                     label or path.name, e, note)
        return default
