from tests.helpers import ServiceTestCase


class TestSelfLearningImportance(ServiceTestCase):
    def test_clinician_highlight_increases_similar_future_priority(self):
        first = self.service.scribe(
            "clinician-lim",
            "patient-maya",
            "Persistent nocturnal wheeze is disrupting sleep. No fever.",
            "doctor_consult",
        )
        future = self.service.scribe(
            "clinician-lim",
            "patient-maya",
            "Persistent nocturnal wheeze continues despite inhaler use.",
            "doctor_consult",
        )
        before = self.service.importance_score_for_entry("clinician-lim", future["id"])
        quote = "Persistent nocturnal wheeze"
        start = first["content"].index(quote)
        self.service.create_highlight(
            "clinician-lim",
            first["id"],
            start,
            start + len(quote),
            "Repeated respiratory symptom",
            status="accepted",
        )
        after = self.service.importance_score_for_entry("clinician-lim", future["id"])
        self.assertGreater(after["learned_boost"], before["learned_boost"])
        self.assertGreater(after["score"], before["score"])
