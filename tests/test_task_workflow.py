from carenote.errors import ForbiddenError
from tests.helpers import ServiceTestCase


class TestTaskWorkflow(ServiceTestCase):
    def test_clinician_can_complete_and_reopen_task_with_audit(self):
        before = self.service.get_care_note("clinician-lim", "patient-maya")
        before_score = next(
            item for item in before["importance"] if item["entry_id"] == "entry-staff-spirometry"
        )

        completed = self.service.update_task_status("clinician-lim", "task-spirometry", "done")
        self.assertEqual(completed["status"], "done")
        self.assertEqual(completed["completed_by"], "clinician-lim")
        self.assertEqual(completed["completed_by_name"], "Dr Adrian Lim")
        self.assertIsNotNone(completed["completed_at"])

        after = self.service.get_care_note("clinician-lim", "patient-maya")
        after_score = next(
            item for item in after["importance"] if item["entry_id"] == "entry-staff-spirometry"
        )
        self.assertFalse(any(task["status"] == "open" for task in after["tasks"]))
        self.assertLess(after_score["base_score"], before_score["base_score"])
        audit = self.service.audit_for_patient("clinician-lim", "patient-maya")
        self.assertTrue(any(event["action"] == "task.completed" for event in audit))

        reopened = self.service.update_task_status("clinician-lim", "task-spirometry", "open")
        self.assertEqual(reopened["status"], "open")
        self.assertIsNone(reopened["completed_by"])
        self.assertIsNone(reopened["completed_at"])
        audit = self.service.audit_for_patient("clinician-lim", "patient-maya")
        self.assertTrue(any(event["action"] == "task.reopened" for event in audit))

    def test_task_completion_respects_assignment_clinic_and_role(self):
        for actor_id in ("staff-jia", "staff-birch", "admin-acacia", "patient-maya-user"):
            with self.subTest(actor_id=actor_id):
                with self.assertRaises(ForbiddenError):
                    self.service.update_task_status(actor_id, "task-spirometry", "done")
