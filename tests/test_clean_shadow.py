from __future__ import annotations

import unittest

from app.clean_shadow import (
    CleanShadowEvaluator,
    CleanShadowExtraction,
    LearnedPatternReview,
    RequirementEvidence,
    build_shadow_scores,
    extraction_consistency_issues,
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

    def test_primary_project_object_overrides_bad_role_family(self) -> None:
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
        self.assertNotIn("role_family_noncore", result.hard_stops)
        self.assertGreaterEqual(result.fit_score, 82)
        self.assertEqual(result.routing_class, "CLEAN_STRONG")

    def test_work_auth_requirement_does_not_invent_location_stop(self) -> None:
        extraction = make_extraction(
            requirements=[
                RequirementEvidence(
                    name="Risk management",
                    category="work_auth",
                    criticality="non_negotiable",
                    evidence_visibility="UNCONFIRMED",
                    match_quality="none",
                    source_text="Управление рисками и изменениями",
                )
            ],
            location_work_auth_status="pass",
        )
        result = build_shadow_scores(
            extraction,
            salary_from=None,
            salary_to=None,
            salary_currency=None,
            description="x" * 500,
        )
        self.assertNotIn("location_work_auth", result.hard_stops)

    def test_consistency_validator_catches_5097_shape(self) -> None:
        extraction = make_extraction(
            role_family_primary="IT_FUNCTION_LEADERSHIP",
            primary_object="project",
            clean_role_class="core",
            requirements=[
                RequirementEvidence(
                    name="Budget management",
                    category="work_auth",
                    criticality="non_negotiable",
                    evidence_visibility="UNCONFIRMED",
                    match_quality="none",
                    source_text="Управление сроками, бюджетом и ресурсами",
                    candidate_evidence="RECRUITER_VISIBLE_RESUME",
                )
            ],
        )
        issues = extraction_consistency_issues(extraction)
        self.assertGreaterEqual(len(issues), 3)

    def test_consistency_validator_catches_it_function_disguised_as_project(self) -> None:
        extraction = make_extraction(
            role_family_primary="PROJECT_CORE",
            primary_object="project",
            project_lifecycle_ownership="full",
        )
        vacancy = """
        Разработка и реализация стратегии развития IT-направления компании.
        Управление IT-инфраструктурой и работой сотрудников IT-направления.
        Обеспечение информационной безопасности, резервного копирования и
        бесперебойной работы ключевых систем. Параллельно руководитель ведёт
        проекты автоматизации, интеграции и внедрения новых сервисов.
        """
        issues = extraction_consistency_issues(
            extraction,
            vacancy=vacancy,
        )
        self.assertTrue(
            any("ongoing IT-function ownership" in item for item in issues)
        )

    def test_consistency_validator_allows_normal_it_project_scope(self) -> None:
        extraction = make_extraction()
        vacancy = """
        Вести ERP-проект от требований до результата, управлять backlog,
        ставить задачи разработчикам, планировать релизы и интеграции,
        синхронизировать бизнес и IT.
        """
        self.assertEqual(
            extraction_consistency_issues(
                extraction,
                vacancy=vacancy,
            ),
            [],
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

    def test_seniority_mismatch_blocks_clean(self) -> None:
        result = build_shadow_scores(
            make_extraction(
                complexity_seniority="mismatch",
                role_confidence=1.0,
            ),
            salary_from=None,
            salary_to=None,
            salary_currency=None,
            description="x" * 500,
        )
        self.assertIn("seniority_mismatch", result.hard_stops)
        self.assertNotIn(
            result.routing_class,
            {"CLEAN_STRONG", "CLEAN_REVIEW"},
        )
        self.assertIsNone(result.invite_score)

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

    def test_learned_patterns_only_change_explanations(self) -> None:
        extraction = make_extraction()
        before = build_shadow_scores(
            extraction,
            salary_from=None,
            salary_to=None,
            salary_currency=None,
            description="x" * 500,
        )

        evaluator = CleanShadowEvaluator(
            llm=object(),
            learned_patterns=[
                {
                    "pattern_key": "pm-visible-lifecycle",
                    "pattern_type": "positive",
                    "statement": "Visible full lifecycle PM evidence converted better.",
                    "support_count": 5,
                    "confidence_score": 80,
                }
            ],
        )
        evaluator._apply_learned_pattern_review(
            extraction,
            LearnedPatternReview(
                relevant_pattern_keys=[
                    "pm-visible-lifecycle",
                    "invented-key",
                ],
                positive_signals=[
                    "Historically similar visible lifecycle evidence was useful."
                ],
                risks=[
                    "Treat as historical context, not a guarantee."
                ],
            ),
        )

        after = build_shadow_scores(
            extraction,
            salary_from=None,
            salary_to=None,
            salary_currency=None,
            description="x" * 500,
        )

        self.assertEqual(before, after)
        self.assertEqual(extraction.role_family_primary, "PROJECT_CORE")
        self.assertEqual(
            extraction.learned_pattern_keys,
            ["pm-visible-lifecycle"],
        )
        self.assertIn(
            "[learned] Historically similar visible lifecycle evidence was useful.",
            extraction.top_invite_reasons,
        )
        self.assertIn(
            "[learned] Treat as historical context, not a guarantee.",
            extraction.invite_risks,
        )

    def test_learned_review_without_valid_pattern_key_is_ignored(self) -> None:
        extraction = make_extraction()
        evaluator = CleanShadowEvaluator(
            llm=object(),
            learned_patterns=[
                {
                    "pattern_key": "known-key",
                    "pattern_type": "positive",
                    "statement": "Known pattern.",
                    "support_count": 4,
                    "confidence_score": 75,
                }
            ],
        )
        evaluator._apply_learned_pattern_review(
            extraction,
            LearnedPatternReview(
                relevant_pattern_keys=["hallucinated-key"],
                positive_signals=["Hallucinated historical benefit."],
                risks=["Hallucinated historical risk."],
            ),
        )

        self.assertEqual(extraction.learned_pattern_keys, [])
        self.assertEqual(extraction.learned_positive_signals, [])
        self.assertEqual(extraction.learned_risks, [])
        self.assertFalse(
            any(
                item.startswith("[learned]")
                for item in extraction.top_invite_reasons
            )
        )
        self.assertFalse(
            any(
                item.startswith("[learned]")
                for item in extraction.invite_risks
            )
        )

    def test_company_key_normalization(self) -> None:
        self.assertEqual(
            normalize_company_key("ООО «Ozon Офис и Коммерция»"),
            "ozon офис и коммерция",
        )


if __name__ == "__main__":
    unittest.main()
