"""No-PHI gateway applied before any text can leave for an LLM."""

import hashlib
import re


NRIC_RE = re.compile(r"\b[STFGM]\d{7}[A-Z]\b", re.IGNORECASE)
LABELED_ID_RE = re.compile(r"\b(?:IC|ID|NRIC|FIN)\s*[:#-]?\s*[A-Z0-9-]{5,}\b", re.IGNORECASE)
PHONE_RE = re.compile(r"(?<!\w)(?:\+?65[\s-]?)?[689]\d{3}[\s-]?\d{4}(?!\w)")
LABELED_NAME_RE = re.compile(
    r"\b(?:patient|name)\s*[:=-]\s*([A-Z][A-Za-z'-]+(?:\s+[A-Z][A-Za-z'-]+){0,3})",
    re.IGNORECASE,
)


def redact_phi(text, known_names=None):
    """Return redacted text and counts; raw text is never logged."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    result = text
    counts = {"names": 0, "ids": 0, "phones": 0}

    for name in sorted(set(known_names or []), key=len, reverse=True):
        if not name or len(name.strip()) < 2:
            continue
        pattern = re.compile(r"\b" + re.escape(name.strip()) + r"\b", re.IGNORECASE)
        result, n = pattern.subn("[REDACTED_NAME]", result)
        counts["names"] += n

    def redact_labeled_name(match):
        counts["names"] += 1
        label = match.group(0).split(":", 1)[0] if ":" in match.group(0) else "Name"
        return label + ": [REDACTED_NAME]"

    result = LABELED_NAME_RE.sub(redact_labeled_name, result)
    result, n_nric = NRIC_RE.subn("[REDACTED_ID]", result)
    result, n_id = LABELED_ID_RE.subn("[REDACTED_ID]", result)
    result, n_phone = PHONE_RE.subn("[REDACTED_PHONE]", result)
    counts["ids"] += n_nric + n_id
    counts["phones"] += n_phone
    return result, counts


def prepare_llm_payload(text, known_names=None):
    """Single controlled boundary for any future external LLM call."""
    redacted, counts = redact_phi(text, known_names=known_names)
    digest = hashlib.sha256(redacted.encode("utf-8")).hexdigest()
    return {
        "redacted_text": redacted,
        "redaction_counts": counts,
        "payload_sha256": digest,
        "phi_policy": "names_ids_phones_removed_before_llm",
    }
