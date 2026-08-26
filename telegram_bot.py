import json
import os
import subprocess
import msvcrt
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
import httpx
from sqlalchemy import func, select
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from background_common import (
    APPLY_STATE,
    PIPELINE_STATE,
    RESUME_RAISE_STATE,
    ROOT,
    TELEGRAM_STATE,
    now_iso,
    read_state,
    write_state,
)
from app.db import (
    Application,
    Evaluation,
    SessionLocal,
    Vacancy,
)


load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID_RAW = os.getenv("TELEGRAM_CHAT_ID")
MIN_SCORE_TO_NOTIFY = int(os.getenv("TELEGRAM_MIN_SCORE", "72"))

if not BOT_TOKEN:
    raise RuntimeError("В .env отсутствует TELEGRAM_BOT_TOKEN")

# Public mode: TELEGRAM_CHAT_ID is optional.
# It is used only as a fallback destination for background notifications.
CHAT_ID = int(CHAT_ID_RAW) if CHAT_ID_RAW else None

TELEGRAM_LOCK_FILE = ROOT / "data" / "runtime" / "telegram_bot.lock"


class TelegramSingleInstanceLock:
    """Windows process lock for telegram_bot.py."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = open(self.path, "a+b")
        self.handle.seek(0, os.SEEK_END)
        if self.handle.tell() == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)

        try:
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            self.handle.close()
            self.handle = None
            return False

    def release(self) -> None:
        if self.handle is None:
            return

        try:
            self.handle.seek(0)
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass

        try:
            self.handle.close()
        except OSError:
            pass

        self.handle = None


def parse_json_list(value: str) -> list[str]:
    try:
        result = json.loads(value)
        if isinstance(result, list):
            return result
    except Exception:
        pass
    return []


def format_salary(vacancy: Vacancy) -> str:
    if vacancy.salary_from is None and vacancy.salary_to is None:
        return "не указана"

    parts = []
    if vacancy.salary_from is not None:
        value = f"{vacancy.salary_from:,}".replace(",", " ")
        parts.append(f"от {value}")
    if vacancy.salary_to is not None:
        value = f"{vacancy.salary_to:,}".replace(",", " ")
        parts.append(f"до {value}")
    if vacancy.salary_currency:
        parts.append(vacancy.salary_currency)
    return " ".join(parts)


def shorten(text: str, limit: int = 700) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def list_to_text(items: list[str], limit: int = 3) -> str:
    if not items:
        return "—"
    return "\n".join(f"• {item}" for item in items[:limit])


def application_exists(session, vacancy_id: int) -> bool:
    stmt = select(Application.id).where(Application.vacancy_id == vacancy_id)
    return session.execute(stmt).first() is not None


def create_notification_state(
    session,
    vacancy: Vacancy,
    evaluation: Evaluation,
) -> None:
    application = Application(
        vacancy_id=vacancy.id,
        status="notified",
        cover_letter=evaluation.cover_letter or None,
        selected_resume_key=evaluation.selected_resume_key,
        selected_resume_title=evaluation.selected_resume_title,
        selected_resume_id=evaluation.selected_resume_id,
        selected_resume_score=evaluation.selected_resume_score,
    )
    session.add(application)
    session.commit()


def get_application_state(session, vacancy_id: int) -> Application | None:
    stmt = (
        select(Application)
        .where(Application.vacancy_id == vacancy_id)
        .order_by(Application.id.desc())
    )
    return session.scalars(stmt).first()


def build_message(vacancy: Vacancy, evaluation: Evaluation) -> str:
    strengths = parse_json_list(evaluation.strengths)
    gaps = parse_json_list(evaluation.gaps)
    must_have = parse_json_list(evaluation.must_have_missing)
    red_flags = parse_json_list(evaluation.red_flags)

    if evaluation.score >= 90:
        icon = "🔥"
        rating = "ОЧЕНЬ СИЛЬНЫЙ MATCH"
    elif evaluation.score >= 82:
        icon = "✅"
        rating = "СИЛЬНЫЙ MATCH"
    else:
        icon = "👀"
        rating = "REVIEW"

    parts = [
        f"{icon} {evaluation.score}/100 — {rating}",
        "",
        vacancy.title,
        vacancy.company or "Компания не указана",
        "",
        f"💰 Зарплата: {format_salary(vacancy)}",
        "",
        (
            f"MATCH: role {evaluation.role_match} | "
            f"seniority {evaluation.seniority_match} | "
            f"domain {evaluation.domain_match} | "
            f"responsibility {evaluation.responsibility_match}"
        ),
        "",
        "✅ Почему подходит:",
        list_to_text(strengths),
    ]

    if must_have:
        parts.extend(["", "⚠️ Must-have gaps:", list_to_text(must_have)])
    if gaps:
        parts.extend(["", "🟡 Пробелы:", list_to_text(gaps)])
    if red_flags:
        parts.extend(["", "🚨 Red flags:", list_to_text(red_flags)])

    if evaluation.selected_resume_id:
        parts.extend(
            [
                "",
                (
                    "📄 Резюме для отклика: "
                    f"{evaluation.selected_resume_title or evaluation.selected_resume_key}"
                ),
                f"🎯 Match резюме: {evaluation.selected_resume_score or 0}%",
            ]
        )

    parts.extend(
        [
            "",
            "💡 Рекомендация:",
            shorten(evaluation.recommendation, 500),
            "",
            "✉️ Сопроводительное:",
            shorten(evaluation.cover_letter, 900),
            "",
            vacancy.url,
        ]
    )
    return "\n".join(parts)


def _vacancy_open_target(vacancy: Vacancy | None) -> tuple[str, str]:
    source = (vacancy.source or "hh").strip().lower() if vacancy is not None else "hh"
    url = (vacancy.url or "").strip() if vacancy is not None else ""

    if not url and source == "hh" and vacancy is not None and vacancy.hh_id:
        url = f"https://hh.ru/vacancy/{vacancy.hh_id}"
    if not url:
        url = "https://hh.ru"

    labels = {
        "hh": "🔗 Открыть HH",
        "yandex": "🔗 Открыть Yandex",
        "vk": "🔗 Открыть VK",
    }
    return url, labels.get(source, f"🔗 Открыть {source.upper()}")


def build_keyboard(vacancy_id: int) -> InlineKeyboardMarkup:
    session = SessionLocal()
    try:
        vacancy = session.get(Vacancy, vacancy_id)
        url, open_label = _vacancy_open_target(vacancy)
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ Откликнуться",
                        callback_data=f"approve:{vacancy_id}",
                    ),
                    InlineKeyboardButton(
                        "❌ Пропустить",
                        callback_data=f"skip:{vacancy_id}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "🚫 Компания в blacklist",
                        callback_data=f"blacklist_company:{vacancy_id}",
                    )
                ],
                [InlineKeyboardButton(open_label, url=url)],
            ]
        )
    finally:
        session.close()


def build_manual_required_message(vacancy: Vacancy, state: Application) -> str:
    return "\n".join(
        [
            "⚠️ Требуется ручное действие",
            "",
            vacancy.title,
            vacancy.company or "Компания не указана",
            "",
            (
                "Автоматический отклик не был завершён или HH не подтвердил "
                "успешную отправку."
            ),
            f"Application ID: {state.id}",
            "",
            "Открой вакансию и заверши отклик вручную.",
        ]
    )


def build_manual_required_keyboard(
    vacancy: Vacancy,
) -> InlineKeyboardMarkup:
    url, _ = _vacancy_open_target(vacancy)
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🖐 Откликнуться вручную",
                    url=url,
                )
            ],
            [
                InlineKeyboardButton(
                    "✅ Уже откликнулся",
                    callback_data=f"manual_done:{vacancy.id}",
                )
            ],
        ]
    )


def get_hh_id(vacancy_id: int) -> str:
    session = SessionLocal()
    try:
        vacancy = session.get(Vacancy, vacancy_id)
        return vacancy.hh_id if vacancy is not None else ""
    finally:
        session.close()


def resolve_target_chat_id(context: ContextTypes.DEFAULT_TYPE) -> int | None:
    if context._chat_id is not None:
        return int(context._chat_id)
    return CHAT_ID


async def _send_manual_required_cards(
    context: ContextTypes.DEFAULT_TYPE,
    session,
    target_chat_id: int,
) -> tuple[int, set[int]]:
    """Show every vacancy whose latest application state still needs manual action."""
    candidates = session.scalars(
        select(Vacancy)
        .join(Application, Application.vacancy_id == Vacancy.id)
        .where(Application.status == "manual_required")
        .order_by(Application.id.desc())
    ).all()

    sent = 0
    shown_vacancy_ids: set[int] = set()

    for vacancy in candidates:
        if vacancy.id in shown_vacancy_ids:
            continue

        state = get_application_state(session, vacancy.id)
        if state is None or state.status != "manual_required":
            continue

        await context.bot.send_message(
            chat_id=target_chat_id,
            text=build_manual_required_message(vacancy, state),
            reply_markup=build_manual_required_keyboard(vacancy),
            disable_web_page_preview=True,
        )
        shown_vacancy_ids.add(vacancy.id)
        sent += 1

    return sent, shown_vacancy_ids


async def send_new_vacancies(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int | None = None,
) -> None:
    target_chat_id = chat_id if chat_id is not None else CHAT_ID
    if target_chat_id is None:
        raise RuntimeError("Не удалось определить Telegram chat_id для отправки вакансий.")

    session = SessionLocal()
    try:
        sent_manual, manual_vacancy_ids = await _send_manual_required_cards(
            context,
            session,
            target_chat_id,
        )

        rows = session.execute(
            select(Vacancy, Evaluation)
            .join(Evaluation, Evaluation.vacancy_id == Vacancy.id)
            .where(Evaluation.score >= MIN_SCORE_TO_NOTIFY)
            .where(~Evaluation.model.startswith("hard-filter/"))
            .order_by(
                Evaluation.score.desc(),
                Evaluation.responsibility_match.desc(),
            )
        ).all()

        sent_new = 0
        sent_pending = 0
        seen_vacancy_ids: set[int] = set(manual_vacancy_ids)

        for vacancy, evaluation in rows:
            if vacancy.id in seen_vacancy_ids:
                continue
            seen_vacancy_ids.add(vacancy.id)

            state = get_application_state(session, vacancy.id)

            # /new repeats cards only while no decision exists. Manual-required
            # cards are handled above as a separate recovery queue.
            if state is not None and state.status != "notified":
                continue

            await context.bot.send_message(
                chat_id=target_chat_id,
                text=build_message(vacancy=vacancy, evaluation=evaluation),
                reply_markup=build_keyboard(vacancy.id),
                disable_web_page_preview=True,
            )

            if state is None:
                create_notification_state(
                    session=session,
                    vacancy=vacancy,
                    evaluation=evaluation,
                )
                sent_new += 1
            else:
                sent_pending += 1

        total_sent = sent_new + sent_pending + sent_manual

        if total_sent == 0:
            await context.bot.send_message(
                chat_id=target_chat_id,
                text=(
                    "Нет новых вакансий, карточек без решения и откликов, "
                    "требующих ручного действия."
                ),
            )
        else:
            await context.bot.send_message(
                chat_id=target_chat_id,
                text=(
                    f"Новых вакансий: {sent_new}\n"
                    f"Без решения, показаны повторно: {sent_pending}\n"
                    f"Требуют ручного действия: {sent_manual}"
                ),
            )
    finally:
        session.close()


def _format_uptime(started_at: str | None) -> str:
    if not started_at:
        return "неизвестно"
    try:
        started = datetime.fromisoformat(started_at)
        now = datetime.now().astimezone()
        if started.tzinfo is None:
            started = started.astimezone()
        seconds = max(0, int((now - started).total_seconds()))
        days, seconds = divmod(seconds, 86400)
        hours, seconds = divmod(seconds, 3600)
        minutes, _ = divmod(seconds, 60)

        parts = []
        if days:
            parts.append(f"{days}д")
        if hours or days:
            parts.append(f"{hours}ч")
        parts.append(f"{minutes}м")
        return " ".join(parts)
    except Exception:
        return "неизвестно"


def _touch_telegram_state() -> dict:
    state = read_state(TELEGRAM_STATE)
    started_at = state.get("started_at") or now_iso()
    write_state(
        TELEGRAM_STATE,
        status="running",
        stage="polling",
        started_at=started_at,
        pid=os.getpid(),
        last_error=None,
    )
    return read_state(TELEGRAM_STATE)


def _fmt_state(name: str, state: dict) -> list[str]:
    if not state:
        return [f"{name}: данных пока нет"]

    status = state.get("status") or "unknown"
    stage = state.get("stage") or "—"
    lines = [f"{name}: {status}", f"  этап: {stage}"]

    if state.get("started_at"):
        lines.append(f"  старт: {state['started_at']}")
    if state.get("finished_at"):
        lines.append(f"  финиш: {state['finished_at']}")
    if state.get("updated_at"):
        lines.append(f"  heartbeat: {state['updated_at']}")
    if state.get("last_error"):
        lines.append("  ошибка: " + shorten(str(state["last_error"]), 350))
    return lines


def _ollama_health() -> tuple[bool, str]:
    try:
        response = httpx.get("http://127.0.0.1:11434/api/tags", timeout=2.5)
        response.raise_for_status()
        return True, "Ollama отвечает"
    except Exception as exc:
        return False, f"Ollama недоступен: {type(exc).__name__}"


def _project_health() -> tuple[bool, list[str]]:
    checks: list[str] = []
    ok = True
    required = [
        ROOT / "hh_collect.py",
        ROOT / "process_vacancies.py",
        ROOT / "apply_worker.py",
        ROOT / "browser-profile",
        ROOT / "data" / "hh_agent.db",
    ]

    for path in required:
        exists = path.exists()
        checks.append(("✅ " if exists else "❌ ") + str(path.relative_to(ROOT)))
        ok = ok and exists

    ollama_ok, ollama_text = _ollama_health()
    checks.append(("✅ " if ollama_ok else "❌ ") + ollama_text)
    ok = ok and ollama_ok
    return ok, checks


def _queue_stats() -> list[str]:
    session = SessionLocal()
    try:
        vacancies_total = session.scalar(select(func.count(Vacancy.id))) or 0
        unprocessed = (
            session.scalar(
                select(func.count(Vacancy.id)).where(Vacancy.processed.is_(False))
            )
            or 0
        )
        approved = (
            session.scalar(
                select(func.count(Application.id)).where(
                    Application.status == "approved"
                )
            )
            or 0
        )
        applying = (
            session.scalar(
                select(func.count(Application.id)).where(
                    Application.status == "applying"
                )
            )
            or 0
        )
        manual_required = (
            session.scalar(
                select(func.count(Application.id)).where(
                    Application.status == "manual_required"
                )
            )
            or 0
        )
        return [
            f"вакансий в БД: {vacancies_total}",
            f"не обработано: {unprocessed}",
            f"approved: {approved}",
            f"applying: {applying}",
            f"manual_required: {manual_required}",
        ]
    finally:
        session.close()


def _start_pipeline_from_telegram(chat_id: int) -> int:
    python_exe = ROOT / ".venv" / "Scripts" / "python.exe"
    script = ROOT / "background_pipeline.py"
    env = os.environ.copy()
    env["HH_TRIGGERED_BY_TELEGRAM"] = "true"
    env["TELEGRAM_CHAT_ID"] = str(chat_id)
    env["PYTHONUNBUFFERED"] = "1"
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

    process = subprocess.Popen(
        [str(python_exe), str(script)],
        cwd=str(ROOT),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
        close_fds=True,
    )
    return int(process.pid)


async def health_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    telegram_state = _touch_telegram_state()
    ok, checks = _project_health()
    pipeline_state = read_state(PIPELINE_STATE)
    apply_state = read_state(APPLY_STATE)
    resume_raise_state = read_state(RESUME_RAISE_STATE)

    lines = [
        "✅ HH Agent healthy" if ok else "⚠️ HH Agent: есть проблемы",
        "",
        "🤖 Telegram bot: ONLINE",
        f"  PID: {telegram_state.get('pid') or os.getpid()}",
        f"  uptime: {_format_uptime(telegram_state.get('started_at'))}",
        "",
        *checks,
        "",
        "Runtime:",
        *_fmt_state("pipeline", pipeline_state),
        *_fmt_state("apply", apply_state),
        *_fmt_state("resume raise", resume_raise_state),
        "",
        "Очередь:",
        *["• " + item for item in _queue_stats()],
    ]
    await update.message.reply_text("\n".join(lines))


async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    _touch_telegram_state()
    pipeline_state = read_state(PIPELINE_STATE)
    apply_state = read_state(APPLY_STATE)
    resume_raise_state = read_state(RESUME_RAISE_STATE)

    lines = [
        "📊 HH Agent — runtime status",
        "",
        *_fmt_state("PIPELINE", pipeline_state),
        "",
        *_fmt_state("APPLY", apply_state),
        "",
        *_fmt_state("RESUME RAISE", resume_raise_state),
        "",
        "Очередь:",
        *["• " + item for item in _queue_stats()],
    ]
    await update.message.reply_text("\n".join(lines))


async def run_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    _touch_telegram_state()
    current = read_state(PIPELINE_STATE)

    if current.get("status") in {"starting", "running"}:
        await update.message.reply_text(
            "⏳ Pipeline уже выполняется.\n\n"
            f"Этап: {current.get('stage') or '—'}\n"
            "Используй /status."
        )
        return

    try:
        if update.effective_chat is None:
            raise RuntimeError("Не удалось определить текущий Telegram chat_id.")

        pid = _start_pipeline_from_telegram(update.effective_chat.id)
        write_state(
            PIPELINE_STATE,
            status="starting",
            stage="telegram_trigger",
            started_at=now_iso(),
            pid=pid,
            triggered_by="telegram",
            last_error=None,
        )
        await update.message.reply_text(
            "▶️ Pipeline запущен.\n"
            f"PID: {pid}\n\n"
            "Я пришлю сообщения о переходе между этапами и о результате.\n"
            "Текущий статус: /status"
        )
    except Exception as exc:
        await update.message.reply_text(
            "❌ Не удалось запустить pipeline:\n"
            f"{type(exc).__name__}: {exc}"
        )


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await update.message.reply_text(
        "HH Agent Xander запущен.\n"
        "Режим доступа: публичный.\n\n"
        "/health — healthcheck агента\n"
        "/status — текущий процесс и очереди\n"
        "/run — запустить pipeline сейчас\n"
        "/new — новые + без решения + ручные отклики\n"
        "/stats — статистика решений"
    )


async def new_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await update.message.reply_text("Проверяю базу...")
    await send_new_vacancies(
        context,
        chat_id=(
            update.effective_chat.id if update.effective_chat is not None else None
        ),
    )


async def stats_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    session = SessionLocal()
    try:
        applications = session.scalars(select(Application)).all()
        statuses: dict[str, int] = {}
        for item in applications:
            statuses[item.status] = statuses.get(item.status, 0) + 1

        lines = ["HH Agent — статистика:", ""]
        if not statuses:
            lines.append("Решений пока нет.")
        else:
            for status, count in sorted(statuses.items()):
                lines.append(f"{status}: {count}")
        await update.message.reply_text("\n".join(lines))
    finally:
        session.close()


async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    if query is None:
        return
    if query.message is None:
        await query.answer()
        return

    await query.answer()
    data = query.data or ""
    try:
        action, vacancy_id_raw = data.split(":", 1)
        vacancy_id = int(vacancy_id_raw)
    except Exception:
        await query.edit_message_reply_markup(reply_markup=None)
        return

    session = SessionLocal()
    try:
        vacancy = session.get(Vacancy, vacancy_id)
        if vacancy is None:
            await query.answer("Вакансия не найдена.", show_alert=True)
            return

        state = get_application_state(session, vacancy_id)
        if state is None:
            latest_evaluation = session.scalars(
                select(Evaluation)
                .where(Evaluation.vacancy_id == vacancy_id)
                .order_by(Evaluation.id.desc())
            ).first()
            state = Application(
                vacancy_id=vacancy_id,
                status="notified",
                cover_letter=(
                    latest_evaluation.cover_letter
                    if latest_evaluation is not None
                    else None
                ),
                selected_resume_key=(
                    latest_evaluation.selected_resume_key
                    if latest_evaluation is not None
                    else None
                ),
                selected_resume_title=(
                    latest_evaluation.selected_resume_title
                    if latest_evaluation is not None
                    else None
                ),
                selected_resume_id=(
                    latest_evaluation.selected_resume_id
                    if latest_evaluation is not None
                    else None
                ),
                selected_resume_score=(
                    latest_evaluation.selected_resume_score
                    if latest_evaluation is not None
                    else None
                ),
            )
            session.add(state)

        if action == "approve":
            state.status = "approved"
            resume_text = (
                state.selected_resume_title
                or state.selected_resume_key
                or "не выбрано"
            )
            response_text = (
                "✅ Отмечено: откликнуться.\n\n"
                f"📄 Резюме: {resume_text}"
            )
        elif action == "skip":
            state.status = "skipped"
            response_text = "❌ Вакансия пропущена."
        elif action == "blacklist_company":
            state.status = "company_blacklist"
            response_text = (
                "🚫 Компания отмечена для blacklist.\n\n"
                f"{vacancy.company or 'Компания не указана'}"
            )
        elif action == "manual_done":
            state.status = "applied"
            state.applied_at = datetime.utcnow()
            response_text = "✅ Отмечено: ручной отклик завершён."
        else:
            return

        session.commit()
        original = query.message.text or ""
        await query.edit_message_text(
            text=original + "\n\n" + response_text,
            disable_web_page_preview=True,
        )
    finally:
        session.close()


def main() -> None:
    lock = TelegramSingleInstanceLock(TELEGRAM_LOCK_FILE)
    if not lock.acquire():
        print(
            "Telegram bot уже запущен. Второй экземпляр завершён.",
            flush=True,
        )
        return

    try:
        app = ApplicationBuilder().token(BOT_TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("new", new_command))
        app.add_handler(CommandHandler("stats", stats_command))
        app.add_handler(CommandHandler("health", health_command))
        app.add_handler(CommandHandler("status", status_command))
        app.add_handler(CommandHandler("run", run_command))
        app.add_handler(CallbackQueryHandler(button_handler))

        print("HH Telegram Bot запущен.")
        print("Access mode: public (commands accepted from any Telegram chat).")
        print(f"Минимальный score: {MIN_SCORE_TO_NOTIFY}")
        print("Ctrl+C для остановки.")

        write_state(
            TELEGRAM_STATE,
            status="running",
            stage="polling",
            started_at=now_iso(),
            pid=os.getpid(),
            last_error=None,
        )
        print("Telegram runtime state initialized.", flush=True)
        app.run_polling()
    finally:
        try:
            current_state = read_state(TELEGRAM_STATE)
            if current_state.get("pid") == os.getpid():
                write_state(
                    TELEGRAM_STATE,
                    status="stopped",
                    stage="stopped",
                    finished_at=now_iso(),
                    pid=os.getpid(),
                )
        except Exception:
            pass
        lock.release()


if __name__ == "__main__":
    main()
