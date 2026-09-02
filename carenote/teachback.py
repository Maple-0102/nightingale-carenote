"""Deterministic teach-back coverage checks; never generates clinical advice."""

import re


CONCEPT_RULES = (
    ("bring inhaler", ("bring", "inhaler")),
    ("severe breathlessness", ("severe breathlessness", "very short of breath", "cannot breathe")),
    ("blue lips", ("blue lips", "lips turn blue")),
    ("difficulty speaking", ("difficulty speaking", "cannot speak", "can't speak", "hard to speak")),
    ("seek urgent care", ("urgent care", "emergency", "call for help", "seek help")),
)


def _normalise(text):
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def expected_concepts(instruction):
    source = _normalise(instruction)
    expected = []
    for label, phrases in CONCEPT_RULES:
        if any(phrase in source for phrase in phrases):
            expected.append({"label": label, "phrases": phrases})
    return expected


def assess_teach_back(instruction, response):
    answer = _normalise(response)
    expected = expected_concepts(instruction)
    matched = []
    missing = []
    for concept in expected:
        target = matched if any(phrase in answer for phrase in concept["phrases"]) else missing
        target.append(concept["label"])
    coverage = 1.0 if not expected else len(matched) / len(expected)
    return {
        "matched_concepts": matched,
        "missing_concepts": missing,
        "coverage": round(coverage, 3),
        "screening_result": "ready_for_review" if not missing else "possible_gap",
        "disclaimer": "Keyword coverage only; a clinician makes the final understanding decision.",
    }
