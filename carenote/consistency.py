"""Deterministic, provenance-first consistency checks for longitudinal notes."""

import hashlib


def _source(entry, term):
    content = entry.get("content", "")
    start = content.lower().find(term.lower())
    if start < 0:
        start = 0
        end = min(len(content), 120)
    else:
        end = start + len(term)
    return {
        "entry_id": entry["id"],
        "entry_version": entry["version"],
        "start_offset": start,
        "end_offset": end,
        "quote": content[start:end],
        "source_type": entry["type"],
        "author_role": entry["author_role"],
        "created_at": entry["created_at"],
        "provenance_pointer": entry.get("provenance_pointer"),
    }


def _conflict_id(rule_id, left, right):
    canonical = "|".join(
        [rule_id, left["id"], str(left["version"]), right["id"], str(right["version"])]
    )
    return "conflict-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def detect_conflicts(entries, decisions=None):
    """Detect a small, explainable set of safety conflicts without making diagnoses."""
    decisions = decisions or {}
    allergy_entries = []
    medication_entries = []
    for entry in entries:
        entities = {value.lower() for value in entry.get("entities", [])}
        content = entry.get("content", "").lower()
        section = entry.get("section_key", "").lower()
        if "allergy:penicillin" in entities or "penicillin allergy" in content:
            allergy_entries.append(entry)
        medication_signal = "medication:amoxicillin" in entities or any(
            phrase in content
            for phrase in ("active medication: amoxicillin", "lists amoxicillin as active", "prescribed amoxicillin")
        )
        if medication_signal and "allerg" not in section and "allergy:penicillin" not in entities:
            medication_entries.append(entry)

    conflicts = []
    for allergy in allergy_entries:
        for medication in medication_entries:
            conflict_id = _conflict_id("drug_allergy_penicillin_amoxicillin", allergy, medication)
            stored = decisions.get(conflict_id, {})
            conflicts.append(
                {
                    "id": conflict_id,
                    "rule_id": "drug_allergy_penicillin_amoxicillin",
                    "title": "Medication–allergy conflict requires review",
                    "summary": "An active amoxicillin record conflicts with a documented penicillin allergy.",
                    "severity": "critical",
                    "status": stored.get("status", "suggested"),
                    "decided_by": stored.get("decided_by"),
                    "decided_by_name": stored.get("decided_by_name"),
                    "decided_at": stored.get("decided_at"),
                    "why_now": "Two current longitudinal sources trigger an explainable medication–allergy rule.",
                    "sources": [_source(allergy, "penicillin allergy"), _source(medication, "amoxicillin")],
                    "clinician_final_control": True,
                }
            )
    return conflicts
