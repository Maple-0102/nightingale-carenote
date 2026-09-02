"""Explainable prototype policy for clinical fact re-verification."""

from datetime import datetime, timezone


REVIEW_WINDOWS_DAYS = {
    "critical": 30,
    "high": 60,
    "medium": 180,
    "low": 365,
}


def _timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def review_freshness(entry, verifications=None, now=None):
    """Return an explainable freshness state; this is not a clinical guideline."""
    now = now or datetime.now(timezone.utc)
    current_version = int(entry.get("version", 1))
    matching = [
        item for item in (verifications or [])
        if int(item.get("entry_version", 0)) == current_version and item.get("outcome") == "confirmed"
    ]
    latest = max(matching, key=lambda item: item["verified_at"], default=None)
    reference = latest["verified_at"] if latest else entry["created_at"]
    age_days = max(0, (now - _timestamp(reference)).days)
    window = REVIEW_WINDOWS_DAYS.get(entry.get("risk_level", "low"), 365)
    unverified_priority = entry.get("risk_level") in {"critical", "high"} or entry.get("author_role") == "system"
    due = age_days >= window or (latest is None and unverified_priority)

    if latest and due:
        state = "overdue"
    elif latest:
        state = "fresh"
    elif due:
        state = "never_verified"
    else:
        state = "not_yet_due"

    return {
        "state": state,
        "due": due,
        "age_days": age_days,
        "review_window_days": window,
        "last_verified_at": latest.get("verified_at") if latest else None,
        "last_verified_by": latest.get("verified_by") if latest else None,
        "last_verified_by_name": latest.get("verified_by_name") if latest else None,
        "verification_count": len(matching),
        "entry_version": current_version,
        "reason": (
            "Current version has never been explicitly verified"
            if latest is None and due
            else "Prototype re-verification window has elapsed"
            if due
            else "Current version is within the prototype review window"
            if latest
            else "Current version is not yet due under the prototype policy"
        ),
        "policy": "Prototype review policy; not a clinical guideline",
    }
