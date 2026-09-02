from carenote.security import prepare_llm_payload
from tests.helpers import ServiceTestCase


class TestPHIRedaction(ServiceTestCase):
    def test_names_ids_and_phones_are_removed_before_llm_boundary(self):
        raw = "Patient: Maya Tan. NRIC S1234567D. Call +65 9123 4567 about cough."
        payload = prepare_llm_payload(raw, known_names=["Maya Tan"])
        self.assertNotIn("Maya Tan", payload["redacted_text"])
        self.assertNotIn("S1234567D", payload["redacted_text"])
        self.assertNotIn("9123 4567", payload["redacted_text"])
        self.assertEqual(payload["redaction_counts"], {"names": 1, "ids": 1, "phones": 1})

    def test_scribe_audit_contains_hash_and_counts_not_raw_transcript(self):
        raw = "Maya Tan says cough is worse. Phone 91234567."
        entry = self.service.scribe("clinician-lim", "patient-maya", raw, "doctor_consult")
        self.assertNotIn("Maya Tan", entry["content"])
        rows = self.db.query_all("SELECT metadata_json FROM audit_log WHERE action='scribe.created'")
        self.assertEqual(len(rows), 1)
        self.assertNotIn(raw, rows[0]["metadata_json"])
        self.assertIn("payload_sha256", rows[0]["metadata_json"])

    def test_patient_session_is_captured_without_exposing_raw_ai_note(self):
        before = self.db.query_one("SELECT COUNT(*) AS total FROM tasks")["total"]
        result = self.service.scribe(
            "patient-maya-user",
            "patient-maya",
            "Maya Tan reports cough. Call 9123 4567 if needed.",
            "patient_session",
        )
        self.assertEqual(result["status"], "queued_for_clinical_review")
        self.assertEqual(result["visibility"], "internal")
        self.assertIsNotNone(result["review_task_id"])
        after = self.db.query_one("SELECT COUNT(*) AS total FROM tasks")["total"]
        self.assertEqual(after, before + 1)
        task = self.db.query_one("SELECT * FROM tasks WHERE id=?", (result["review_task_id"],))
        self.assertEqual(task["source_entry_id"], result["id"])
        self.assertEqual(task["status"], "open")
        patient_view = self.service.get_care_note("patient-maya-user", "patient-maya")
        self.assertFalse(any(item["id"] == result["id"] for item in patient_view["entries"]))
