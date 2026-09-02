"""Explainable importance scoring with lightweight interaction learning."""

from datetime import datetime, timezone
import json
import math
import re


STOPWORDS = {
    "about", "after", "again", "also", "before", "being", "could", "from",
    "have", "into", "patient", "reports", "that", "their", "there", "these",
    "this", "today", "with", "would", "summary", "note",
}

LEARNED_BOOST_CAP = 4.0


def parse_timestamp(value):
    if not value:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def feature_tokens(text, entities=None):
    words = re.findall(r"[a-z][a-z0-9_-]{3,}", (text or "").lower())
    features = {"kw:" + word for word in words if word not in STOPWORDS}
    for entity in entities or []:
        features.add("entity:" + entity.lower())
    return sorted(features)


def base_score(entry, now=None, unresolved_task=False, clinician_confirmed=False):
    now = now or datetime.now(timezone.utc)
    age_days = max(0.0, (now - parse_timestamp(entry.get("created_at"))).total_seconds() / 86400)
    recency = 3.0 * math.exp(-age_days / 120.0)
    risk = {"critical": 4.0, "high": 2.5, "medium": 1.2, "low": 0.2}.get(entry.get("risk_level"), 0.2)
    task = 2.0 if unresolved_task else 0.0
    verified = 2.0 if clinician_confirmed else 0.0
    entities = entry.get("entities")
    if entities is None:
        try:
            entities = json.loads(entry.get("entities_json") or "[]")
        except (TypeError, json.JSONDecodeError):
            entities = []
    entity_bonus = min(1.5, 0.3 * len(entities))
    return round(recency + risk + task + verified + entity_bonus, 3)


def learned_boost(text, entities, signal_weights):
    features = feature_tokens(text, entities)
    boost = sum(float(signal_weights.get(feature, 0.0)) for feature in features)
    return round(max(-LEARNED_BOOST_CAP, min(boost, LEARNED_BOOST_CAP)), 3), features


def explain_score(entry, signal_weights, now=None, unresolved_task=False, clinician_confirmed=False):
    base = base_score(entry, now=now, unresolved_task=unresolved_task, clinician_confirmed=clinician_confirmed)
    entities = entry.get("entities")
    if entities is None:
        try:
            entities = json.loads(entry.get("entities_json") or "[]")
        except (TypeError, json.JSONDecodeError):
            entities = []
    learned, features = learned_boost(entry.get("content", ""), entities, signal_weights)
    return {
        "score": round(base + learned, 3),
        "base_score": base,
        "learned_boost": learned,
        "matched_features": [feature for feature in features if feature in signal_weights],
        "influence_budget": {
            "used": abs(learned),
            "cap": LEARNED_BOOST_CAP,
            "remaining": round(max(0.0, LEARNED_BOOST_CAP - abs(learned)), 3),
            "policy": "Ranking contribution is capped; only clinician-reviewed interactions update signals",
        },
    }
