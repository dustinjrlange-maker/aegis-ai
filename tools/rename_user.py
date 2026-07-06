# tools/rename_user.py
"""Rename an Aegis username across every place usernames are stored.

Usage (server MUST be stopped):
    python tools/rename_user.py olduser newuser           # dry-run (prints plan)
    python tools/rename_user.py olduser newuser --apply   # execute

RENAME_LOCATIONS below is the COMPLETE registry of username storage. Any new
feature that stores a username MUST be added here and to
tests/test_rename_user.py::test_rename_locations_registry_is_complete.
"""

import argparse
import json
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path

RENAME_LOCATIONS = [
    "users.json key",
    "data/users/<name>/ directory",
    "telegram.json user_mappings values",
    "core_config.json heartbeat.primary_user",
]

_VALID_NAME = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path, data):
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False),
                          encoding="utf-8")


def plan_rename(data_root, old, new, config=None):
    """Return a list of (location, detail) actions. Read-only."""
    data_root = Path(data_root)
    plan = []
    users = _read_json(data_root / "users.json")
    if old not in users:
        raise ValueError(f"user '{old}' not found in users.json")
    if new in users:
        raise ValueError(f"user '{new}' already exists in users.json")
    if not _VALID_NAME.match(new):
        raise ValueError(f"invalid new username '{new}' (lowercase, 2-32 chars)")
    plan.append(("users.json key", f"{old} -> {new}"))
    plan.append(("data/users/<name>/ directory",
                 f"users/{old}/ -> users/{new}/"))
    tg_path = data_root / "telegram.json"
    if tg_path.exists():
        tg = _read_json(tg_path)
        hits = [k for k, v in tg.get("user_mappings", {}).items() if v == old]
        plan.append(("telegram.json user_mappings values",
                     f"{len(hits)} mapping(s)"))
    else:
        plan.append(("telegram.json user_mappings values", "file absent — skip"))
    pu = (config or {}).get("heartbeat", {}).get("primary_user")
    plan.append(("core_config.json heartbeat.primary_user",
                 f"{pu} -> {new}" if pu == old else "not this user — skip"))
    return plan


def _backup(data_root, old):
    backups = Path(data_root) / "backups"
    backups.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    zpath = backups / f"rename-{stamp}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for name in ("users.json", "telegram.json"):
            p = Path(data_root) / name
            if p.exists():
                z.write(p, name)
        user_dir = Path(data_root) / "users" / old
        for f in user_dir.rglob("*"):
            if f.is_file():
                z.write(f, str(f.relative_to(data_root)))
    return zpath


def apply_rename(data_root, old, new, config_path=None):
    """Execute the rename. Backup first; then users.json -> dir ->
    telegram.json -> config, in that order."""
    data_root = Path(data_root)
    config = _read_json(config_path) if config_path else {}
    plan_rename(data_root, old, new, config=config)   # preflight (raises)
    zpath = _backup(data_root, old)

    users_path = data_root / "users.json"
    users = _read_json(users_path)
    users[new] = users.pop(old)
    _write_json(users_path, users)

    (data_root / "users" / old).rename(data_root / "users" / new)

    tg_path = data_root / "telegram.json"
    if tg_path.exists():
        tg = _read_json(tg_path)
        for k, v in tg.get("user_mappings", {}).items():
            if v == old:
                tg["user_mappings"][k] = new
        _write_json(tg_path, tg)

    if config_path and config.get("heartbeat", {}).get("primary_user") == old:
        config["heartbeat"]["primary_user"] = new
        _write_json(config_path, config)

    return zpath


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("old")
    ap.add_argument("new")
    ap.add_argument("--apply", action="store_true",
                    help="execute (default: dry-run)")
    args = ap.parse_args(argv)

    root = Path(__file__).resolve().parent.parent
    data_root = root / "data"
    config_path = root / "core" / "config" / "core_config.json"

    config = _read_json(config_path)
    plan = plan_rename(data_root, args.old, args.new, config=config)
    print(f"Rename plan '{args.old}' -> '{args.new}':")
    for loc, detail in plan:
        print(f"  - {loc}: {detail}")
    if not args.apply:
        print("\nDry-run only. Re-run with --apply to execute "
              "(STOP THE SERVER FIRST).")
        return 0
    zpath = apply_rename(data_root, args.old, args.new, config_path=config_path)
    print(f"\nDone. Backup at {zpath}. Restart the server.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
