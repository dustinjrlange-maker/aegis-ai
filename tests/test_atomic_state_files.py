"""State-file durability (2026-07-09 audit).

Two silent data-loss paths: (1) non-atomic writes — a crash mid-save truncates
tasks.json/recurring.json/contacts.json; (2) corrupt JSON silently reads as
empty, and the next save overwrites the corrupt file — accounts.json could
lose every linked account this way. Fix: tmp+os.replace writes, and corrupt
files are backed up to <name>.corrupt before falling back to empty.
"""

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.accounts.manager import AccountManager
from core.memory.contact_manager import ContactManager
from core.protocols.operations import OperationsProtocol


# --- crash mid-write must not destroy the previous file ----------------------

def test_save_tasks_crash_leaves_original_intact(tmp_path, monkeypatch):
    proto = OperationsProtocol(data_dir=tmp_path)
    proto.add_task("first task")
    original = (tmp_path / "tasks.json").read_text(encoding="utf-8")

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(json, "dump", boom)
    try:
        proto.add_task("second task")
    except Exception:
        pass
    assert (tmp_path / "tasks.json").read_text(encoding="utf-8") == original


def test_save_contacts_crash_leaves_original_intact(tmp_path, monkeypatch):
    cm = ContactManager(tmp_path)
    cm.add_contact(name="Krunch")
    original = (tmp_path / "contacts.json").read_text(encoding="utf-8")

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(json, "dump", boom)
    try:
        cm.add_contact(name="SSCanine")
    except Exception:
        pass
    assert (tmp_path / "contacts.json").read_text(encoding="utf-8") == original


# --- corrupt file must be backed up, not silently replaced -------------------

def test_corrupt_tasks_json_backed_up(tmp_path):
    (tmp_path / "tasks.json").write_text('{"broken', encoding="utf-8")
    proto = OperationsProtocol(data_dir=tmp_path)
    assert proto._tasks == []
    backup = tmp_path / "tasks.json.corrupt"
    assert backup.exists()
    assert backup.read_text(encoding="utf-8") == '{"broken'


def test_corrupt_recurring_json_backed_up(tmp_path):
    (tmp_path / "recurring.json").write_text("[oops", encoding="utf-8")
    proto = OperationsProtocol(data_dir=tmp_path)
    assert proto._recurring == []
    assert (tmp_path / "recurring.json.corrupt").exists()


def test_corrupt_contacts_json_backed_up(tmp_path):
    (tmp_path / "contacts.json").write_text("not json at all", encoding="utf-8")
    cm = ContactManager(tmp_path)
    assert cm.list_contacts() == []
    assert (tmp_path / "contacts.json.corrupt").exists()


def test_corrupt_accounts_json_backed_up_and_survives_upsert(tmp_path):
    """The registry-destruction path: corrupt accounts.json read as empty,
    then upsert_account overwrites it. The corrupt original must survive in
    the backup so linked accounts are recoverable."""
    corrupt = '{"accounts": [{"id": "google-personal", "em'  # torn write
    (tmp_path / "accounts.json").write_text(corrupt, encoding="utf-8")
    mgr = AccountManager(tmp_path)
    assert mgr.list() == []
    mgr.upsert_account(label="New", email="new@x.ca")
    backup = tmp_path / "accounts.json.corrupt"
    assert backup.exists()
    assert backup.read_text(encoding="utf-8") == corrupt
    # and the new registry is valid JSON with the new account
    data = json.loads((tmp_path / "accounts.json").read_text(encoding="utf-8"))
    assert len(data["accounts"]) == 1
