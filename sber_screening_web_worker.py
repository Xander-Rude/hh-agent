from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

from app.sber_screening import generate_suggestion, resolve_application_for_vacancy_title
from app.sber_screening_store import DEFAULT_STORE_PATH, SberScreeningStore
from background_common import AgentLock


ROOT = Path(__file__).resolve().parent
LOG_PATH = ROOT / "logs" / "sber_screening_web.log"
SBER_WORKER_LOCK_PATH = ROOT / "data" / "sber_screening_web.lock"


def _configure_windowless_output() -> None:
    force_redirect = Path(sys.executable).name.lower() == "pythonw.exe"
    if not force_redirect and sys.stdout is not None and sys.stderr is not None:
        return
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    stream = LOG_PATH.open(
        "a",
        encoding="utf-8",
        errors="backslashreplace",
        buffering=1,
    )
    if sys.stdout is None or force_redirect:
        sys.stdout = stream
    if sys.stderr is None or force_redirect:
        sys.stderr = stream


_configure_windowless_output()
load_dotenv(ROOT / ".env")

CDP_URL = os.getenv(
    "HH_SBER_TELEGRAM_WEB_CDP_URL",
    "http://127.0.0.1:9223",
).strip()
WEB_URL = os.getenv(
    "HH_SBER_TELEGRAM_WEB_URL",
    "https://web.telegram.org/a/",
).strip()
GIGA_CHAT_ID = os.getenv(
    "HH_SBER_GIGA_CHAT_ID",
    "8491767152",
).strip()
WEB_PROFILE = Path(
    os.getenv(
        "HH_SBER_TELEGRAM_WEB_PROFILE",
        str(ROOT / "data" / "secrets" / "telegram_web_profile"),
    )
)
CHROME_PATH = Path(
    os.getenv(
        "HH_SBER_TELEGRAM_WEB_CHROME",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    )
)
POLL_SECONDS = max(
    0.5,
    float(os.getenv("HH_SBER_APPROVAL_POLL_SECONDS", "0.5")),
)


