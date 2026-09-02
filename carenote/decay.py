"""Hybrid-storage policy preview for ageing longitudinal entries."""

from datetime import datetime, timezone
from .importance import parse_timestamp


SAFETY_ENTITY_PREFIXES = ("allergy:", "medication:", "safety_net:")


def classify_storage_tier(entry, now=None):
    now = now or datetime.now(timezone.utc)
    age_days = max(0, int((now - parse_timestamp(entry.get("created_at"))).total_seconds() / 86400))
    entities = entry.get("entities") or []
    protected = entry.get("risk_level") in {"critical", "high"} or any(
        entity.startswith(SAFETY_ENTITY_PREFIXES) for entity in entities
    )
    if protected or age_days <= 90:
        tier = "hot"
        policy = "full content indexed; instant glance and timeline access"
    elif age_days <= 365:
        tier = "warm"
        policy = "structured summary indexed; full immutable version retained"
    else:
        tier = "cold"
        policy = "compressed encrypted archive; provenance stub stays queryable"
    return {"tier": tier, "age_days": age_days, "protected": protected, "policy": policy}
