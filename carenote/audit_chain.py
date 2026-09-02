"""Tamper-evident hashing helpers for content-free audit metadata."""

import hashlib
import json


GENESIS_HASH = "0" * 64


def canonical_metadata(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = {"legacy_value": value}
    return json.dumps(value or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def event_hash(row, previous_hash):
    payload = {
        "action": row["action"],
        "actor_id": row["actor_id"],
        "clinic_id": row["clinic_id"],
        "created_at": row["created_at"],
        "entity_id": row["entity_id"],
        "entity_type": row["entity_type"],
        "metadata": json.loads(canonical_metadata(row.get("metadata_json", {}))),
        "previous_hash": previous_hash,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def verify_rows(rows):
    previous = GENESIS_HASH
    for row in rows:
        stored_previous = row.get("prev_hash") or ""
        stored_hash = row.get("event_hash") or ""
        expected = event_hash(row, previous)
        if stored_previous != previous or stored_hash != expected:
            return {
                "valid": False,
                "event_count": len(rows),
                "first_broken_event_id": row.get("id"),
                "head_hash": previous,
            }
        previous = stored_hash
    return {
        "valid": True,
        "event_count": len(rows),
        "first_broken_event_id": None,
        "head_hash": previous,
    }
