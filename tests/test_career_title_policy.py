from __future__ import annotations

import unittest

from sources.tbank import is_target_title as tbank_is_target_title
from sources.vk import is_target_title as vk_is_target_title
from sources.yandex import is_target_title as yandex_is_target_title


TITLE_CHECKERS = (
    tbank_is_target_title,
    vk_is_target_title,
    yandex_is_target_title,
)


class CareerTitlePolicyTests(unittest.TestCase):
    def test_non_standard_technical_management_titles_reach_evaluator(self):
        titles = (
            "Руководитель группы LLM-агента поддержки",
            "IT Team Lead в команду разработки AI-сервисов",
            "Руководитель ML-продукта в AI-центр",
            "Технический руководитель по данным — Платформа обслуживания",
            "Менеджер по сопровождению продуктовых запусков в MAX",
            "Platform Lead",
            "Head of AI Platform",
        )
        for checker in TITLE_CHECKERS:
            for title in titles:
                with self.subTest(checker=checker.__module__, title=title):
                    self.assertTrue(checker(title))

    def test_fallback_does_not_open_commercial_or_generic_roles(self):
        titles = (
            "Старший менеджер по продажам в Сервисы продуктивности VK Tech",
            "Коммерческий директор в Бизнес-приложения VK Tech",
            "Руководитель маркетинга крупного бизнеса",
            "Менеджер по работе с ключевыми клиентами",
            "Backend Developer",
        )
        for checker in TITLE_CHECKERS:
            for title in titles:
                with self.subTest(checker=checker.__module__, title=title):
                    self.assertFalse(checker(title))

    def test_intern_and_junior_rejection_stays_strict(self):
        titles = (
            "Менеджер продукта AI (стажёр)",
            "Junior Technical Project Manager",
            "AI Platform Manager Intern",
        )
        for checker in TITLE_CHECKERS:
            for title in titles:
                with self.subTest(checker=checker.__module__, title=title):
                    self.assertFalse(checker(title))


if __name__ == "__main__":
    unittest.main()
