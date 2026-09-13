import unittest
import test_evaluation_policy as cases


class EvaluationPolicyCITests(unittest.TestCase):
    def test_office(self):
        cases.test_office_5_2_is_not_a_red_flag()

    def test_ai_ml_management(self):
        cases.test_ai_ml_head_does_not_get_false_critical_stack_mismatch()

    def test_ai_ml_hands_on(self):
        cases.test_hands_on_ml_requirement_keeps_red_flag()
