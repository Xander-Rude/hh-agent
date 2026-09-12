from datetime import datetime

from backfill_hard_filter_appeals import (
    DEFAULT_SINCE,
    is_final_reason,
    parse_args,
)


def test_default_since_is_september_first_2026():
    assert DEFAULT_SINCE == "2026-09-01"
    assert datetime.strptime(DEFAULT_SINCE, "%Y-%m-%d")


def test_salary_reject_is_final():
    assert is_final_reason("Зарплата до 250000 RUB ниже минимума 350000 RUB")


def test_company_blacklist_is_final():
    assert is_final_reason("Компания в blacklist: Example")


def test_role_reject_is_not_final():
    assert not is_final_reason("Неподходящая роль: Python Developer")
