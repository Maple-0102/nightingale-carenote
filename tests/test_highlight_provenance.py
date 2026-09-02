from carenote.errors import ConflictError, ForbiddenError
from tests.helpers import ServiceTestCase


class TestHighlightProvenance(ServiceTestCase):
    def test_highlight_resolves_to_exact_immutable_span(self):
        entry = self.service.create_entry(
            "staff-jia",
            "patient-maya",
            {
                "type": "staff_note",
                "content": "Patient requested a same-day call about worsening nocturnal cough.",
                "risk_level": "high",
            },
        )
        quote = "worsening nocturnal cough"
        start = entry["content"].index(quote)
        highlight = self.service.create_highlight(
            "clinician-lim",
            entry["id"],
            start,
            start + len(quote),
            "Worsening respiratory symptom",
            status="accepted",
        )
        self.assertEqual(highlight["resolved_quote"], quote)
        self.assertEqual(highlight["entry_version"], 1)
        self.assertIn(entry["id"], highlight["timeline_anchor"])

        self.service.edit_entry(
            "staff-jia", entry["id"], "The staff note was clarified after review.", expected_version=1
        )
        still_resolves = self.service.resolve_highlight("clinician-lim", highlight["id"])
        self.assertEqual(still_resolves["resolved_quote"], quote)
        self.assertEqual(still_resolves["entry_version"], 1)

    def test_rejected_ai_suggestion_retains_decider_and_source_trail(self):
        passport = self.service.get_highlight_passport("clinician-lim", "highlight-cough")
        decided = self.service.decide_highlight(
            "clinician-lim", "highlight-cough", "rejected", passport["evidence_token"]
        )

        self.assertEqual(decided["status"], "rejected")
        self.assertEqual(decided["decided_by"], "clinician-lim")
        self.assertEqual(decided["decided_by_name"], "Dr Adrian Lim")
        self.assertIsNotNone(decided["decided_at"])
        self.assertEqual(decided["resolved_quote"], "dry cough that is worse at night")

        care_note = self.service.get_care_note("clinician-lim", "patient-maya")
        stored = next(item for item in care_note["highlights"] if item["id"] == "highlight-cough")
        self.assertEqual(stored["status"], "rejected")
        self.assertEqual(stored["decided_by_name"], "Dr Adrian Lim")

    def test_only_a_clinician_can_make_one_final_ai_decision(self):
        with self.assertRaises(ForbiddenError):
            self.service.decide_highlight("staff-jia", "highlight-cough", "accepted")

        passport = self.service.get_highlight_passport("clinician-lim", "highlight-cough")
        self.service.decide_highlight(
            "clinician-lim", "highlight-cough", "accepted", passport["evidence_token"]
        )
        with self.assertRaises(ConflictError):
            self.service.decide_highlight(
                "clinician-lim", "highlight-cough", "rejected", passport["evidence_token"]
            )

    def test_trust_passport_connects_source_decision_learning_and_retention(self):
        review = self.service.get_highlight_passport("clinician-lim", "highlight-cough")
        self.assertIsNotNone(review["evidence_token"])
        self.service.decide_highlight(
            "clinician-lim", "highlight-cough", "accepted", review["evidence_token"]
        )
        passport = self.service.get_highlight_passport("clinician-lim", "highlight-cough")

        self.assertEqual(passport["evidence"]["entry_version"], 1)
        self.assertEqual(passport["evidence"]["quote"], "dry cough that is worse at night")
        self.assertEqual(passport["decision"]["status"], "accepted")
        self.assertEqual(passport["decision"]["decided_by_name"], "Dr Adrian Lim")
        self.assertTrue(passport["decision"]["final"])
        self.assertTrue(passport["authority"]["clinician_final_control"])
        self.assertIn("base_rank", passport["learning"])
        self.assertIn("current_rank", passport["learning"])
        self.assertIn("rank_change", passport["learning"])
        self.assertLess(passport["learning"]["current_rank"], passport["learning"]["base_rank"])
        self.assertGreater(passport["learning"]["rank_change"], 0)
        weights = {
            row["feature"]: row["weight"]
            for row in self.db.query_all("SELECT feature,weight FROM importance_signals")
        }
        self.assertEqual(weights["entity:chief_complaint:cough"], 0.35)
        self.assertEqual(weights["kw:cough"], 0.0875)
        self.assertIn(passport["retention"]["tier"], {"hot", "warm", "cold"})
        self.assertEqual(passport["privacy"]["mode"], "local_deterministic_no_external_call")
        self.assertEqual(passport["privacy"]["evidence"], "seeded_synthetic_record")
        self.assertEqual(passport["learning"]["influence_budget"]["cap"], 4.0)
        self.assertIsNone(passport["evidence_token"])

        with self.assertRaises(ForbiddenError):
            self.service.get_highlight_passport("patient-maya-user", "highlight-cough")

    def test_decision_requires_freshly_witnessed_evidence_after_source_edit(self):
        entry = self.service.create_entry(
            "staff-jia",
            "patient-maya",
            {"type": "staff_note", "content": "External result requires review.", "risk_level": "high"},
        )
        highlight = self.service.create_highlight(
            "staff-jia", entry["id"], 0, len("External result"), "Review external result", status="suggested"
        )
        first = self.service.get_highlight_passport("clinician-lim", highlight["id"])
        self.service.edit_entry("staff-jia", entry["id"], "External result was superseded.", 1)
        with self.assertRaises(ConflictError):
            self.service.decide_highlight(
                "clinician-lim", highlight["id"], "accepted", first["evidence_token"]
            )
        reopened = self.service.get_highlight_passport("clinician-lim", highlight["id"])
        self.assertTrue(reopened["evidence"]["superseded"])
        decided = self.service.decide_highlight(
            "clinician-lim", highlight["id"], "accepted", reopened["evidence_token"]
        )
        self.assertEqual(decided["status"], "accepted")