def _enabled() -> bool:
    return os.getenv("HH_SBER_SCREENING_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _bot_credentials() -> tuple[str, int]:
    bot_token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id_raw = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    if not bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing")
    if not chat_id_raw:
        raise RuntimeError("TELEGRAM_CHAT_ID is missing")
    return bot_token, int(chat_id_raw)


async def _notify(
    *,
    bot_token: str,
    chat_id: int,
    text: str,
    keyboard: dict | None = None,
) -> None:
    payload: dict[str, object] = {
        "chat_id": chat_id,
        "text": text[:4000],
        "disable_web_page_preview": True,
    }
    if keyboard:
        payload["reply_markup"] = keyboard
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()


def _pending_keyboard(turn_id: int, has_suggestion: bool) -> dict:
    action_row = []
    if has_suggestion:
        action_row.append(
            {
                "text": "✅ Отправить",
                "callback_data": f"sber_send:{turn_id}",
            }
        )
    action_row.append(
        {
            "text": "✏️ Свой ответ",
            "callback_data": f"sber_custom:{turn_id}",
        }
    )
    return {
        "inline_keyboard": [
            action_row,
            [
                {
                    "text": "⏭ Не отправлять",
                    "callback_data": f"sber_skip:{turn_id}",
                }
            ],
        ]
    }


def _choice_keyboard(turn_id: int, options: list[str]) -> dict:
    rows = [
        [
            {
                "text": label[:55],
                "callback_data": f"sber_choice:{turn_id}:{index}",
            }
        ]
        for index, label in enumerate(options)
    ]
    rows.append(
        [
            {
                "text": "⏭ Не отправлять",
                "callback_data": f"sber_skip:{turn_id}",
            }
        ]
    )
    return {"inline_keyboard": rows}


def _clean_message_text(value: str) -> str:
    lines = (value or "").replace("\r", "").split("\n")
    while lines and re.fullmatch(r"\d{1,2}:\d{2}", lines[-1].strip()):
        lines.pop()
    return "\n".join(lines).strip()


def _is_terminal_message(value: str) -> bool:
    normalized = re.sub(r"\s+", " ", (value or "").strip().lower())
    return (
        "спасибо за интервью" in normalized
        and "передам ваше резюме" in normalized
    )


def _screening_start_vacancy_title(value: str) -> str | None:
    text = (value or "").strip()
    patterns = [
        (
            r"давайте\s+продолжим\s+общение\s+по\s+вакансии\s+"
            r"(.+?)\.\s*мы\s+остановились\s+на\s+этом\s+месте\s*:"
        ),
        r"получил\s+ваш\s+отклик\s+на\s+позицию\s+(.+?)(?:\.\s*(?:\n|$))",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match is None:
            continue
        title = re.sub(r"\s+", " ", match.group(1)).strip()
        if title:
            return title
    return None


def _session_subject(session: dict) -> str:
    application_id = int(session.get("application_id") or 0)
    if application_id > 0:
        return f"Application #{application_id}"
    vacancy_title = str(session.get("vacancy_title") or "").strip()
    return (
        f"External Giga screening · {vacancy_title}"
        if vacancy_title
        else "External Giga screening"
    )


async def _recent_screening_start_vacancy_title(
    web,
    *,
    after_external_id: int | None,
) -> str | None:
    recent_method = getattr(web, "recent_incoming", None)
    if recent_method is None:
        return None
    recent = await recent_method(limit=20)
    for item in reversed(recent):
        external_id = int(item.get("external_message_id") or 0)
        if after_external_id is not None and external_id <= after_external_id:
            continue
        title = _screening_start_vacancy_title(str(item.get("question") or ""))
        if title:
            return title
    return None


async def _cdp_ready() -> bool:
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            response = await client.get(CDP_URL.rstrip("/") + "/json/version")
        return response.status_code == 200
    except Exception:
        return False


async def _ensure_browser() -> None:
    if await _cdp_ready():
        return
    if not CHROME_PATH.exists():
        raise RuntimeError(f"Chrome not found: {CHROME_PATH}")
    WEB_PROFILE.mkdir(parents=True, exist_ok=True)
    port = CDP_URL.rsplit(":", 1)[-1].strip("/")
    subprocess.Popen(
        [
            str(CHROME_PATH),
            f"--user-data-dir={WEB_PROFILE}",
            "--remote-debugging-address=127.0.0.1",
            f"--remote-debugging-port={port}",
            "--no-first-run",
            "--disable-extensions",
            "--headless=new",
            WEB_URL,
        ],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(30):
        if await _cdp_ready():
            return
        await asyncio.sleep(1)
    raise RuntimeError("Telegram Web Chrome CDP did not start")


class TelegramWebGigaClient:
    def __init__(self) -> None:
        self._playwright = None
        self.browser = None
        self.page = None

    async def connect(self) -> None:
        from playwright.async_api import async_playwright

        await _ensure_browser()
        self._playwright = await async_playwright().start()
        self.browser = await self._playwright.chromium.connect_over_cdp(CDP_URL)
        if not self.browser.contexts:
            raise RuntimeError("Telegram Web browser has no context")
        context = self.browser.contexts[0]
        self.page = context.pages[0] if context.pages else await context.new_page()
        await self.page.goto(
            WEB_URL,
            wait_until="domcontentloaded",
            timeout=60000,
        )
        await self.page.wait_for_timeout(1200)

        body = (await self.page.locator("body").inner_text()).lower()
        login_markers = (
            "log in to telegram",
            "log in by phone number",
            "point your phone at this screen",
        )
        if any(marker in body for marker in login_markers):
            raise RuntimeError(
                "Telegram Web profile is not authorized. "
                "Open the dedicated profile and scan the QR code."
            )

        chat_link = self.page.locator(
            f'a[href="#{GIGA_CHAT_ID}"]'
        ).first
        if await chat_link.count() == 0:
            raise RuntimeError(
                f"GigaRecruiter chat #{GIGA_CHAT_ID} not found in Telegram Web"
            )
        await chat_link.click()
        await self.page.wait_for_timeout(1200)
        await self.assert_authorized()

    async def disconnect(self) -> None:
        # Do not close the CDP browser: its persistent profile is the session.
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    async def assert_authorized(self) -> None:
        assert self.page is not None
        body = (await self.page.locator("body").inner_text()).lower()
        login_markers = (
            "log in to telegram",
            "log in by phone number",
            "point your phone at this screen",
        )
        if any(marker in body for marker in login_markers):
            raise RuntimeError(
                "Telegram Web profile is not authorized. "
                "Open the dedicated profile and scan the QR code."
            )
        composer = self.page.locator(
            '[contenteditable="true"][aria-label="Message"]'
        )
        if await composer.count() == 0:
            raise RuntimeError("Telegram Web message composer not found")

    async def _incoming_from_message(self, message) -> dict | None:
        message_id_raw = await message.get_attribute("data-message-id")
        if not message_id_raw:
            return None
        text_node = message.locator(".text-content").first
        text = ""
        if await text_node.count():
            text = _clean_message_text(await text_node.inner_text())
        if not text:
            text = _clean_message_text(await message.inner_text())

        option_nodes = message.locator(
            "button.Button.tiny.primary, button[class*='tiny'][class*='primary']"
        )
        options: list[str] = []
        for index in range(await option_nodes.count()):
            label = (await option_nodes.nth(index).inner_text()).strip()
            if label:
                options.append(label)

        return {
            "external_message_id": int(message_id_raw),
            "question": text or "(сообщение без текста)",
            "options": options,
        }

    async def latest_incoming(self) -> dict | None:
        assert self.page is not None
        messages = self.page.locator(".Message:not(.own)")
        count = await messages.count()
        if count == 0:
            return None
        return await self._incoming_from_message(messages.nth(count - 1))

    async def recent_incoming(self, limit: int = 20) -> list[dict]:
        assert self.page is not None
        messages = self.page.locator(".Message:not(.own)")
        count = await messages.count()
        start = max(0, count - max(1, limit))
        result: list[dict] = []
        for index in range(start, count):
            item = await self._incoming_from_message(messages.nth(index))
            if item is not None:
                result.append(item)
        return result

    async def _assert_turn_is_current(self, external_message_id: int) -> None:
        latest = await self.latest_incoming()
        if latest is None:
            raise RuntimeError("GigaRecruiter has no incoming message")
        if int(latest["external_message_id"]) != int(external_message_id):
            raise RuntimeError(
                "GigaRecruiter question changed before approval; "
                "stale answer was not sent"
            )

    async def send_text(
        self,
        *,
        external_message_id: int,
        answer: str,
    ) -> None:
        assert self.page is not None
        await self._assert_turn_is_current(external_message_id)
        composer = self.page.locator(
            '[contenteditable="true"][aria-label="Message"]'
        ).first
        await composer.fill(answer)
        send_button = self.page.locator(
            'button[aria-label="Send Message"], button[title="Send Message"]'
        )
        if await send_button.count():
            await send_button.first.click()
        else:
            await composer.press("Enter")

    async def click_option(
        self,
        *,
        external_message_id: int,
        index: int,
    ) -> None:
        assert self.page is not None
        message = self.page.locator(
            f'.Message[data-message-id="{int(external_message_id)}"]'
        ).first
        if await message.count() == 0:
            raise RuntimeError(
                "GigaRecruiter question is no longer present; stale choice was not sent"
            )
        buttons = message.locator(
            "button.Button.tiny.primary, button[class*='tiny'][class*='primary']"
        )
        count = await buttons.count()
        if not (0 <= index < count):
            raise RuntimeError(
                f"inline option index {index} is out of range ({count})"
            )
        await buttons.nth(index).click()


async def _handle_new_message(
    *,
    web: TelegramWebGigaClient,
    store: SberScreeningStore,
    bot_token: str,
    chat_id: int,
    last_unarmed_id: int | None,
) -> int | None:
    incoming = await web.latest_incoming()
    if incoming is None:
        return last_unarmed_id

    external_id = int(incoming["external_message_id"])
    question = str(incoming["question"])
    session = store.get_active_session()
    if session is None:
        vacancy_title = _screening_start_vacancy_title(question)
        if not vacancy_title:
            vacancy_title = await _recent_screening_start_vacancy_title(
                web,
                after_external_id=store.last_external_message_id(),
            )
        if vacancy_title:
            application_id = await asyncio.to_thread(
                resolve_application_for_vacancy_title,
                vacancy_title,
            )
            if application_id is not None:
                session = store.arm(
                    application_id,
                    vacancy_title=vacancy_title,
                )
                print(
                    "[SBER WEB] auto-started screening "
                    f"session #{session['id']} for application #{application_id} "
                    f"from Giga vacancy '{vacancy_title}'",
                    flush=True,
                )
            else:
                session = store.arm_external(vacancy_title)
                print(
                    "[SBER WEB] auto-started external screening "
                    f"session #{session['id']} from Giga vacancy "
                    f"'{vacancy_title}'",
                    flush=True,
                )

        if session is None:
            latest_session = store.get_latest_session()
            if latest_session and latest_session.get("status") == "completed":
                # A completed screening stays closed, but subsequent messages
                # must still reach the owner as read-only notifications.
                informational = store.create_turn(
                    session_id=int(latest_session["id"]),
                    external_message_id=external_id,
                    question=question,
                    options=list(incoming["options"]),
                )
                if informational["status"] == "pending":
                    store.mark_post_terminal(int(informational["id"]))
                    informational = store.get_turn(int(informational["id"]))
                if (
                    informational
                    and informational["status"] == "post_terminal"
                    and not informational.get("notified_at")
                ):
                    await _notify(
                        bot_token=bot_token,
                        chat_id=chat_id,
                        text=(
                            "ℹ️ Сбер / ГигаРекрутер\n"
                            f"{_session_subject(latest_session)}\n\n"
                            "Сообщение после завершения скрининга:\n"
                            f"{question[:3200]}\n\n"
                            "Скрининг завершён. Ответ не отправлен."
                        ),
                    )
                    store.mark_notified(int(informational["id"]))
                    print(
                        f"[SBER WEB] notified post-terminal message "
                        f"#{external_id} on session #{latest_session['id']}",
                        flush=True,
                    )
                return external_id
            if external_id != last_unarmed_id:
                await _notify(
                    bot_token=bot_token,
                    chat_id=chat_id,
                    text=(
                        "⚠️ Не удалось автоматически определить отклик для нового "
                        "скрининга ГигаРекрутера. Используй /sber_arm APPLICATION_ID "
                        "как резервный вариант."
                    ),
                )
            return external_id

    session_id = int(session["id"])
    existing = store.create_turn(
        session_id=session_id,
        external_message_id=external_id,
        question=question,
        options=list(incoming["options"]),
    )

    if _is_terminal_message(question):
        turn_id = int(existing["id"])
        store.mark_terminal(turn_id)
        store.complete_session(session_id)
        if not existing.get("notified_at"):
            store.mark_notified(turn_id)
            try:
                await _notify(
                    bot_token=bot_token,
                    chat_id=chat_id,
                    text=(
                        "✅ Сбер / ГигаРекрутер\n"
                        f"{_session_subject(session)}\n\n"
                        "Скрининг завершён. ГигаРекрутер сообщил, что "
                        "передаст резюме и итоги диалога рекрутеру."
                    ),
                )
            except Exception as exc:
                print(
                    "[SBER WEB] terminal notification failed: "
                    f"{type(exc).__name__}: {exc}",
                    flush=True,
                )
        print(
            f"[SBER WEB] completed session #{session_id} on turn #{turn_id}",
            flush=True,
        )
        return external_id

    if existing.get("status") != "pending" or existing.get("notified_at"):
        return last_unarmed_id

    store.set_session_active(session_id)
    options = list(incoming["options"])
    if options:
        await _notify(
            bot_token=bot_token,
            chat_id=chat_id,
            text=(
                "🟢 Сбер / ГигаРекрутер\n"
                f"{_session_subject(session)}\n\n"
                f"{str(incoming['question'])[:2500]}\n\n"
                "Выбери вариант. Ничего не уйдёт без твоего нажатия."
            ),
            keyboard=_choice_keyboard(int(existing["id"]), options),
        )
        store.mark_notified(int(existing["id"]))
        return last_unarmed_id

    history = [
        item
        for item in store.history(session_id)
        if int(item["id"]) != int(existing["id"])
    ]
    try:
        suggestion = await asyncio.to_thread(
            generate_suggestion,
            application_id=int(session["application_id"]),
            question=str(incoming["question"]),
            history=history,
            vacancy_title=str(session.get("vacancy_title") or "") or None,
        )
        answer = suggestion.answer
        confidence = suggestion.confidence
        reason_text = suggestion.reason
    except Exception as exc:
        answer = ""
        confidence = "needs_user"
        reason_text = f"{type(exc).__name__}: {exc}"

    store.update_suggestion(
        int(existing["id"]),
        suggested_answer=answer or None,
        confidence=confidence,
        reason=reason_text,
    )

    if answer:
        text = (
            "🟢 Сбер / ГигаРекрутер\n"
            f"{_session_subject(session)}\n\n"
            f"Вопрос:\n{str(incoming['question'])[:1800]}\n\n"
            f"Предлагаю ответ:\n{answer[:1500]}\n\n"
            f"Уверенность: {confidence}\n"
            f"Основание: {reason_text[:500]}\n\n"
            "Для другого текста нажми «✏️ Свой ответ»."
        )
    else:
        text = (
            "🟡 Сбер / ГигаРекрутер\n"
            f"{_session_subject(session)}\n\n"
            f"Вопрос:\n{str(incoming['question'])[:2200]}\n\n"
            "Не могу честно ответить только из подтвержденных данных.\n"
            f"Нужно уточнить: {reason_text[:700]}\n\n"
            "Нажми «✏️ Свой ответ» и напиши его обычным сообщением."
        )

    await _notify(
        bot_token=bot_token,
        chat_id=chat_id,
        text=text,
        keyboard=_pending_keyboard(int(existing["id"]), bool(answer)),
    )
    store.mark_notified(int(existing["id"]))
    return last_unarmed_id


async def _approval_pass(
    web: TelegramWebGigaClient,
    store: SberScreeningStore,
) -> None:
    for turn in store.approved_turns():
        turn_id = int(turn["id"])
        external_id = int(turn["external_message_id"])
        payload = str(turn.get("approved_payload") or "")
        try:
            if payload.startswith("text:"):
                answer = payload[5:].strip()
                if not answer:
                    raise RuntimeError("approved text is empty")
                await web.send_text(
                    external_message_id=external_id,
                    answer=answer,
                )
            elif payload.startswith("button:"):
                index = int(payload.split(":", 1)[1])
                await web.click_option(
                    external_message_id=external_id,
                    index=index,
                )
            else:
                raise RuntimeError("unknown approved payload")
            store.mark_sent(turn_id)
            print(f"[SBER WEB] sent turn #{turn_id}", flush=True)
        except Exception as exc:
            store.mark_error(turn_id, f"{type(exc).__name__}: {exc}")
            print(
                f"[SBER WEB] send failed turn #{turn_id}: "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )


async def run() -> None:
    if not _enabled():
        print(
            "[SBER WEB] disabled. Set HH_SBER_SCREENING_ENABLED=true.",
            flush=True,
        )
        return

    bot_token, chat_id = _bot_credentials()
    store = SberScreeningStore(DEFAULT_STORE_PATH)
    web = TelegramWebGigaClient()
    await web.connect()
    print(
        f"[SBER WEB] listening chat #{GIGA_CHAT_ID}; "
        "copilot mode, auto-send disabled",
        flush=True,
    )

    last_unarmed_id: int | None = None
    try:
        while True:
            try:
                last_unarmed_id = await _handle_new_message(
                    web=web,
                    store=store,
                    bot_token=bot_token,
                    chat_id=chat_id,
                    last_unarmed_id=last_unarmed_id,
                )
                await _approval_pass(web, store)
            except Exception as exc:
                print(
                    f"[SBER WEB] loop error: {type(exc).__name__}: {exc}",
                    flush=True,
                )
            await asyncio.sleep(POLL_SECONDS)
    finally:
        await web.disconnect()


def main() -> None:
    try:
        with AgentLock(path=SBER_WORKER_LOCK_PATH):
            asyncio.run(run())
    except RuntimeError as exc:
        if str(exc) == "agent_lock_busy":
            print(
                "[SBER WEB] another worker is already running; exiting duplicate",
                flush=True,
            )
            return
        raise


if __name__ == "__main__":
    main()
