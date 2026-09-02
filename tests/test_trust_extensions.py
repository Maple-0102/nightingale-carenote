from carenote.errors import ConflictError, ForbiddenError
from tests.helpers import ServiceTestCase


class TestTrustExtensions(ServiceTestCase):
    def test_teach_back_is_patient_submitted_version_bound_and_clinician_decided(self):
        response = (
            "I will bring my inhaler. I will seek urgent care for severe breathlessness, "
            "blue lips, or difficulty speaking."
        )
        attempt = self.service.submit_teach_back(
            "patient-maya-user", "entry-patient-instructions", response
        )
        self.assertEqual(attempt["status"], "pending")
        self.assertEqual(attempt["coverage"], 1.0)
        self.assertFalse(attempt["missing_concepts"])

        clinician_view = self.service.get_care_note("clinician-lim", "patient-maya")
        self.assertTrue(any(item["kind"] == "teach_back" for item in clinician_view["review_queue"]))
        decided = self.service.decide_teach_back("clinician-lim", attempt["id"], "confirmed")
        self.assertEqual(decided["status"], "confirmed")
        with self.assertRaises(ForbiddenError):
            self.service.decide_teach_back("staff-jia", attempt["id"], "needs_clarification")

        audit_blob = " ".join(
            row["metadata_json"]
            for row in self.db.query_all("SELECT metadata_json FROM audit_log")
        )
        self.assertNotIn(response, audit_blob)

    def test_teach_back_decision_fails_if_instruction_changed(self):
        attempt = self.service.submit_teach_back(
            "patient-maya-user",
            "entry-patient-instructions",
            "I will bring the inhaler and seek urgent care if I cannot breathe.",
        )
        self.service.edit_entry(
            "clinician-lim",
            "entry-patient-instructions",
            "Bring your inhaler. Call emergency services for severe breathlessness.",
            1,
        )
        with self.assertRaises(ConflictError):
            self.service.decide_teach_back("clinician-lim", attempt["id"], "confirmed")

    def test_patient_access_report_exposes_metadata_not_clinical_content(self):
        self.service.get_care_note("clinician-lim", "patient-maya")
        self.service.get_care_note("staff-jia", "patient-maya")
        report = self.service.patient_access_report("patient-maya-user", "patient-maya")
        self.assertEqual(report["total_accesses"], 2)
        self.assertEqual({item["role"] for item in report["visitors"]}, {"clinician", "staff"})
        self.assertTrue(all("content" not in item and "metadata" not in item for item in report["visitors"]))
        with self.assertRaises(ForbiddenError):
            self.service.patient_access_report("clinician-lim", "patient-maya")

    def test_audit_hash_chain_detects_metadata_tampering(self):
        self.service.get_care_note("clinician-lim", "patient-maya")
        valid = self.service.verify_audit_chain("clinician-lim", "patient-maya")
        self.assertTrue(valid["valid"])
        self.assertGreater(valid["event_count"], 0)

        with self.db.transaction() as conn:
            conn.execute("UPDATE audit_log SET metadata_json='{}' WHERE id=(SELECT MIN(id) FROM audit_log)")
        broken = self.service.verify_audit_chain("clinician-lim", "patient-maya")
        self.assertFalse(broken["valid"])
        self.assertIsNotNone(broken["first_broken_event_id"])

    def test_security_sandbox_runs_only_safe_local_probes(self):
        report = self.service.run_security_sandbox("clinician-lim", "patient-maya")
        self.assertEqual(report["mode"], "safe_local_policy_probes")
        self.assertTrue(report["all_blocked"])
        self.assertGreaterEqual(len(report["scenarios"]), 6)
        with self.assertRaises(ForbiddenError):
            self.service.run_security_sandbox("staff-jia", "patient-maya")
