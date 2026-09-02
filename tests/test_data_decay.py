from datetime import datetime, timezone

from carenote.errors import ForbiddenError
from tests.helpers import ServiceTestCase


class TestDataDecay(ServiceTestCase):
    def test_retention_lens_explains_hot_warm_and_cold_records(self):
        now = datetime(2026, 8, 27, tzinfo=timezone.utc)
        preview = self.service.decay_preview("clinician-lim", "patient-maya", now=now)
        original_count = len(preview)
        by_entry = {item["entry_id"]: item for item in preview}

        self.assertEqual(by_entry["entry-allergy"]["tier"], "hot")
        self.assertTrue(by_entry["entry-allergy"]["protected"])
        self.assertEqual(by_entry["entry-annual-review"]["tier"], "warm")
        self.assertFalse(by_entry["entry-annual-review"]["protected"])
        self.assertEqual(by_entry["entry-legacy-referral"]["tier"], "cold")

        self.db.seed_demo()
        self.assertEqual(
            len(self.service.decay_preview("clinician-lim", "patient-maya", now=now)),
            original_count,
        )

        with self.assertRaises(ForbiddenError):
            self.service.decay_preview("staff-jia", "patient-maya", now=now)
