import unittest
from unittest.mock import patch

import httpx

import telegram_cover_letter_patch as cover_patch


class TelegramCoverLetterHHFallbackTests(unittest.TestCase):
    def test_hh_403_falls_back_to_public_vacancy_page(self):
        url = "https://hh.ru/vacancy/137383476"
        expected = cover_patch.VacancySnapshot(
            title="Руководитель проекта",
            company=None,
            url=url,
            description="Описание вакансии " * 20,
        )
        response = httpx.Response(
            403,
            request=httpx.Request("GET", "https://api.hh.ru/vacancies/137383476"),
        )

        with patch.object(cover_patch.httpx, "get", return_value=response), patch.object(
            cover_patch,
            "_fetch_generic",
            return_value=expected,
        ) as generic_fetch:
            result = cover_patch._fetch_hh(url, "137383476")

        self.assertIs(result, expected)
        generic_fetch.assert_called_once_with(url)

    def test_hh_404_does_not_mask_missing_vacancy(self):
        url = "https://hh.ru/vacancy/123"
        response = httpx.Response(
            404,
            request=httpx.Request("GET", "https://api.hh.ru/vacancies/123"),
        )

        with patch.object(cover_patch.httpx, "get", return_value=response), patch.object(
            cover_patch,
            "_fetch_generic",
        ) as generic_fetch:
            with self.assertRaises(httpx.HTTPStatusError):
                cover_patch._fetch_hh(url, "123")

        generic_fetch.assert_not_called()

    def test_hh_network_error_falls_back_to_public_vacancy_page(self):
        url = "https://hh.ru/vacancy/137383476"
        expected = cover_patch.VacancySnapshot(
            title="Руководитель проекта",
            company=None,
            url=url,
            description="Описание вакансии " * 20,
        )
        request = httpx.Request("GET", "https://api.hh.ru/vacancies/137383476")

        with patch.object(
            cover_patch.httpx,
            "get",
            side_effect=httpx.ConnectError("boom", request=request),
        ), patch.object(
            cover_patch,
            "_fetch_generic",
            return_value=expected,
        ) as generic_fetch:
            result = cover_patch._fetch_hh(url, "137383476")

        self.assertIs(result, expected)
        generic_fetch.assert_called_once_with(url)


if __name__ == "__main__":
    unittest.main()
