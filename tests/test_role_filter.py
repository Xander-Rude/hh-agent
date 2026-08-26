import unittest

from app.role_filter import check_role_title, normalize


class RoleFilterTests(unittest.TestCase):
    def test_normalize_lowercases_and_collapses_whitespace(self):
        self.assertEqual(
            normalize("  Senior   Product Manager  "),
            "senior product manager",
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

    def test_blocks_internships(self):
        result = check_role_title("Product Manager Intern", {})
        self.assertFalse(result.passed)
        self.assertIn("intern", result.reason.lower())

    def test_blocked_marker_has_priority_over_allowed_marker(self):
        result = check_role_title("Senior Product Manager / Developer", {})
        self.assertFalse(result.passed)
        self.assertIn("developer", result.reason.lower())

    def test_custom_allowed_marker_extends_defaults(self):
        result = check_role_title(
            "Transformation Manager",
            {"allowed_role_markers": ["transformation manager"]},
        )
        self.assertTrue(result.passed)

    def test_unknown_role_is_rejected(self):
        result = check_role_title("Finance Director", {})
        self.assertFalse(result.passed)
        self.assertIn("не соответствует", result.reason)


if __name__ == "__main__":
    unittest.main()
