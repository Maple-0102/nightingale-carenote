import json

from tests.helpers import ServiceTestCase


class TestRevisionHistory(ServiceTestCase):
    def test_edit_increments_version_and_revert_restores_prior_content(self):
        original = self.service.create_entry(
            "clinician-lim",
            "patient-maya",
            {"type": "clinician_note", "section_key": "plan", "content": "Original plan."},
        )
        edited = self.service.edit_entry(
            "clinician-lim", original["id"], "Updated plan.", expected_version=1
        )
        self.assertEqual(edited["version"], 2)

        reverted = self.service.revert_entry(
            "clinician-lim", original["id"], target_version=1, expected_version=2
        )
        self.assertEqual(reverted["version"], 3)
        self.assertEqual(reverted["content"], "Original plan.")
        versions = self.service.list_versions("clinician-lim", original["id"])
        self.assertEqual([row["version"] for row in versions], [3, 2, 1])

    def test_audit_log_is_metadata_only(self):
        secret_text = "Clinical free text must not enter the audit log."
        entry = self.service.create_entry(
            "staff-jia",
            "patient-maya",
            {"type": "staff_note", "content": secret_text},
        )
        self.service.edit_entry("staff-jia", entry["id"], "Changed clinical text.", 1)
        rows = self.db.query_all("SELECT metadata_json FROM audit_log")
        serialized = " ".join(row["metadata_json"] for row in rows)
        self.assertNotIn(secret_text, serialized)
        self.assertNotIn("Changed clinical text", serialized)
        for row in rows:
            metadata = json.loads(row["metadata_json"])
            self.assertFalse({"content", "body", "quote", "raw_text"}.intersection(metadata))
        visible_events = self.service.audit_for_patient("clinician-lim", "patient-maya")
        self.assertGreaterEqual(len(visible_events), 2)
        self.assertTrue(all("metadata" in event for event in visible_events))
