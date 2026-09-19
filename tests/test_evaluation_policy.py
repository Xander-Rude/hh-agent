from app.evaluation_policy import apply_management_policy
from app.models import VacancyEvaluation


RESUME = "Program Management, PMO, управление портфелем 30+ проектов, управление IT-проектами."


def make_result(
    red_flags=None,
    gaps=None,
    must_have_missing=None,
    it_relevant=None,
):
    return VacancyEvaluation(
        score=85,
        decision="reject",
        role_match=85,
        seniority_match=85,
        domain_match=80,
        responsibility_match=90,
        must_have_missing=must_have_missing or [],
        nice_to_have_missing=[],
        strengths=["Управление портфелем IT-проектов"],
        gaps=gaps or [],
        red_flags=red_flags or [],
        summary="Кандидат не подходит для данной вакансии.",
        recommendation="Не стоит откликаться.",
        it_relevant=it_relevant,
        cover_letter="",
    )


def test_office_5_2_is_not_a_red_flag():
    result = make_result(
        red_flags=["Работа в офисе 5/2 без возможности удаленки."],
        gaps=["Только офис, без удаленки."],
    )
    vacancy = "Название: Project Manager\nОписание: Работа в офисе 5/2. Управление IT-проектами."

    updated = apply_management_policy(result, resume=RESUME, vacancy=vacancy)

    assert updated.red_flags == []
    assert updated.gaps == []
    assert updated.decision == "apply"


def test_ai_ml_head_does_not_get_false_critical_stack_mismatch():
    result = make_result(
        red_flags=["Критический mismatch по техническому стеку: Python, Git, Data Science; профиль — чистый менеджмент."],
        gaps=["Нет подтвержденных навыков Python и Git."],
        must_have_missing=["Python, Git, основы обучения моделей"],
    )
    vacancy = (
        "Название: Руководитель направления развития AI/ML\n"
        "Описание: управление портфелем ИИ-проектов, формирование требований, "
        "координация команд. Требуется знание Python, Git и основ обучения моделей."
    )

    updated = apply_management_policy(result, resume=RESUME, vacancy=vacancy)

    assert updated.red_flags == []
    assert updated.gaps == []
    assert updated.must_have_missing == []
    assert updated.role_match >= 90
    assert updated.decision == "apply"


def test_hands_on_ml_requirement_keeps_red_flag():
    result = make_result(
        red_flags=["Требуется лично писать код на Python и обучать ML-модели."],
        must_have_missing=["Python и hands-on ML-разработка"],
    )
    vacancy = (
        "Название: Руководитель направления развития AI/ML\n"
        "Описание: управление командой; необходимо лично писать код на Python "
        "и обучать модели машинного обучения."
    )

    updated = apply_management_policy(result, resume=RESUME, vacancy=vacancy)

    assert updated.red_flags
    assert updated.must_have_missing
    assert updated.decision == "reject"


def test_non_it_communication_design_is_rejected_before_management_floors():
    result = make_result(
        it_relevant=False,
    )
    result.decision = "apply"
    result.role_match = 95
    result.domain_match = 80
    result.responsibility_match = 90
    result.cover_letter = "Не должно уйти работодателю"

    vacancy = (
        "Название: Менеджер проектов в коммуникационном дизайне\n"
        "Описание: ведение рекламных кампаний, коммуникационного дизайна, "
        "подрядчиков и производства креативных материалов."
    )

    updated = apply_management_policy(
        result,
        resume=RESUME,
        vacancy=vacancy,
    )

    assert updated.decision == "reject"
    assert updated.role_match <= 25
    assert updated.domain_match <= 20
    assert updated.score < 70
    assert updated.cover_letter == ""
    assert any("не относится к it" in item.lower() for item in updated.red_flags)


def test_strong_non_it_scope_overrides_false_llm_optimism():
    result = make_result(
        it_relevant=True,
    )
    result.decision = "apply"
    result.role_match = 95
    result.domain_match = 85
    result.responsibility_match = 90

    vacancy = (
        "Название: Руководитель проекта по операционной эффективности (нефтегаз)\n"
        "Описание: повышение эффективности производственных процессов, Lean, "
        "работа с добывающими активами и операционными командами."
    )

    updated = apply_management_policy(
        result,
        resume=RESUME,
        vacancy=vacancy,
    )

    assert updated.decision == "reject"
    assert updated.score < 70
    assert updated.cover_letter == ""


def test_semantic_it_relevance_can_rescue_ambiguous_wording():
    result = make_result(
        it_relevant=True,
    )
    vacancy = (
        "Название: Project Manager\n"
        "Описание: ownership of delivery for internal business systems, "
        "cross-functional teams, roadmap, risks and production rollout."
    )

    updated = apply_management_policy(
        result,
        resume=RESUME,
        vacancy=vacancy,
    )

    assert updated.decision == "apply"
    assert updated.role_match >= 90


def test_explicit_it_scope_rescues_llm_false_negative():
    result = make_result(
        it_relevant=False,
    )
    vacancy = (
        "Название: Project Manager\n"
        "Описание: управление разработкой backend, API-интеграциями и "
        "выводом программного обеспечения в production."
    )

    updated = apply_management_policy(
        result,
        resume=RESUME,
        vacancy=vacancy,
    )

    assert updated.decision == "apply"
    assert updated.role_match >= 90
