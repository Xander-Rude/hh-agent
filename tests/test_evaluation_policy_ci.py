import unittest
import test_evaluation_policy as cases


class EvaluationPolicyCITests(unittest.TestCase):
    def test_office(self):
        cases.test_office_5_2_is_not_a_red_flag()

    def test_ai_ml_management(self):
        cases.test_ai_ml_head_does_not_get_false_critical_stack_mismatch()

    def test_ai_ml_hands_on(self):
        cases.test_hands_on_ml_requirement_keeps_red_flag()

    def test_non_it_communication_design(self):
        cases.test_non_it_communication_design_is_rejected_before_management_floors()

    def test_non_it_strong_scope(self):
        cases.test_strong_non_it_scope_overrides_false_llm_optimism()

    def test_semantic_it_scope(self):
        cases.test_semantic_it_relevance_can_rescue_ambiguous_wording()

    def test_explicit_it_scope(self):
        cases.test_explicit_it_scope_rescues_llm_false_negative()
