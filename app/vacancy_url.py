from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse


URL_RE = re.compile(r"https?://[^\s<>\]\[\"']+", re.IGNORECASE)
HH_VACANCY_RE = re.compile(r"^/vacancy/(?P<id>\d+)(?:/|$)", re.IGNORECASE)
YANDEX_VACANCY_RE = re.compile(
    r"^/jobs/vacancies/.+-(?P<id>\d+)(?:/|$)",
    re.IGNORECASE,
)
VK_VACANCY_RE = re.compile(r"^/vacancy/(?P<id>\d+)(?:/|$)", re.IGNORECASE)


@dataclass(frozen=True)
class VacancyIdentity:
    source: str | None
    external_id: str | None


def extract_first_url(text: str) -> str | None:
    match = URL_RE.search(text or "")
    if match is None:
        return None
    return match.group(0).rstrip(".,;:!?)]}")


def canonicalize_url(url: str) -> str:
    parsed = urlparse((url or "").strip())
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    if scheme not in {"http", "https"} or not host:
        raise ValueError("Некорректная ссылка вакансии.")

    port = parsed.port
    netloc = host
    if port is not None and not (
        (scheme == "http" and port == 80)
        or (scheme == "https" and port == 443)
    ):
        netloc = f"{host}:{port}"

    path = parsed.path or "/"
    return urlunparse((scheme, netloc, path, "", parsed.query, ""))


def identify_vacancy(url: str) -> VacancyIdentity:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path or "/"

    if host == "hh.ru" or host.endswith(".hh.ru"):
        match = HH_VACANCY_RE.match(path)
        return VacancyIdentity("hh", match.group("id") if match else None)

    if host in {"yandex.ru", "www.yandex.ru"}:
        match = YANDEX_VACANCY_RE.match(path)
        return VacancyIdentity("yandex", match.group("id") if match else None)

    if host == "team.vk.company":
        match = VK_VACANCY_RE.match(path)
        return VacancyIdentity("vk", match.group("id") if match else None)

    return VacancyIdentity(None, None)
