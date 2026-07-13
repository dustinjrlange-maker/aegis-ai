"""load_recent_log_text must not inject stale logs as 'recent' context.

Regression: two 6-week-old audio-test memos were the 2 newest files, so they
were injected every turn with no age limit -> the local model fixated and kept
raising 'audio setup'. Injection must be windowed by the log's `created` date.
"""
import json
from datetime import datetime, timedelta
from pathlib import Path

from core.memory.personal_log import load_recent_log_text

NOW = datetime(2026, 7, 12, 12, 0, 0)


def _write_log(dir_, log_id, created, text):
    (dir_ / "personal_logs").mkdir(parents=True, exist_ok=True)
    (dir_ / "personal_logs" / f"{log_id}.json").write_text(
        json.dumps({"id": log_id, "created": created.isoformat(), "text": text}),
        encoding="utf-8")


def test_stale_logs_are_not_injected(tmp_path):
    # 42 days old — must NOT be injected even though it's the newest file.
    _write_log(tmp_path, "2026-05-31_184913",
               NOW - timedelta(days=42), "switched my audio inputs to USB, turned mic down")
    out = load_recent_log_text(2, tmp_path, max_age_days=14, now=NOW)
    assert out == []


def test_recent_logs_are_injected(tmp_path):
    _write_log(tmp_path, "2026-07-10_090000", NOW - timedelta(days=2), "thinking about the podcast")
    out = load_recent_log_text(2, tmp_path, max_age_days=14, now=NOW)
    assert len(out) == 1
    assert "podcast" in out[0]


def test_window_boundary_excludes_old_keeps_new(tmp_path):
    _write_log(tmp_path, "2026-07-11_100000", NOW - timedelta(days=1), "fresh thought")
    _write_log(tmp_path, "2026-06-01_100000", NOW - timedelta(days=41), "old audio note")
    out = load_recent_log_text(5, tmp_path, max_age_days=14, now=NOW)
    assert out == ["fresh thought"]


def test_count_cap_applies_within_window(tmp_path):
    for i in range(4):
        _write_log(tmp_path, f"2026-07-{10 - i:02d}_100000",
                   NOW - timedelta(days=i + 1), f"note {i}")
    out = load_recent_log_text(2, tmp_path, max_age_days=14, now=NOW)
    assert len(out) == 2                      # newest two only
    assert out[0] == "note 0" and out[1] == "note 1"


def test_default_window_is_14_days(tmp_path):
    # Real-world guard: no explicit window -> 14-day default, so a 42-day memo is out.
    _write_log(tmp_path, "2026-05-31_184913",
               NOW - timedelta(days=42), "audio setup memo")
    assert load_recent_log_text(2, tmp_path, now=NOW) == []
