import unittest

from app.role_filter import check_role_title, normalize


class RoleFilterTests(unittest.TestCase):
    def test_normalize_lowercases_and_collapses_whitespace(self):
        self.assertEqual(
            normalize("  Senior   Product Manager  "),
            "senior product manager",
        )

    def test_normalize_unifies_russian_yo_and_dashes(self):
        self.assertEqual(
            normalize("ИТ‑ДИРЕКТОР"),
            "ит-директор",
        )

    def test_allows_standard_english_role(self):
        result = check_role_title("Senior Product Manager", {})
        self.assertTrue(result.passed)

    def test_allows_standard_russian_role(self):
        result = check_role_title("Руководитель проектов", {})
        self.assertTrue(result.passed)

    def test_allows_segmented_product_role(self):
        result = check_role_title("Менеджер B2B-продуктов", {})
        self.assertTrue(result.passed)

    def test_allows_cto(self):
        result = check_role_title("CTO", {})
        self.assertTrue(result.passed)

    def test_allows_chief_technology_officer(self):
        result = check_role_title("Chief Technology Officer", {})
        self.assertTrue(result.passed)

    def test_allows_cio(self):
        result = check_role_title("CIO", {})
        self.assertTrue(result.passed)

    def test_allows_cdto(self):
        result = check_role_title("CDTO", {})
        self.assertTrue(result.passed)

    def test_allows_vp_of_technology(self):
        result = check_role_title("VP of Technology", {})
        self.assertTrue(result.passed)

    def test_allows_managing_director_technology(self):
        result = check_role_title("Managing Director, Technology", {})
        self.assertTrue(result.passed)

    def test_allows_it_director(self):
        result = check_role_title("IT Director", {})
        self.assertTrue(result.passed)

    def test_allows_punctuated_it_director(self):
        result = check_role_title("Director, IT", {})
        self.assertTrue(result.passed)

    def test_allows_head_of_engineering(self):
        result = check_role_title("Head of Engineering", {})
        self.assertTrue(result.passed)

    def test_allows_director_of_developer_platform(self):
        result = check_role_title("Director of Developer Platform", {})
        self.assertTrue(result.passed)

    def test_allows_russian_it_director(self):
        result = check_role_title("ИТ-директор", {})
        self.assertTrue(result.passed)

    def test_allows_russian_director_of_it(self):
        result = check_role_title("Директор по ИТ", {})
        self.assertTrue(result.passed)

    def test_allows_russian_deputy_it_director(self):
        result = check_role_title("Заместитель директора по ИТ", {})
        self.assertTrue(result.passed)

    def test_allows_russian_technical_director(self):
        result = check_role_title("Технический директор", {})
        self.assertTrue(result.passed)

    def test_allows_russian_digital_transformation_director(self):
        result = check_role_title("Директор по цифровой трансформации", {})
        self.assertTrue(result.passed)

    def test_allows_russian_it_department_head(self):
        result = check_role_title("Руководитель департамента ИТ", {})
        self.assertTrue(result.passed)

    def test_allows_russian_information_technology_department_director(self):
        result = check_role_title(
            "Директор департамента информационных технологий",
            {},
        )
        self.assertTrue(result.passed)

    def test_allows_russian_information_systems_management_head(self):
        result = check_role_title(
            "Начальник управления информационных систем",
            {},
        )
        self.assertTrue(result.passed)

    def test_allows_russian_it_development_direction_head(self):
        result = check_role_title("Руководитель направления развития ИТ", {})
        self.assertTrue(result.passed)

    def test_allows_russian_development_department_director(self):
        result = check_role_title("Директор департамента разработки", {})
        self.assertTrue(result.passed)

    def test_allows_russian_it_service_head(self):
        result = check_role_title("Руководитель ИТ-службы", {})
        self.assertTrue(result.passed)

    def test_blocks_internships(self):
        result = check_role_title("Product Manager Intern", {})
        self.assertFalse(result.passed)
        self.assertIn("intern", result.reason.lower())

    def test_blocked_marker_has_priority_over_allowed_marker(self):
        result = check_role_title("Senior Product Manager / Developer", {})
        self.assertFalse(result.passed)
        self.assertIn("developer", result.reason.lower())

    def test_short_blocked_marker_does_not_match_inside_director(self):
        result = check_role_title(
            "IT Director",
            {"blocked_role_markers": ["cto"]},
        )
        self.assertTrue(result.passed)

    def test_custom_allowed_marker_extends_defaults(self):
        result = check_role_title(
            "Transformation Manager",
            {"allowed_role_markers": ["transformation manager"]},
        )
        self.assertTrue(result.passed)

    def test_generic_department_head_is_not_enough(self):
        result = check_role_title("Руководитель департамента маркетинга", {})
        self.assertFalse(result.passed)

    def test_unknown_c_level_role_is_rejected(self):
        result = check_role_title("Chief Marketing Officer", {})
        self.assertFalse(result.passed)
        self.assertIn("не соответствует", result.reason)


if __name__ == "__main__":
    unittest.main()
