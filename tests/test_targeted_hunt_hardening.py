import pytest

from app.targeted_hunt.service import normalize_company_name, normalize_person_name


def test_company_normalization_is_stable():
    assert normalize_company_name("  Иви / IVI  ") == "иви ivi"


def test_person_normalization_is_case_and_space_insensitive():
    assert normalize_person_name("  Евгений   Россинский ") == normalize_person_name("евгений россинский")


def test_empty_person_normalizes_to_empty_string():
    assert normalize_person_name("") == ""
