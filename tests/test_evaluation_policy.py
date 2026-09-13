from app.evaluation_policy import apply_management_policy
from app.models import VacancyEvaluation


RESUME = "Program Management, PMO, управление портфелем 30+ проектов, управление IT-проектами."


def make_result(red_flags=None, gaps=None, must_have_missing=None):
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
