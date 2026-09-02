from concurrent.futures import ThreadPoolExecutor

from carenote.errors import ConflictError
from tests.helpers import ServiceTestCase


class TestConcurrentEdits(ServiceTestCase):
    def test_different_role_owned_sections_do_not_overwrite_each_other(self):
        staff_entry = self.service.create_entry(
            "staff-jia", "patient-maya", {"type": "staff_note", "content": "Staff baseline."}
        )
        clinician_entry = self.service.create_entry(
            "clinician-lim",
            "patient-maya",
            {"type": "clinician_note", "content": "Clinician baseline."},
        )

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(
                self.service.edit_entry,
                "staff-jia",
                staff_entry["id"],
                "Staff concurrent update.",
                1,
            )
            second = pool.submit(
                self.service.edit_entry,
                "clinician-lim",
                clinician_entry["id"],
                "Clinician concurrent update.",
                1,
            )
            staff_result = first.result()
            clinician_result = second.result()

        self.assertEqual(staff_result["content"], "Staff concurrent update.")
        self.assertEqual(clinician_result["content"], "Clinician concurrent update.")

    def test_same_section_conflict_has_deterministic_reject_strategy(self):
        entry = self.service.create_entry(
            "staff-jia", "patient-maya", {"type": "staff_note", "content": "Shared baseline."}
        )

        def attempt(content):
            try:
                result = self.service.edit_entry("staff-jia", entry["id"], content, 1)
                return ("saved", result["version"])
            except ConflictError as exc:
                return ("conflict", exc.details["current_version"])

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, ["Update A", "Update B"]))

        self.assertEqual(sorted(status for status, _ in results), ["conflict", "saved"])
        self.assertTrue(all(version == 2 for _, version in results))
