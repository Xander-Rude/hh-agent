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
    def test_igaming_alone_is_not_unwanted_domain(self) -> None:
        result = build_shadow_scores(
            make_extraction(unwanted_domain_status="unknown"),
            salary_from=None,
            salary_to=None,
            salary_currency=None,
            description=(
                "Наш клиент разрабатывает B2B-решения для партнеров в сфере "
                "iGaming. Ищем Project Manager для управления dev/QA-командой, "
                "рисками и полным циклом разработки."
            ),
        )
        self.assertNotIn("unwanted_domain", result.hard_stops)

    def test_explicit_betting_is_global_stop_even_with_igaming_word(self) -> None:
        result = build_shadow_scores(
            make_extraction(unwanted_domain_status="unknown"),
            salary_from=None,
            salary_to=None,
            salary_currency=None,
            description=(
                "iGaming platform for sportsbook and online betting. "
                "Project Manager owns delivery and releases."
            ),
        )
        self.assertIn("unwanted_domain", result.hard_stops)
        self.assertEqual(result.routing_class, "SKIP")

    def test_explicit_crypto_is_global_stop_when_llm_misses_it(self) -> None:
        result = build_shadow_scores(
            make_extraction(unwanted_domain_status="unknown"),
            salary_from=None,
            salary_to=None,
            salary_currency=None,
            description=(
                "Project Manager для crypto exchange и Web3 продукта. "
                "Управление разработкой, релизами и интеграциями."
            ),
        )
        self.assertIn("unwanted_domain", result.hard_stops)
        self.assertEqual(result.routing_class, "SKIP")

    def test_video_gaming_without_gambling_marker_is_not_unwanted_domain(self) -> None:
        result = build_shadow_scores(
            make_extraction(unwanted_domain_status="unknown"),
            salary_from=None,
            salary_to=None,
            salary_currency=None,
            description=(
                "Project Manager игровой студии. Разработка video gaming "
                "platform, управление backend/frontend, QA и релизами. "
                "Полный цикл IT-проекта и команда разработки."
            ),
        )
        self.assertNotIn("unwanted_domain", result.hard_stops)

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

    def test_business_analysis_family_is_noncore_even_with_project_lifecycle(self) -> None:
        result = build_shadow_scores(
            make_extraction(
                role_family_primary="BUSINESS_ANALYSIS",
                primary_object="project",
                project_lifecycle_ownership="full",
            ),
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

    def test_consistency_validator_catches_business_analyst_disguised_as_pm(self) -> None:
        extraction = make_extraction(
            role_family_primary="PROJECT_CORE",
            primary_object="project",
            project_lifecycle_ownership="full",
        )
        issues = extraction_consistency_issues(
            extraction,
            vacancy=(
                "Title: Senior/Lead Business Analyst\n"
                "Requirements analysis, BRD, User Stories, solution design, "
                "expert support of implementation."
            ),
        )
        self.assertTrue(
            any("business/system-analysis signals" in item for item in issues)
        )

    def test_pm_with_requirements_artifacts_is_not_automatically_business_analysis(self) -> None:
        extraction = make_extraction()
        issues = extraction_consistency_issues(
            extraction,
            vacancy=(
                "Title: Project Manager\n"
                "Own project budget, schedule, delivery team and risks. "
                "Also coordinate requirements and User Stories."
            ),
        )
        self.assertFalse(
            any("business/system-analysis signals" in item for item in issues)
        )

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

    def test_consistency_validator_catches_mandatory_hardware_domain_as_other(self) -> None:
        extraction = make_extraction(
            requirements=[
                RequirementEvidence(
                    name="Hardware/Electronics Knowledge",
                    category="other",
                    criticality="non_negotiable",
                    evidence_visibility="UNCONFIRMED",
                    match_quality="none",
                    source_text=(
                        "понимание процессов разработки печатных плат, "
                        "конструкторской документации"
                    ),
                )
            ]
        )
        issues = extraction_consistency_issues(extraction)
        self.assertTrue(
            any(
                "mandatory domain-specific technical expertise" in item
                for item in issues
            )
        )

        corrected = make_extraction(
            requirements=[
                RequirementEvidence(
                    name="Hardware/Electronics Knowledge",
                    category="exact_domain",
                    criticality="non_negotiable",
                    evidence_visibility="UNCONFIRMED",
                    match_quality="none",
                    source_text=(
                        "понимание процессов разработки печатных плат, "
                        "конструкторской документации"
                    ),
                )
            ]
        )
        result = build_shadow_scores(
            corrected,
            salary_from=None,
            salary_to=None,
            salary_currency=None,
            description="x" * 500,
        )
        self.assertIn("mandatory_exact_domain", result.hard_stops)
        self.assertNotIn(
            result.routing_class,
            {"CLEAN_STRONG", "CLEAN_REVIEW"},
        )

    def test_domain_validator_does_not_flag_hardware_team_composition(self) -> None:
        extraction = make_extraction(
            requirements=[
                RequirementEvidence(
                    name="Cross-functional coordination",
                    category="other",
                    criticality="non_negotiable",
                    evidence_visibility="CV_DIRECT",
                    match_quality="full",
                    source_text=(
                        "координация команды: программисты, конструкторы, "
                        "схемотехники и технологи"
                    ),
                )
            ]
        )
        issues = extraction_consistency_issues(extraction)
        self.assertFalse(
            any(
                "mandatory domain-specific technical expertise" in item
                for item in issues
            )
        )

    def test_business_function_role_is_noncore(self) -> None:
        result = build_shadow_scores(
            make_extraction(
                role_family_primary="BUSINESS_FUNCTION",
                primary_object="business_function",
                project_lifecycle_ownership="full",
                clean_role_class="noncore",
                role_confidence=1.0,
            ),
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

    def test_consistency_validator_catches_business_function_disguised_as_it_pm(self) -> None:
        extraction = make_extraction(
            role_family_primary="PROJECT_CORE",
            primary_object="project",
            project_lifecycle_ownership="full",
            technical_context_fit="strong",
        )
        vacancy = """
        Title: Менеджер проектов карьеры
        Формулировать карьерные цели по трудоустройству, отслеживать метрики
        карьерных инструментов, анализировать путь студента (CJM).
        Выстраивать работу с партнёрами по трудоустройству, собирать обратную
        связь работодателей и оптимизировать процессы трудоустройства.
        Вести коммуникацию с разработчиками и аналитиками по развитию CRM и
        карьерного кабинета.
        """
        issues = extraction_consistency_issues(
            extraction,
            vacancy=vacancy,
        )
        self.assertTrue(
            any("non-IT business-function outcome signals" in item for item in issues)
        )

    def test_consistency_validator_allows_hr_tech_it_delivery_with_explicit_sdlc(self) -> None:
        extraction = make_extraction(
            role_family_primary="PROJECT_CORE",
            primary_object="project",
            project_lifecycle_ownership="full",
            technical_context_fit="strong",
        )
        vacancy = """
        Title: IT Project Manager HR Tech
        Развиваем карьерные сервисы и метрики трудоустройства, работаем с
        работодателями, CJM и CRM. Руководитель владеет delivery IT-системы:
        разработка backend/frontend, тестирование и релиз в production,
        управляет требованиями, архитектурой и deployment.
        """
        issues = extraction_consistency_issues(
            extraction,
            vacancy=vacancy,
        )
        self.assertFalse(
            any("non-IT business-function outcome signals" in item for item in issues)
        )

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

    def test_consistency_validator_catches_executive_support_disguised_as_project(self) -> None:
        extraction = make_extraction(
            role_family_primary="PROJECT_CORE",
            primary_object="project",
            project_lifecycle_ownership="full",
        )
        vacancy = """
        Title: Executive AI Assistant / Координатор CEO
        Стать операционной опорой генерального директора.
        Готовить CEO к встречам и решениям, формировать briefing-материалы.
        Вести систему поручений и follow-up, управлять входящим потоком
        информации для CEO. Координировать отдельные инициативы и сроки.
        """
        issues = extraction_consistency_issues(
            extraction,
            vacancy=vacancy,
        )
        self.assertTrue(
            any("executive-support/operating-cadence" in item for item in issues)
        )

    def test_executive_operations_role_is_noncore(self) -> None:
        result = build_shadow_scores(
            make_extraction(
                role_family_primary="EXECUTIVE_OPERATIONS",
                primary_object="executive_support",
                project_lifecycle_ownership="partial",
                clean_role_class="noncore",
            ),
            salary_from=None,
            salary_to=None,
            salary_currency=None,
            description="x" * 500,
        )
        self.assertIn("role_family_noncore", result.hard_stops)
        self.assertEqual(result.routing_class, "SKIP")

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

    def test_consistency_rejects_unwanted_affinity_when_domain_passes(self) -> None:
        extraction = make_extraction(
            domain_affinity="unwanted",
            unwanted_domain_status="pass",
        )
        issues = extraction_consistency_issues(
            extraction,
            vacancy="Project Manager for a digital B2B platform.",
        )
        self.assertTrue(
            any("domain_affinity=unwanted" in issue for issue in issues)
        )

    def test_consistency_aligns_failed_unwanted_domain_with_affinity(self) -> None:
        extraction = make_extraction(
            domain_affinity="weak",
            unwanted_domain_status="fail",
        )
        issues = extraction_consistency_issues(
            extraction,
            vacancy="Project Manager for an online casino betting platform.",
        )
        self.assertTrue(
            any("unwanted_domain_status=fail" in issue for issue in issues)
        )

    def test_non_it_project_family_is_noncore_even_with_full_lifecycle(self) -> None:
        result = build_shadow_scores(
            make_extraction(
                role_family_primary="NON_IT_PROJECT",
                primary_object="project",
                project_lifecycle_ownership="full",
                technical_context_fit="weak",
                domain_affinity="weak",
                role_confidence=1.0,
            ),
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

    def test_weak_technical_context_blocks_clean_project(self) -> None:
        result = build_shadow_scores(
            make_extraction(
                role_family_primary="PROJECT_CORE",
                primary_object="project",
                project_lifecycle_ownership="full",
                technical_context_fit="weak",
                domain_affinity="weak",
                role_confidence=1.0,
            ),
            salary_from=None,
            salary_to=None,
            salary_currency=None,
            description="x" * 500,
        )
        self.assertIn("technical_context_weak", result.hard_stops)
        self.assertNotIn(
            result.routing_class,
            {"CLEAN_STRONG", "CLEAN_REVIEW"},
        )

    def test_missing_nonnegotiable_other_requirement_blocks_clean(self) -> None:
        result = build_shadow_scores(
            make_extraction(
                requirements=[
                    RequirementEvidence(
                        name="Mandatory domain artifact knowledge",
                        category="other",
                        criticality="non_negotiable",
                        evidence_visibility="UNCONFIRMED",
                        match_quality="none",
                        source_text="Required knowledge of domain-specific artifacts",
                    )
                ],
                role_confidence=1.0,
            ),
            salary_from=None,
            salary_to=None,
            salary_currency=None,
            description="x" * 500,
        )
        self.assertIn("mandatory_requirement_missing", result.hard_stops)
        self.assertNotIn(
            result.routing_class,
            {"CLEAN_STRONG", "CLEAN_REVIEW"},
        )

    def test_consistency_validator_catches_non_it_project_disguised_as_clean(self) -> None:
        extraction = make_extraction(
            role_family_primary="PROJECT_CORE",
            primary_object="project",
            project_lifecycle_ownership="full",
            technical_context_fit="weak",
            domain_affinity="weak",
        )
        issues = extraction_consistency_issues(
            extraction,
            vacancy=(
                "Руководитель строительного проекта. Организация СМР, "
                "работа с чертежами, подрядчиками и исполнительной документацией."
            ),
        )
        self.assertTrue(
            any("weak technical context" in item for item in issues)
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
