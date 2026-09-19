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

def test_management_policy_preserves_evaluator_cover_letter():
    result = make_result(it_relevant=True)
    result.decision = "review"
    original = (
        "Здравствуйте!\n\n"
        "У меня есть опыт управления IT-проектами полного цикла и работы "
        "с кросс-функциональными командами. Вёл проекты от требований "
        "до запуска в production.\n\n"
        "С уважением,\nАлександр Руденко"
    )
    result.cover_letter = original

    vacancy = (
        "Название: Руководитель цифровых проектов\n"
        "Описание: управление IT-проектами полного цикла, backend, API-интеграции, "
        "кросс-функциональные команды и запуск в production."
    )

    updated = apply_management_policy(
        result,
        resume=RESUME,
        vacancy=vacancy,
    )

    assert updated.decision in {"apply", "review"}
    assert updated.cover_letter == original
    assert "многолетний опыт" not in updated.cover_letter.lower()
    assert "senior/lead" not in updated.cover_letter.lower()


def test_management_policy_preserves_ai_project_context():
    result = make_result(it_relevant=True)
    result.decision = "review"
    project_url = "https://rudenko.one/hh-agent.html"
    original = (
        "Здравствуйте!\n\n"
        "Управляю IT-проектами полного цикла и развиваю собственный AI-agent "
        f"для автоматизации workflow работы с вакансиями: {project_url}.\n\n"
        "С уважением,\nАлександр Руденко"
    )
    result.cover_letter = original

    vacancy = (
        "Название: Руководитель проекта GenAI\n"
        "Описание: управление внедрением LLM, AI-продуктами, roadmap, "
        "разработкой backend и кросс-функциональной командой."
    )

    updated = apply_management_policy(
        result,
        resume=RESUME,
        vacancy=vacancy,
    )

    assert updated.decision in {"apply", "review"}
    assert updated.cover_letter == original
    assert project_url in updated.cover_letter


def test_management_policy_empty_letter_uses_conservative_fallback():
    result = make_result(it_relevant=True)
    result.cover_letter = ""

    vacancy = (
        "Название: Руководитель цифровых проектов\n"
        "Описание: управление IT-проектами полного цикла, интеграции, "
        "roadmap, сроки, кросс-функциональная команда и production."
    )

    updated = apply_management_policy(
        result,
        resume=RESUME,
        vacancy=vacancy,
    )

    assert updated.decision in {"apply", "review"}
    assert "Мой основной профиль - управление IT-проектами" in updated.cover_letter
    assert "многолетний опыт" not in updated.cover_letter.lower()
    assert "senior/lead" not in updated.cover_letter.lower()
    assert "30+" not in updated.cover_letter
    assert "70 человек" not in updated.cover_letter
    assert "40+" not in updated.cover_letter
    assert "C-level" not in updated.cover_letter
    assert "CEO-1" not in updated.cover_letter


def test_management_policy_reject_clears_stale_cover_letter():
    result = make_result(
        it_relevant=True,
        red_flags=["Требуется лично писать код на Python и обучать ML-модели."],
        must_have_missing=["Python и hands-on ML-разработка"],
    )
    result.cover_letter = (
        "Здравствуйте!\n\nЭто письмо не должно пережить финальный reject.\n\n"
        "С уважением,\nАлександр Руденко"
    )

    vacancy = (
        "Название: Руководитель направления развития AI/ML\n"
        "Описание: необходимо лично писать код на Python и обучать "
        "модели машинного обучения."
    )

    updated = apply_management_policy(
        result,
        resume=RESUME,
        vacancy=vacancy,
    )

    assert updated.decision == "reject"
    assert updated.cover_letter == ""

