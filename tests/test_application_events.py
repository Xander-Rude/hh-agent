import unittest

from app.application_events import career_transition_allowed


class CareerTransitionTests(unittest.TestCase):
    def test_workflow_invitation_is_not_human_response(self):
        self.assertTrue(
            career_transition_allowed("viewed", "workflow_invited")
        )
        self.assertFalse(
            career_transition_allowed("human_response", "workflow_invited")
        )

    def test_transport_submit_cannot_regress_viewed_state(self):
        self.assertFalse(
            career_transition_allowed("viewed", "submitted")
        )

    def test_rejection_is_terminal_for_platform_states(self):
        self.assertFalse(
            career_transition_allowed("rejected", "workflow_invited")
        )
        self.assertTrue(
            career_transition_allowed("rejected", "human_response")
        )


if __name__ == "__main__":
    unittest.main()
