from carenote.errors import ForbiddenError, ValidationError
from tests.helpers import ServiceTestCase


class TestRBACScope(ServiceTestCase):
    def test_staff_and_clinician_cannot_overwrite_each_other(self):
        with self.assertRaises(ForbiddenError):
            self.service.edit_entry(
                "staff-jia", "entry-allergy", "Staff overwrote clinician note", 1
            )
        with self.assertRaises(ForbiddenError):
            self.service.edit_entry(
                "clinician-lim", "entry-staff-spirometry", "Clinician overwrote staff note", 2
            )

    def test_patient_cannot_access_internal_or_raw_ai_content(self):
        care = self.service.get_care_note("patient-maya-user", "patient-maya")
        self.assertTrue(care["entries"])
        self.assertTrue(all(item["visibility"] == "patient" for item in care["entries"]))
        self.assertTrue(all(not item["type"].startswith("ai_") for item in care["entries"]))
        self.assertTrue(all("comments" not in item for item in care["entries"]))
        self.assertEqual(care["highlights"], [])

        with self.assertRaises(ForbiddenError):
            self.service.get_entry("patient-maya-user", "entry-ai-session")

        created = self.service.create_entry(
            "patient-maya-user",
            "patient-maya",
            {"type": "patient_insight", "content": "My cough was worse after dinner."},
        )
        self.assertEqual(created["status"], "queued_for_clinical_review")
        self.assertIsNotNone(created["review_task_id"])
        clinician_view = self.service.get_care_note("clinician-lim", "patient-maya")
        self.assertTrue(any(task["id"] == created["review_task_id"] for task in clinician_view["tasks"]))

    def test_clinic_scope_is_enforced_server_side(self):
        with self.assertRaises(ForbiddenError):
            self.service.get_care_note("staff-birch", "patient-maya")
        malicious_id = '\"><img src=x onerror=alert(1)>'
        with self.assertRaises(ValidationError):
            self.service.create_entry(
                "staff-jia",
                "patient-maya",
                {"id": malicious_id, "type": "staff_note", "content": "Untrusted identifier"},
            )
        self.assertIsNone(self.db.query_one("SELECT id FROM entries WHERE id=?", (malicious_id,)))
