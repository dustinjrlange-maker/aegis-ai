from datetime import datetime
from core.heartbeat.state import HeartbeatState


def test_roundtrip_mark_and_reload(tmp_path):
    p = tmp_path / "heartbeat.json"
    st = HeartbeatState(p)
    assert st.get("job_a") is None
    now = datetime(2026, 7, 4, 9, 0, 0)
    st.mark_fired("job_a", fired_at=now, next_eligible_at=datetime(2026, 7, 4, 9, 1, 0))
    st2 = HeartbeatState(p)
    rec = st2.get("job_a")
    assert rec["last_fired_at"] == now
    assert rec["next_eligible_at"] == datetime(2026, 7, 4, 9, 1, 0)


def test_atomic_write_leaves_no_temp(tmp_path):
    p = tmp_path / "heartbeat.json"
    st = HeartbeatState(p)
    st.mark_fired("j", datetime(2026, 7, 4, 9, 0), datetime(2026, 7, 4, 9, 1))
    assert p.exists()
    assert list(tmp_path.glob("*.tmp")) == []


def test_pending_pushes_queue(tmp_path):
    p = tmp_path / "heartbeat.json"
    st = HeartbeatState(p)
    st.queue_push({"user_id": "switch", "title": "t", "body": "b", "channels": ["notification"]})
    st2 = HeartbeatState(p)
    pending = st2.drain_pushes()
    assert pending == [{"user_id": "switch", "title": "t", "body": "b", "channels": ["notification"]}]
    assert st2.drain_pushes() == []


def test_garbage_file_starts_fresh(tmp_path):
    p = tmp_path / "heartbeat.json"
    p.write_text("}{not json", encoding="utf-8")
    st = HeartbeatState(p)
    assert st.get("anything") is None


def test_valid_json_bad_shape_starts_fresh(tmp_path):
    p = tmp_path / "heartbeat.json"
    p.write_text('{"jobs": {"j": {"last_fired_at": "garbage"}}}', encoding="utf-8")
    st = HeartbeatState(p)
    assert st.get("j") is None


def test_save_swallows_oserror(tmp_path, monkeypatch):
    """_save must log and return rather than propagate an OSError (FIX M8)."""
    import os
    p = tmp_path / "heartbeat.json"
    st = HeartbeatState(p)
    monkeypatch.setattr(os, "replace", lambda src, dst: (_ for _ in ()).throw(OSError("disk full")))
    # mark_fired internally calls _save — must not raise
    st.mark_fired("j", datetime(2026, 7, 4, 9, 0), datetime(2026, 7, 4, 9, 1))
