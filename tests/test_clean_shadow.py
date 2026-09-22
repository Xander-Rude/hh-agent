from __future__ import annotations

import unittest

from app.clean_shadow import (
    CleanShadowExtraction,
    RequirementEvidence,
    build_shadow_scores,
    normalize_company_key,
    score_invite,
)


def make_extraction(**overrides) -> CleanShadowExtraction:
    data = {
        "role_family_primary": "PROJECT_CORE",
        "role_family_secondary": None,
        "primary_object": "project",
        "project_lifecycle_ownership": "full",
        "clean_role_class": "core",
        "role_confidence": 0.9,
        "role_rationale": "End-to-end IT project ownership.",
        "complexity_seniority": "strong",
        "technical_context_fit": "strong",
        "domain_affinity": "direct",
        "change_outcome_fit": "strong",
        "role_narrative_coherence": "strong",
        "recent_relevant_evidence": "strong_recent",
        "seniority_autonomy_visibility": "strong",
        "domain_technical_visibility": "direct",
        "visible_differentiators": "strong",
        "cover_surfaced_evidence": "neutral",
        "unwanted_domain_status": "pass",
        "location_work_auth_status": "pass",
        "requirements": [
            {
                "name": "IT project management",
                "category": "other",
                "criticality": "core",
                "evidence_visibility": "CV_DIRECT",
                "match_quality": "full",
                "source_text": "Управление IT-проектами полного цикла",
                "candidate_evidence": "Current PM resume",
            }
        ],
        "top_fit_reasons": [],
        "top_invite_reasons": [],
        "invite_risks": [],
    }
    data.update(overrides)
    return CleanShadowExtraction.model_validate(data)


class CleanShadowTests(unittest.TestCase):
    def test_strong_pm_routes_to_clean(self) -> None:
        result = build_shadow_scores(
            make_extraction(),
            salary_from=None,
            salary_to=None,
            salary_currency=None,
            description="x" * 500,
        )
        self.assertGreaterEqual(result.fit_score, 82)
        self.assertGreaterEqual(result.invite_score or 0, 82)
        self.assertEqual(result.routing_class, "CLEAN_STRONG")
        self.assertEqual(result.hard_stops, ())

    def test_mandatory_stack_blocks_even_with_high_fit(self) -> None:
        extraction = make_extraction(
            requirements=[
                RequirementEvidence(
                    name="1C and GOST 34",
                    category="exact_stack",
                    criticality="non_negotiable",
                    evidence_visibility="UNCONFIRMED",
                    match_quality="none",
                    source_text="Опыт 1С и ГОСТ 34 обязателен",
                )
            ]
        )
        result = build_shadow_scores(
            extraction,
            salary_from=None,
            salary_to=None,
            salary_currency=None,
            description="x" * 500,
        )
        self.assertGreaterEqual(result.fit_score, 82)
        self.assertIsNone(result.invite_score)
        self.assertIn("mandatory_exact_stack", result.hard_stops)
        self.assertNotEqual(result.routing_class, "CLEAN_STRONG")

    def test_role_family_overrides_inconsistent_clean_role_class(self) -> None:
        extraction = make_extraction(
            role_family_primary="IT_FUNCTION_LEADERSHIP",
            primary_object="project",
            clean_role_class="core",
        )
        result = build_shadow_scores(
            extraction,
            salary_from=None,
            salary_to=None,
            salary_currency=None,
            description="x" * 500,
        )
        self.assertIn("role_family_noncore", result.hard_stops)
        self.assertLessEqual(result.fit_score, 60)
        self.assertNotIn(
            result.routing_class,
            {"CLEAN_STRONG", "CLEAN_REVIEW"},
        )

    def test_noncore_product_role_does_not_become_clean(self) -> None:
        extraction = make_extraction(
            role_family_primary="PRODUCT",
            primary_object="product",
            clean_role_class="noncore",
            role_narrative_coherence="incoherent",
            project_lifecycle_ownership="partial",
        )
        result = build_shadow_scores(
            extraction,
            salary_from=None,
            salary_to=None,
            salary_currency=None,
            description="x" * 500,
        )
        self.assertIn("role_family_noncore", result.hard_stops)
        self.assertNotIn(
            result.routing_class,
            {"CLEAN_STRONG", "CLEAN_REVIEW"},
        )

    def test_known_rub_ceiling_below_floor_is_global_skip(self) -> None:
        result = build_shadow_scores(
            make_extraction(),
            salary_from=200_000,
            salary_to=280_000,
            salary_currency="RUB",
            description="x" * 500,
        )
        self.assertIn("salary_floor", result.hard_stops)
        self.assertEqual(result.routing_class, "SKIP")

    def test_internal_only_evidence_does_not_help_invite(self) -> None:
        direct = make_extraction(
            requirements=[
                RequirementEvidence(
                    name="Program scale",
                    category="other",
                    criticality="core",
                    evidence_visibility="CV_DIRECT",
                    match_quality="full",
                )
            ]
        )
        hidden = make_extraction(
            requirements=[
                RequirementEvidence(
                    name="Program scale",
                    category="other",
                    criticality="core",
                    evidence_visibility="INTERNAL_ONLY",
                    match_quality="full",
                )
            ]
        )
        self.assertGreater(score_invite(direct), score_invite(hidden))

    def test_low_confidence_goes_to_review(self) -> None:
        result = build_shadow_scores(
            make_extraction(role_confidence=0.4),
            salary_from=None,
            salary_to=None,
            salary_currency=None,
            description="x" * 500,
        )
        self.assertEqual(result.routing_class, "REVIEW")

    def test_company_key_normalization(self) -> None:
        self.assertEqual(
            normalize_company_key("ООО «Ozon Офис и Коммерция»"),
            "ozon офис и коммерция",
        )


if __name__ == "__main__":
    unittest.main()
