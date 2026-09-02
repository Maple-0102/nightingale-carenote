from carenote.errors import ForbiddenError, ValidationError
from tests.helpers import ServiceTestCase


class TestTimeMachine(ServiceTestCase):
    def test_reconstructs_glance_before_spirometry_arrived(self):
        snapshot = self.service.care_note_as_of("clinician-lim", "patient-maya", "2026-08-25T09:00:00Z")
        ids = {item["entry_id"] for item in snapshot["glance"]}
        self.assertIn("entry-allergy", ids)
        self.assertIn("entry-ai-session", ids)
        # Spirometry was created at 09:04, so it must not appear at 09:00.
        self.assertNotIn("entry-staff-spirometry", ids)
        offset_snapshot = self.service.care_note_as_of(
            "clinician-lim", "patient-maya", "2026-08-25T17:00:00+08:00"
        )
        self.assertEqual(snapshot["glance"], offset_snapshot["glance"])

    def test_reconstructs_historical_entry_content(self):
        before = self.service.care_note_as_of("clinician-lim", "patient-maya", "2026-08-25T08:30:00Z")
        self.assertNotIn("entry-ai-session", {entry["id"] for entry in before["entries"]})

        after = self.service.care_note_as_of("clinician-lim", "patient-maya", "2026-08-25T09:10:00Z")
        spirometry = next(entry for entry in after["entries"] if entry["id"] == "entry-staff-spirometry")
        # The v2 content (changed at 09:10) is in effect; the earlier v1 text is not.
        self.assertEqual(spirometry["version"], 2)
        self.assertIn("Assigned to @DrLim", spirometry["content"])

    def test_time_machine_is_not_patient_facing_and_rejects_future(self):
        with self.assertRaises(ForbiddenError):
            self.service.care_note_as_of("patient-maya-user", "patient-maya", "2026-08-25T09:00:00Z")
        with self.assertRaises(ValidationError):
            self.service.care_note_as_of("clinician-lim", "patient-maya", "2099-01-01T00:00:00Z")
        with self.assertRaises(ValidationError):
            self.service.care_note_as_of("clinician-lim", "patient-maya", "2026-08-25T09:00:00")
