import unittest
from types import SimpleNamespace
from unittest.mock import patch

import telegram_cover_letter_patch as cover_patch

from app.vacancy_url import (
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


class _FakeSession:
    def __init__(self, vacancy):
        self.vacancy = vacancy

    def get(self, model, ident):
        return self.vacancy if ident == self.vacancy.id else None

    def close(self):
        pass


class TelegramManualCoverPolicyTests(unittest.TestCase):
    def _snapshot_and_vacancy(self):
        vacancy = SimpleNamespace(
            id=77,
            title="AI Project Manager",
            company="Example",
            description="Lead AI delivery and cross-functional teams.",
        )
        snapshot = cover_patch.VacancySnapshot(
            title=vacancy.title,
            company=vacancy.company,
            url="https://hh.ru/vacancy/123",
            description=vacancy.description,
            vacancy_id=vacancy.id,
        )
        return snapshot, vacancy

    def test_old_manual_request_bypasses_policy_skipped_artifact(self):
        snapshot, vacancy = self._snapshot_and_vacancy()
        session = _FakeSession(vacancy)
        generated = SimpleNamespace(final_text="manual cover")

        with (
            patch.object(cover_patch, "_find_cached_snapshot", return_value=snapshot),
            patch.object(cover_patch, "SessionLocal", return_value=session),
            patch.object(cover_patch, "current_clean_assessment", return_value=None),
            patch.object(
                cover_patch,
                "get_or_enqueue_artifact",
                return_value=SimpleNamespace(status="policy_skipped"),
            ),
            patch.object(
                cover_patch,
                "generate_cover_letter_text",
                return_value=generated,
            ) as generate,
            patch.object(
                cover_patch,
                "get_or_generate_cover_letter",
            ) as canonical,
            patch("pathlib.Path.read_text", return_value="resume"),
        ):
            result = cover_patch.create_cover_letter_for_url(snapshot.url)

        self.assertEqual(result.cover_letter, "manual cover")
        self.assertFalse(result.used_cached_evaluation)
        generate.assert_called_once()
        canonical.assert_not_called()

    def test_clean_policy_skipped_keeps_existing_canonical_behavior(self):
        snapshot, vacancy = self._snapshot_and_vacancy()
        session = _FakeSession(vacancy)
        assessment = SimpleNamespace(id=99)

        with (
            patch.object(cover_patch, "_find_cached_snapshot", return_value=snapshot),
            patch.object(cover_patch, "SessionLocal", return_value=session),
            patch.object(
                cover_patch,
                "current_clean_assessment",
                return_value=assessment,
            ),
            patch.object(
                cover_patch,
                "get_or_enqueue_artifact",
                return_value=SimpleNamespace(status="policy_skipped"),
            ),
            patch.object(
                cover_patch,
                "get_or_generate_cover_letter",
                return_value=("canonical clean", True),
            ) as canonical,
            patch.object(
                cover_patch,
                "generate_cover_letter_text",
            ) as generate,
        ):
            result = cover_patch.create_cover_letter_for_url(snapshot.url)

        self.assertEqual(result.cover_letter, "canonical clean")
        canonical.assert_called_once()
        generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
