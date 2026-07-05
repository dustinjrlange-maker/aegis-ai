import json
from datetime import datetime
from core.heartbeat.hlog import HeartbeatLog


def test_write_appends_record(tmp_path):
    p = tmp_path / "heartbeat_log.jsonl"
    log = HeartbeatLog(p, max_bytes=10_000)
    log.write(datetime(2026, 7, 4, 9, 0), "job_a", "silent", "silent_log", "ran fine", 12)
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["job_id"] == "job_a"
    assert rec["kind"] == "silent"
    assert rec["outcome"] == "silent_log"
    assert rec["detail"] == "ran fine"
    assert rec["duration_ms"] == 12
    assert rec["ts"] == "2026-07-04T09:00:00"


def test_size_cap_truncates_oldest(tmp_path):
    p = tmp_path / "heartbeat_log.jsonl"
    log = HeartbeatLog(p, max_bytes=400)
    for i in range(50):
        log.write(datetime(2026, 7, 4, 9, 0), f"j{i}", "silent", "silent_log", "x" * 20, 1)
    assert p.stat().st_size <= 400 * 2
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    assert json.loads(lines[-1])["job_id"] == "j49"
    assert all(json.loads(l)["job_id"] != "j0" for l in lines)
