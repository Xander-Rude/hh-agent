import unittest

from telegram_cover_letter_patch import (
    canonicalize_url,
    extract_first_url,
    identify_vacancy,
)


class TelegramCoverLetterUrlTests(unittest.TestCase):
    def test_extract_first_url_from_plain_message(self):
        text = "Сделай письмо https://hh.ru/vacancy/12345678 пожалуйста"
        self.assertEqual(
            extract_first_url(text),
            "https://hh.ru/vacancy/12345678",
        )

    def test_extract_url_strips_chat_punctuation(self):
        self.assertEqual(
            extract_first_url("Вот ссылка: https://team.vk.company/vacancy/52657/)."),
            "https://team.vk.company/vacancy/52657/",
        )

    def test_identify_hh_vacancy(self):
        identity = identify_vacancy("https://hh.ru/vacancy/12345678?from=share")
        self.assertEqual(identity.source, "hh")
        self.assertEqual(identity.external_id, "12345678")

    def test_identify_yandex_vacancy(self):
        identity = identify_vacancy(
            "https://yandex.ru/jobs/vacancies/project-manager-987654/"
        )
        self.assertEqual(identity.source, "yandex")
        self.assertEqual(identity.external_id, "987654")

    def test_identify_vk_vacancy(self):
        identity = identify_vacancy("https://team.vk.company/vacancy/52657/")
        self.assertEqual(identity.source, "vk")
        self.assertEqual(identity.external_id, "52657")

    def test_unknown_site_is_generic(self):
        identity = identify_vacancy("https://careers.example.com/jobs/42")
        self.assertIsNone(identity.source)
        self.assertIsNone(identity.external_id)

    def test_canonicalize_removes_fragment_and_normalizes_host(self):
        self.assertEqual(
            canonicalize_url("HTTPS://HH.RU/vacancy/123#description"),
            "https://hh.ru/vacancy/123",
        )


if __name__ == "__main__":
    unittest.main()
