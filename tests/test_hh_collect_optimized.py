from hh_collect_optimized import (
    is_obvious_non_target_title,
    is_target_title,
    prioritize_search_queries,
)


def test_obvious_non_target_titles_are_rejected():
    titles = [
        "Руководитель направления бренд-маркетинга",
        "Руководитель направления продаж",
        "HR Business Partner (IT, Админ, Аппарат совета директоров)",
        "Начальник отдела образовательных программ ДПО",
        "Директор по закупкам",
        "Системный администратор / Помощник IT-директора",
    ]

    for title in titles:
        assert is_obvious_non_target_title(title)
        assert not is_target_title(title)


def test_ambiguous_and_senior_titles_stay_eligible():
    titles = [
        "Руководитель направления",
        "Руководитель направления реализации (платформа Яндекс.Смена)",
        "Руководитель направления маркетплейсов",
        "Руководитель департамента",
        "Заместитель директора",
        "Директор",
        "Head of Platform",
        "CTO",
        "CIO",
        "Директор по информационным технологиям",
    ]

    for title in titles:
        assert not is_obvious_non_target_title(title)
        assert is_target_title(title)


def test_search_queries_are_prioritized_without_losing_coverage():
    queries = [
        "Руководитель направления",
        "Project Manager",
        "CTO",
        "IT Business Partner",
        "Project Manager",
        "CIO",
    ]

    result = prioritize_search_queries(queries)

    assert set(result) == {
        "Руководитель направления",
        "Project Manager",
        "CTO",
        "IT Business Partner",
        "CIO",
    }
    assert result[:2] == ["CTO", "CIO"]
    assert result.count("Project Manager") == 1
