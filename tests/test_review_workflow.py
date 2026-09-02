from carenote.errors import ConflictError, ForbiddenError
from tests.helpers import ServiceTestCase


class TestReviewWorkflow(ServiceTestCase):
    def test_consistency_watcher_emits_two_source_alert_in_bounded_queue(self):
        care = self.service.get_care_note("clinician-lim", "patient-maya")
        conflict = next(item for item in care["conflicts"] if item["status"] == "suggested")

        self.assertEqual(conflict["rule_id"], "drug_allergy_penicillin_amoxicillin")
        self.assertEqual(len(conflict["sources"]), 2)
        self.assertEqual(conflict["sources"][0]["quote"].lower(), "penicillin allergy")
        self.assertEqual(conflict["sources"][1]["quote"].lower(), "amoxicillin")
        self.assertLessEqual(len(care["review_queue"]), care["review_limit"])
        self.assertEqual(care["review_queue"][0]["kind"], "contradiction")
        self.assertTrue(all(item.get("reason") for item in care["review_queue"]))

    def test_conflict_decision_requires_two_source_evidence(self):
        care = self.service.get_care_note("clinician-lim", "patient-maya")
        conflict_id = care["conflicts"][0]["id"]
        passport = self.service.get_conflict_passport("clinician-lim", "patient-maya", conflict_id)

        self.assertIsNotNone(passport["evidence_token"])
        with self.assertRaises(ForbiddenError):
            self.service.decide_conflict(
                "clinician-lim", "patient-maya", conflict_id, "acknowledged", passport["evidence_token"] + "x"
            )
        decided = self.service.decide_conflict(
            "clinician-lim", "patient-maya", conflict_id, "acknowledged", passport["evidence_token"]
        )
        self.assertEqual(decided["status"], "acknowledged")
        self.assertEqual(decided["decided_by_name"], "Dr Adrian Lim")
        refreshed = self.service.get_care_note("clinician-lim", "patient-maya")
        self.assertFalse(any(item["kind"] == "contradiction" for item in refreshed["review_queue"]))

    def test_verification_is_append_only_and_bound_to_current_version(self):
        care = self.service.get_care_note("clinician-lim", "patient-maya")
        allergy = next(item for item in care["entries"] if item["id"] == "entry-allergy")
        self.assertEqual(allergy["freshness"]["state"], "fresh")
        self.assertEqual(allergy["freshness"]["verification_count"], 1)

        self.service.edit_entry(
            "clinician-lim",
            "entry-allergy",
            "Confirmed penicillin allergy: urticaria after amoxicillin; reviewed for this visit.",
            1,
        )
        changed = self.service.get_care_note("clinician-lim", "patient-maya")
        allergy = next(item for item in changed["entries"] if item["id"] == "entry-allergy")
        self.assertEqual(allergy["freshness"]["state"], "never_verified")
        with self.assertRaises(ConflictError):
            self.service.verify_entry("clinician-lim", "entry-allergy", 1)
        verified = self.service.verify_entry("clinician-lim", "entry-allergy", 2)
        self.assertEqual(verified["freshness"]["state"], "fresh")
        self.assertEqual(
            self.db.query_one("SELECT COUNT(*) AS total FROM entry_verifications WHERE entry_id='entry-allergy'")[
                "total"
            ],
            2,
        )
        with self.assertRaises(ForbiddenError):
            self.service.verify_entry("staff-jia", "entry-allergy", 2)

    def test_previsit_brief_and_redaction_preview_are_source_grounded(self):
        brief = self.service.get_previsit_brief("clinician-lim", "patient-maya")
        self.assertEqual(brief["mode"], "deterministic_source_assembly")
        self.assertTrue(brief["safety_facts"])
        self.assertTrue(brief["open_tasks"])
        self.assertTrue(brief["consistency_alerts"])
        self.assertTrue(all(item["entry_id"] for item in brief["recent_changes"]))

        audit_before = self.db.query_one("SELECT COUNT(*) AS total FROM audit_log")["total"]
        preview = self.service.preview_redaction(
            "clinician-lim",
            "patient-maya",
            "Maya Tan NRIC S1234567D, call 9123 4567. Is the inhaler related?",
        )
        self.assertNotIn("Maya Tan", preview["redacted_text"])
        self.assertNotIn("S1234567D", preview["redacted_text"])
        self.assertNotIn("9123 4567", preview["redacted_text"])
        self.assertFalse(preview["persisted"])
        self.assertFalse(preview["external_call"])
        audit_after = self.db.query_one("SELECT COUNT(*) AS total FROM audit_log")["total"]
        self.assertEqual(audit_after, audit_before)

        patient_note = self.service.create_entry(
            "patient-maya-user",
            "patient-maya",
            {"type": "patient_insight", "content": "Could the new inhaler be causing the cough?"},
        )
        task = self.db.query_one("SELECT title FROM tasks WHERE id=?", (patient_note["review_task_id"],))
        self.assertEqual(task["title"], "Patient question awaiting response")

