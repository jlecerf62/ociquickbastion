from pathlib import Path

from qb2.session_registry import (
    list_recent_sessions,
    load_session_registry,
    save_session_registry,
    upsert_session_record,
)
from qb2.types import LocalSessionRecord, SessionMode


def _record(session_id: str, ts: str) -> LocalSessionRecord:
    return LocalSessionRecord(
        session_id=session_id,
        target_ocid=None,
        target_name="x",
        target_private_ip="10.0.0.1",
        mode=SessionMode.PFWD,
        region="eu-paris-1",
        local_port=4444,
        remote_port=22,
        os_user="opc",
        created_at_utc=ts,
        last_used_at_utc=ts,
        status_hint="created",
    )


def test_save_and_load_roundtrip(tmp_path: Path):
    path = tmp_path / "sessions.json"
    rec = _record("s1", "2026-01-01T00:00:00+00:00")
    save_session_registry(path, [rec])
    loaded = load_session_registry(path)
    assert len(loaded) == 1
    assert loaded[0].session_id == "s1"
    assert loaded[0].mode == SessionMode.PFWD


def test_upsert_keeps_original_created_at(tmp_path: Path):
    path = tmp_path / "sessions.json"
    first = _record("s1", "2026-01-01T00:00:00+00:00")
    upsert_session_record(path, first)

    updated = _record("s1", "2026-02-01T00:00:00+00:00")
    updated.last_used_at_utc = "2026-02-01T00:00:00+00:00"
    updated.status_hint = "resumed"
    upsert_session_record(path, updated)

    loaded = load_session_registry(path)
    assert loaded[0].created_at_utc == "2026-01-01T00:00:00+00:00"
    assert loaded[0].status_hint == "resumed"


def test_load_handles_corrupt_file(tmp_path: Path):
    path = tmp_path / "sessions.json"
    path.write_text("{not-json", encoding="utf-8")
    assert load_session_registry(path) == []


def test_list_recent_sessions_orders_desc(tmp_path: Path):
    path = tmp_path / "sessions.json"
    r1 = _record("old", "2026-01-01T00:00:00+00:00")
    r2 = _record("new", "2026-02-01T00:00:00+00:00")
    save_session_registry(path, [r1, r2])
    out = list_recent_sessions(path)
    assert out[0].session_id == "new"
