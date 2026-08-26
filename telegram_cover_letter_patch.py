from __future__ import annotations

import asyncio
import html
import ipaddress
import json
import re
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

import httpx
from sqlalchemy import select
from telegram.ext import MessageHandler, filters

from app.db import Evaluation, SessionLocal, Vacancy
from app.evaluator import (
    MAX_LLM_ATTEMPTS,
    _detect_language,
    _extract_response_text,
    _normalize_cover_letter,
)
from app.llm import LLMProvider
from app.preferences import load_preferences


ROOT = Path(__file__).resolve().parent
RESUME_PATH = ROOT / "data" / "resume.txt"
REQUEST_TIMEOUT = 30.0
MAX_GENERIC_REDIRECTS = 5
MAX_PAGE_CHARS = 120_000
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/142.0.0.0 Safari/537.36"
)
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


@dataclass(frozen=True)
class VacancySnapshot:
    title: str
    company: str | None
    url: str
    description: str
    cached_cover_letter: str | None = None


@dataclass(frozen=True)
class CoverLetterResult:
    title: str
    company: str | None
    url: str
    cover_letter: str
    used_cached_evaluation: bool


class VisibleTextParser(HTMLParser):
    BLOCK_TAGS = {
        "article",
        "br",
        "div",
        "h1",
        "h2",
        "h3",
        "li",
        "main",
        "ol",
        "p",
        "section",
        "ul",
    }
    SKIP_TAGS = {"script", "style", "noscript", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.h1_parts: list[str] = []
        self.title_parts: list[str] = []
        self.skip_depth = 0
        self.in_h1 = False
        self.in_title = False

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in self.SKIP_TAGS:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag == "h1":
            self.in_h1 = True
        elif tag == "title":
            self.in_title = True
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.SKIP_TAGS:
            if self.skip_depth:
                self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if tag == "h1":
            self.in_h1 = False
        elif tag == "title":
            self.in_title = False
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        value = " ".join(html.unescape(data).split())
        if not value:
            return
        self.parts.extend((value, " "))
        if self.in_h1:
            self.h1_parts.append(value)
        if self.in_title:
            self.title_parts.append(value)

    @property
    def title(self) -> str:
        value = " ".join(self.h1_parts).strip()
        if value:
            return value
        return " ".join(self.title_parts).strip()

    @property
    def text(self) -> str:
        lines: list[str] = []
        for raw in "".join(self.parts).splitlines():
            line = " ".join(raw.split())
            if line and (not lines or line != lines[-1]):
                lines.append(line)
        return "\n".join(lines)


def extract_first_url(text: str) -> str | None:
    match = URL_RE.search(text or "")
    if match is None:
        return None
    return match.group(0).rstrip(".,;:!?)]}")


def canonicalize_url(url: str) -> str:
    parsed = urlparse((url or "").strip())
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    if not scheme or not host:
        raise ValueError("Некорректная ссылка.")

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


def _latest_cover_letter(session, vacancy_id: int) -> str | None:
    evaluation = session.scalars(
        select(Evaluation)
        .where(Evaluation.vacancy_id == vacancy_id)
        .order_by(Evaluation.created_at.desc(), Evaluation.id.desc())
        .limit(1)
    ).first()
    if evaluation is None:
        return None
    value = (evaluation.cover_letter or "").strip()
    return value or None


def _find_cached_snapshot(url: str) -> VacancySnapshot | None:
    identity = identify_vacancy(url)
    session = SessionLocal()
    try:
        vacancy = None
        if identity.source and identity.external_id:
            vacancy = session.scalars(
                select(Vacancy)
                .where(
                    Vacancy.source == identity.source,
                    Vacancy.external_id == identity.external_id,
                )
                .order_by(Vacancy.id.desc())
                .limit(1)
            ).first()

        if vacancy is None:
            vacancy = session.scalars(
                select(Vacancy)
                .where(Vacancy.url == url)
                .order_by(Vacancy.id.desc())
                .limit(1)
            ).first()

        if vacancy is None:
            return None

        return VacancySnapshot(
            title=vacancy.title or "Вакансия",
            company=vacancy.company,
            url=vacancy.url or url,
            description=vacancy.description or "",
            cached_cover_letter=_latest_cover_letter(session, vacancy.id),
        )
    finally:
        session.close()


def _html_to_text(value: str) -> str:
    parser = VisibleTextParser()
    parser.feed(value or "")
    return parser.text


def _fetch_hh(url: str, external_id: str) -> VacancySnapshot:
    api_url = f"https://api.hh.ru/vacancies/{external_id}"
    headers = {
        "User-Agent": USER_AGENT,
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    }
    response = httpx.get(api_url, headers=headers, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    payload = response.json()

    title = str(payload.get("name") or "Вакансия").strip()
    employer = payload.get("employer") or {}
    company = str(employer.get("name") or "").strip() or None
    description = _html_to_text(str(payload.get("description") or ""))
    if len(description) < 100:
        raise RuntimeError("HH API вернуло слишком короткое описание вакансии.")

    return VacancySnapshot(
        title=title,
        company=company,
        url=url,
        description=description,
    )


def _fetch_yandex(url: str) -> VacancySnapshot:
    from sources.yandex import USER_AGENT as SOURCE_USER_AGENT
    from sources.yandex import YandexSource

    headers = {
        "User-Agent": SOURCE_USER_AGENT,
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    }
    with httpx.Client(headers=headers, follow_redirects=True) as client:
        title, description = YandexSource._fetch_vacancy(client, url)
    return VacancySnapshot(
        title=title,
        company="Яндекс",
        url=url,
        description=description,
    )


def _fetch_vk(url: str) -> VacancySnapshot:
    from sources.vk import USER_AGENT as SOURCE_USER_AGENT
    from sources.vk import VKSource

    headers = {
        "User-Agent": SOURCE_USER_AGENT,
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    }
    with httpx.Client(headers=headers, follow_redirects=True) as client:
        title, description = VKSource._fetch_vacancy(client, url)
    return VacancySnapshot(
        title=title,
        company="VK",
        url=url,
        description=description,
    )


def _validate_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Поддерживаются только http/https ссылки.")
    if parsed.username or parsed.password:
        raise ValueError("Ссылки с логином/паролем не поддерживаются.")
    if parsed.port not in {None, 80, 443}:
        raise ValueError("Нестандартный порт в ссылке запрещён.")

    host = parsed.hostname
    if not host:
        raise ValueError("В ссылке отсутствует hostname.")

    if host.lower() in {"localhost", "localhost.localdomain"}:
        raise ValueError("Локальные адреса запрещены.")

    try:
        addresses = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("Не удалось определить адрес сайта.") from exc

    if not addresses:
        raise ValueError("Не удалось определить адрес сайта.")

    for item in addresses:
        address = item[4][0].split("%", 1)[0]
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ValueError("Ссылки на локальные/служебные сети запрещены.")


def _safe_generic_get(url: str) -> tuple[str, str]:
    current = url
    headers = {
        "User-Agent": USER_AGENT,
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    }

    with httpx.Client(headers=headers, follow_redirects=False) as client:
        for _ in range(MAX_GENERIC_REDIRECTS + 1):
            _validate_public_url(current)
            response = client.get(current, timeout=REQUEST_TIMEOUT)

            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    raise RuntimeError("Сайт вернул redirect без Location.")
                current = urljoin(current, location)
                continue

            response.raise_for_status()
            content_type = (response.headers.get("content-type") or "").lower()
            if "html" not in content_type and "text" not in content_type:
                raise RuntimeError("Ссылка не ведёт на текстовую HTML-страницу.")
            return current, response.text[:MAX_PAGE_CHARS]

    raise RuntimeError("Слишком много redirect при открытии вакансии.")


def _fetch_generic(url: str) -> VacancySnapshot:
    final_url, page_html = _safe_generic_get(url)
    parser = VisibleTextParser()
    parser.feed(page_html)
    title = parser.title or "Вакансия"
    description = parser.text
    if len(description) < 200:
        raise RuntimeError("Не удалось извлечь описание вакансии со страницы.")
    return VacancySnapshot(
        title=title,
        company=None,
        url=final_url,
        description=description,
    )


def _fetch_snapshot(url: str) -> VacancySnapshot:
    identity = identify_vacancy(url)
    if identity.source == "hh" and identity.external_id:
        return _fetch_hh(url, identity.external_id)
    if identity.source == "yandex" and identity.external_id:
        return _fetch_yandex(url)
    if identity.source == "vk" and identity.external_id:
        return _fetch_vk(url)
    return _fetch_generic(url)


def _build_vacancy_text(snapshot: VacancySnapshot) -> str:
    return (
        f"Название:\n{snapshot.title}\n\n"
        f"Компания:\n{snapshot.company or 'Не указана'}\n\n"
        f"Ссылка:\n{snapshot.url}\n\n"
        f"Описание:\n{snapshot.description}"
    )


def _generate_cover_letter(snapshot: VacancySnapshot) -> str:
    if not RESUME_PATH.exists():
        raise RuntimeError(f"Не найдено резюме: {RESUME_PATH}")

    resume = RESUME_PATH.read_text(encoding="utf-8")
    preferences = load_preferences()
    vacancy_text = _build_vacancy_text(snapshot)
    language = _detect_language(vacancy_text)
    llm = LLMProvider()

    language_rule = (
        "Пиши письмо на русском языке."
        if language == "ru"
        else "Write the letter in English."
    )

    prompt = f"""
Ты пишешь сопроводительное письмо от лица кандидата на конкретную вакансию.

{language_rule}

Правила:
- используй ТОЛЬКО подтверждённые факты из резюме;
- не придумывай технологии, отраслевой опыт, достижения или обязанности;
- письмо должно быть коротким и содержательным: ориентир 700-1200 знаков;
- выбери 2-3 наиболее релевантных факта/результата под задачи вакансии;
- пиши от первого лица;
- русский текст начинай с «Здравствуйте!»;
- не используй «Уважаемый HR/рекрутер», «Уверен, что», «Готов обсудить»;
- не используй placeholders;
- не добавляй подпись и имя кандидата: Python добавит подпись сам;
- не пиши о кандидате в третьем лице;
- стиль деловой, живой, без HR-воды и самовосхваления;
- даже если fit неидеальный, задача сейчас именно написать корректное письмо, а не принять решение об отклике.

РЕЗЮМЕ
{resume[:32000]}

ПРЕДПОЧТЕНИЯ
{json.dumps(preferences, ensure_ascii=False, indent=2)[:6000]}

ВАКАНСИЯ
{vacancy_text[:20000]}

Верни только текст сопроводительного письма без комментариев и markdown.
""".strip()

    last_error: Exception | None = None
    for attempt in range(1, MAX_LLM_ATTEMPTS + 1):
        try:
            response = llm.chat(
                messages=[{"role": "user", "content": prompt}],
            )
            raw = _extract_response_text(response).strip()
            normalized = _normalize_cover_letter(raw, language)
            if normalized:
                return normalized
            last_error = RuntimeError("LLM вернула письмо, не прошедшее нормализацию.")
        except Exception as exc:
            last_error = exc

        print(
            f"[TELEGRAM COVER] attempt {attempt}/{MAX_LLM_ATTEMPTS} failed: "
            f"{last_error}",
            flush=True,
        )

    raise RuntimeError("Не удалось получить корректное сопроводительное письмо.") from last_error


def create_cover_letter_for_url(raw_url: str) -> CoverLetterResult:
    url = canonicalize_url(raw_url)
    cached = _find_cached_snapshot(url)

    if cached is not None and cached.cached_cover_letter:
        return CoverLetterResult(
            title=cached.title,
            company=cached.company,
            url=cached.url,
            cover_letter=cached.cached_cover_letter,
            used_cached_evaluation=True,
        )

    snapshot = cached if cached is not None else _fetch_snapshot(url)
    cover_letter = _generate_cover_letter(snapshot)
    return CoverLetterResult(
        title=snapshot.title,
        company=snapshot.company,
        url=snapshot.url,
        cover_letter=cover_letter,
        used_cached_evaluation=False,
    )


async def vacancy_link_message(update, context) -> None:
    message = update.effective_message
    if message is None:
        return

    url = extract_first_url(message.text or "")
    if url is None:
        return

    progress = await message.reply_text("✍️ Готовлю сопроводительное по вакансии...")

    try:
        result = await asyncio.to_thread(create_cover_letter_for_url, url)
        company_line = f"\n{result.company}" if result.company else ""
        cache_line = "\n\n⚡ Использована уже рассчитанная оценка из базы." if result.used_cached_evaluation else ""
        text = (
            f"✉️ {result.title}{company_line}\n\n"
            f"{result.cover_letter}"
            f"{cache_line}"
        )
        await progress.edit_text(text, disable_web_page_preview=True)
    except Exception as exc:
        print(
            f"[TELEGRAM COVER] ERROR {url}: {type(exc).__name__}: {exc}",
            flush=True,
        )
        await progress.edit_text(
            "❌ Не удалось подготовить сопроводительное.\n\n"
            f"{type(exc).__name__}: {exc}"
        )


def install(bot_module) -> None:
    """Register a text-message handler without rewriting legacy telegram_bot.py."""
    if getattr(bot_module, "_cover_letter_url_patch_installed", False):
        return

    original_build = bot_module.ApplicationBuilder.build

    def build_with_cover_handler(builder, *args, **kwargs):
        application = original_build(builder, *args, **kwargs)
        application.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                vacancy_link_message,
            ),
            group=10,
        )
        return application

    bot_module.ApplicationBuilder.build = build_with_cover_handler
    bot_module._cover_letter_url_patch_installed = True
