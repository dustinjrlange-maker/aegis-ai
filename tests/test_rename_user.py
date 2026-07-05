# tests/test_rename_user.py
import json
import zipfile
from pathlib import Path
from tools.rename_user import plan_rename, apply_rename, RENAME_LOCATIONS


def _make_data_root(tmp_path):
    root = tmp_path / "data"
    (root / "users" / "olduser").mkdir(parents=True)
    (root / "users" / "olduser" / "tasks.json").write_text("[]", encoding="utf-8")
    (root / "users.json").write_text(json.dumps(
        {"olduser": {"display_name": "Switch", "passcode_hash": "h"}}),
        encoding="utf-8")
    (root / "telegram.json").write_text(json.dumps(
        {"user_mappings": {"123": "olduser"}}), encoding="utf-8")
    return root


def test_plan_rename_is_read_only(tmp_path):
    root = _make_data_root(tmp_path)
    plan = plan_rename(root, "olduser", "newuser",
                       config={"heartbeat": {"primary_user": "olduser"}})
    assert len(plan) == 4          # users.json, dir, telegram, config
    # nothing changed on disk
    assert (root / "users" / "olduser").exists()
    assert "olduser" in json.loads((root / "users.json").read_text(encoding="utf-8"))


def test_apply_rename_moves_everything(tmp_path):
    root = _make_data_root(tmp_path)
    cfg_path = root / "core_config_test.json"
    cfg_path.write_text(json.dumps(
        {"heartbeat": {"primary_user": "olduser"}}), encoding="utf-8")
    apply_rename(root, "olduser", "newuser", config_path=cfg_path)

    assert not (root / "users" / "olduser").exists()
    assert (root / "users" / "newuser" / "tasks.json").exists()
    users = json.loads((root / "users.json").read_text(encoding="utf-8"))
    assert "newuser" in users and "olduser" not in users
    tg = json.loads((root / "telegram.json").read_text(encoding="utf-8"))
    assert tg["user_mappings"]["123"] == "newuser"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert cfg["heartbeat"]["primary_user"] == "newuser"
    # backup zip exists and contains the old users.json
    backups = list((root / "backups").glob("rename-*.zip"))
    assert len(backups) == 1
    assert "users.json" in zipfile.ZipFile(backups[0]).namelist()


def test_apply_rename_refuses_taken_name(tmp_path):
    root = _make_data_root(tmp_path)
    users = json.loads((root / "users.json").read_text(encoding="utf-8"))
    users["newuser"] = {"display_name": "X", "passcode_hash": "h"}
    (root / "users.json").write_text(json.dumps(users), encoding="utf-8")
    import pytest
    with pytest.raises(ValueError):
        apply_rename(root, "olduser", "newuser", config_path=None)


def test_no_hardcoded_user_ids_in_source():
    """Guard: the Wave 3 bug class. No core/server source may hardcode a
    username literal as a user_id."""
    import re
    pattern = re.compile(r'user_id\s*=\s*["\'](switch|dustin)["\']')
    offenders = []
    for base in ("core", "server", "integrations"):
        for p in (Path(__file__).parent.parent / base).rglob("*.py"):
            if pattern.search(p.read_text(encoding="utf-8", errors="ignore")):
                offenders.append(str(p))
    assert offenders == []


def test_rename_locations_registry_is_complete():
    """RENAME_LOCATIONS documents every place a username is stored. If you
    add a new username-keyed store, add it to the tool AND this list."""
    assert set(RENAME_LOCATIONS) == {
        "users.json key",
        "data/users/<name>/ directory",
        "telegram.json user_mappings values",
        "core_config.json heartbeat.primary_user",
    }
