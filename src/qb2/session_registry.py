from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import List

from .types import LocalSessionRecord, SessionMode


def _deserialize_record(obj: dict) -> LocalSessionRecord:
    mode_val = obj.get("mode", SessionMode.PFWD.value)
    return LocalSessionRecord(
        session_id=obj["session_id"],
        target_ocid=obj.get("target_ocid"),
        target_name=obj.get("target_name", ""),
        target_private_ip=obj.get("target_private_ip"),
        mode=SessionMode(mode_val),
        region=obj.get("region", ""),
        local_port=obj.get("local_port"),
        remote_port=obj.get("remote_port"),
        os_user=obj.get("os_user"),
        created_at_utc=obj.get("created_at_utc", ""),
        last_used_at_utc=obj.get("last_used_at_utc", ""),
        status_hint=obj.get("status_hint", ""),
    )


def _serialize_record(rec: LocalSessionRecord) -> dict:
    data = asdict(rec)
    data["mode"] = rec.mode.value
    return data


def load_session_registry(path: Path) -> List[LocalSessionRecord]:
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    records: List[LocalSessionRecord] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            records.append(_deserialize_record(item))
        except Exception:
            continue
    return records


def save_session_registry(path: Path, records: List[LocalSessionRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [_serialize_record(r) for r in records]
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def upsert_session_record(path: Path, record: LocalSessionRecord, max_entries: int = 100) -> None:
    records = load_session_registry(path)
    by_id = {r.session_id: r for r in records}
    existing = by_id.get(record.session_id)
    if existing and existing.created_at_utc:
        record.created_at_utc = existing.created_at_utc
    by_id[record.session_id] = record
    merged = sorted(by_id.values(), key=lambda r: r.last_used_at_utc, reverse=True)
    save_session_registry(path, merged[:max_entries])


def list_recent_sessions(path: Path, limit: int = 20) -> List[LocalSessionRecord]:
    records = load_session_registry(path)
    records.sort(key=lambda r: r.last_used_at_utc, reverse=True)
    return records[:limit]
