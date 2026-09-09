import unittest

from app.targeted_hunt.service import normalize_company_name, normalize_person_name


class TargetedHuntHardeningTests(unittest.TestCase):
    def test_company_normalization_is_stable(self):
        self.assertEqual(normalize_company_name("  Иви / IVI  "), "иви ivi")

    def test_person_normalization_is_case_and_space_insensitive(self):
        self.assertEqual(
            normalize_person_name("  Евгений   Россинский "),
            normalize_person_name("евгений россинский"),
        )

    def test_empty_person_normalizes_to_empty_string(self):
        self.assertEqual(normalize_person_name(""), "")


if __name__ == "__main__":
    unittest.main()
