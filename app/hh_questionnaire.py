"""HH response questionnaires: read-only DOM capture, grounded drafts, approval store.

Only the apply worker may submit. Telegram approval merely releases the existing
Application back to its account-bound worker; it does not send a response.
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
import html
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STORE = ROOT / "data" / "secrets" / "hh_questionnaires.sqlite3"
ANSWER_LIMIT = 520
TELEGRAM_LIMIT = 4096

_CAPTURE_JS = r"""() => {
  const form = document.querySelector('form[name="vacancy_response"]');
  if (!form) return null;
  const inputs = [...form.querySelectorAll('input[name^="task_"], textarea[name^="task_"], select[name^="task_"]')];
  const groups = new Map();
  for (const input of inputs) {
    const match = /^task_(\d+)(?:_text)?$/.exec(input.name || '');
    if (!match) continue;
    if (!groups.has(match[1])) groups.set(match[1], []);
    groups.get(match[1]).push(input);
  }
  const result = [];
  for (const [id, group] of groups) {
    const primary = group.filter(e => e.type === 'checkbox' || e.type === 'radio');
    const plain = group.find(e => ['textarea', 'text'].includes(e.type) && !e.closest('.task-option-text_hidden'));
    const options = primary.map(e => ({
      text: (e.labels?.[0]?.innerText || e.parentElement?.innerText || '').trim(),
      value: e.value || ''
    })).filter(o => o.text);
    const root = plain || primary[0] || group[0];
    let question = '';
    for (let n = root.parentElement; n && n !== form; n = n.parentElement) {
      const fieldNames = [...n.querySelectorAll('[name^="task_"]')]
        .map(e => /^task_(\d+)/.exec(e.name || '')?.[1]).filter(Boolean);
      if (fieldNames.some(other => other !== id)) break;
      const lines = (n.innerText || '').split(/\n+/).map(s => s.trim()).filter(Boolean);
      let end = lines.findIndex(line => options.some(o => o.text === line));
      if (end < 0) end = lines.length;
      const candidate = lines.slice(0, end).filter(
        line => !['Писать тут', 'Свой вариант', 'Добавить'].includes(line)
      ).join(' ').trim();
      if (candidate.length >= 12 && candidate.length <= 1800) {
        question = candidate;
        break;
      }
    }
    if (!question) return {unsupported: 'question text not found for task_' + id};
    let kind = '';
    if (primary.length >= 2 && primary.length <= 8 && options.length === primary.length) kind = 'choice';
    else if (!primary.length && plain && plain.tagName === 'TEXTAREA') kind = 'text';
    else return {unsupported: 'unsupported input controls for task_' + id};
    result.push({id, name: kind === 'text' ? plain.name : primary[0].name,
      kind, question, options: kind === 'choice' ? options : []});
  }
  if (!result.length) return null;
  return {
    questions: result,
    visibility_block: /чтобы откликнуться[\s\S]{0,170}поменяйте видимость резюме/i.test(form.innerText || ''),
    upload_block: !!form.querySelector('input[type="file"]')
  };
}"""


def extract(page) -> dict | None:
    """No clicking or mutation: capture the actual rendered HH full-page form."""
    try:
        result = page.evaluate(_CAPTURE_JS)
    except Exception:
        return None
    if not result or result.get("unsupported"):
        return result
    if len(result.get("questions") or []) > 20:
        return {"unsupported": "too many questions"}
    return result


def fingerprint(questions: list[dict]) -> str:
    payload = [
        {"id": q["id"], "name": q["name"], "question": q["question"],
         "kind": q["kind"], "options": q.get("options") or []}
        for q in questions
    ]
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def salary_answer(question: str) -> str | None:
    if not re.search(r"зарплат|доход|оклад|вознагражден|сумм.{0,25}рассматри|salary", question, re.I):
        return None
    if re.search(r"комфорт|от минимума до", question, re.I):
        return "Минимально рассматриваю от 350 000 ₽ на руки (net). Комфортный уровень зависит от задач и условий."
    return "От 350 000 ₽ на руки (net)."


def _compact(text: str) -> str:
    value = re.sub(r"\s+", " ", text or "").strip()
    value = re.sub(r"^(?:[-•*]\s*|\d+[.)]\s*)", "", value)
    if len(value) <= ANSWER_LIMIT:
        return value
    end = max(value.rfind(". ", 0, ANSWER_LIMIT), value.rfind("! ", 0, ANSWER_LIMIT))
    return value[:end + 1] if end >= 80 else ""


_NUMERIC_TOKEN = re.compile(r"(?<![\w])\d+(?:[.,]\d+)?(?![\w])")
_QUANTITY_CLAIM = re.compile(
    r"(?<![\w])(?P<number>\d+(?:[.,]\d+)?)\s*"
    r"(?P<unit>микросервис\w*|сервис\w*|команд\w*|проект\w*|"
    r"разработчик\w*|пользовател\w*|камер\w*|систем\w*|"
    r"дн\w*|месяц\w*|лет|год\w*|%)",
    re.I,
)
_QUANTITY_ROOTS = (
    ("микросервис", "сервис"),
    ("сервис", "сервис"),
    ("команд", "команд"),
    ("проект", "проект"),
    ("разработчик", "разработчик"),
    ("пользовател", "пользовател"),
    ("камер", "камер"),
    ("систем", "систем"),
    ("дн", "дн"),
    ("месяц", "месяц"),
    ("лет", "лет"),
    ("год", "год"),
    ("%", "%"),
)


def ungrounded_numeric_claims(answer: str, resume_evidence: str) -> list[str]:
    """Reject a number lifted from one result and attached to another noun.

    A raw number match is not enough: '~100 -> 24 days' does not support
    '100 microservices'. Only candidate-owned evidence is considered,
    never the employer's vacancy requirements.
    """
    normalized_answer = (answer or "").replace(",", ".").casefold()
    normalized_evidence = (resume_evidence or "").replace(",", ".").casefold()
    evidence_numbers = set(_NUMERIC_TOKEN.findall(normalized_evidence))
    failures = []
    for numeric in _NUMERIC_TOKEN.findall(normalized_answer):
        if numeric not in evidence_numbers:
            failures.append(f"number:{numeric}")
    for match in _QUANTITY_CLAIM.finditer(normalized_answer):
        number, unit = match.group("number"), match.group("unit")
        root = next((root for prefix, root in _QUANTITY_ROOTS if unit.startswith(prefix)), None)
        if root is None:
            continue
        if root == "%":
            adjacent = re.search(r"(?<![\w])" + re.escape(number) + r"\s*%", normalized_evidence)
        else:
            adjacent = re.search(
                r"(?<![\w])" + re.escape(number) + r"\b[^.!?\n]{0,45}"
                + re.escape(root),
                normalized_evidence,
            )
        if not adjacent:
            failures.append(f"quantity:{number} {unit}")
    return list(dict.fromkeys(failures))


def generate_answers(application_id: int, questions: list[dict]) -> list[str]:
    """LLM answers are proposals only. Every answer requires owner confirmation."""
    from app.sber_screening import load_context
    from app.llm import LLMProvider

    context = load_context(application_id)
    provider = None
    answers = []
    for q in questions:
        salary = salary_answer(q["question"])
        if salary:
            answers.append(salary if q["kind"] == "text" else "")
            continue
        if q["kind"] == "choice":
            # Working arrangements and personal agreements must not be guessed.
            answers.append("")
            continue
        if provider is None:
            provider = LLMProvider()
        prompt = f"""Ты готовишь ответ кандидата от первого лица на вопрос анкеты HH.
Строго 1–3 коротких предложения без списков, заголовков, клише, Markdown и вводных.
Если подтверждён опыт: что делал лично, пример проекта и бизнес-эффект — ТОЛЬКО
когда сам пример и цифры подтверждены контекстом. Не выдумывай компании,
инструменты, сроки, проценты, SLA, результаты или причины смены работы.
Если конкретные цифры не указаны, опиши доказанный эффект качественно.
Не приписывай hands-on разработку кандидату: отличай управление от написания кода.
Числа не переносить из одного достижения в другое: «~100 → ~24 дня» — про Time-to-Market, НЕ про 100 сервисов. Стек проекта не означает, что кандидат лично внедрял Kafka, RabbitMQ или PostgreSQL. Пиши «управлял проектом, где использовались», если личная реализация не подтверждена.
Если данных недостаточно для честного ответа, mode=needs_user и пустой answer.
Не отвечай за кандидата на вопросы о неизвестных личных предпочтениях.
Вакансия: {context.vacancy_title}; компания: {context.company}
Выбранное резюме: {context.selected_resume_title}
Проверенные факты: {json.dumps(context.verified_facts, ensure_ascii=False)[:8000]}
Подтвержденные сильные стороны: {json.dumps(context.evaluation_strengths, ensure_ascii=False)[:6000]}
Резюме: {context.resume_text[:18000]}
Описание вакансии: {context.vacancy_description[:9000]}
ВОПРОС: {q['question']}
Верни JSON: {{"mode":"answer" или "needs_user", "answer":"готовый ответ или пустая строка"}}"""
        schema = {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["answer", "needs_user"]},
                "answer": {"type": "string"},
            },
            "required": ["mode", "answer"],
        }
        try:
            response = provider.chat(messages=[{"role": "user", "content": prompt}], format_schema=schema)
            raw = getattr(getattr(response, "message", None), "content", None)
            if raw is None and isinstance(response, dict):
                raw = (response.get("message") or {}).get("content")
            parsed = json.loads(str(raw or "{}"))
            answer = _compact(str(parsed.get("answer") or ""))
            if parsed.get("mode") != "answer":
                answer = ""
            if answer:
                evidence = "\n".join([context.resume_text, *context.verified_facts])
                problems = ungrounded_numeric_claims(answer, evidence)
                if problems:
                    print(
                        f"[HH QUESTIONNAIRE] Unsupported numeric claims: "
                        f"app={application_id} task={q['id']} {problems}. "
                        "Refusing draft; needs user correction.",
                        flush=True,
                    )
                    answer = ""
            answers.append(answer)
        except Exception as exc:
            print(f"[HH QUESTIONNAIRE] draft answer failed: app={application_id} task={q['id']} {type(exc).__name__}", flush=True)
            answers.append("")
    return answers


class QuestionnaireStore:
    def __init__(self, path: Path | str = DEFAULT_STORE):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS questionnaire (
                application_id INTEGER PRIMARY KEY,
                account_key TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                questions_json TEXT NOT NULL,
                answers_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                error TEXT
            )""")

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def get(self, application_id: int) -> dict | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM questionnaire WHERE application_id=?", (application_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["questions"] = json.loads(result.pop("questions_json"))
        result["answers"] = json.loads(result.pop("answers_json"))
        return result

    def save_draft(self, application_id: int, account_key: str,
                   questions: list[dict], answers: list[str]) -> bool:
        if account_key not in {"old", "clean"} or not questions or len(answers) != len(questions):
            raise ValueError("Invalid questionnaire draft")
        old = self.get(application_id)
        hash_value = fingerprint(questions)
        if old and old["fingerprint"] == hash_value and old["account_key"] == account_key and old["status"] in {"pending", "approved", "sent"}:
            return False
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute("""INSERT INTO questionnaire(application_id,account_key,fingerprint,questions_json,answers_json,status,created_at,updated_at,error)
                VALUES(?,?,?,?,?,'pending',?,?,NULL)
                ON CONFLICT(application_id) DO UPDATE SET account_key=excluded.account_key,
                fingerprint=excluded.fingerprint, questions_json=excluded.questions_json,
                answers_json=excluded.answers_json, status='pending',
                created_at=excluded.created_at, updated_at=excluded.updated_at, error=NULL""",
                (application_id, account_key, hash_value,
                 json.dumps(questions, ensure_ascii=False), json.dumps(answers, ensure_ascii=False), now, now))
        return True

    @staticmethod
    def valid_answers(questions: list[dict], answers: list[str]) -> bool:
        if len(questions) != len(answers):
            return False
        for q, answer in zip(questions, answers):
            if not answer or len(answer) > ANSWER_LIMIT:
                return False
            if q["kind"] == "choice" and answer not in [x["text"] for x in q["options"]]:
                return False
        return True

    def edit(self, application_id: int, updates: dict[int, str]) -> bool:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM questionnaire WHERE application_id=? AND status='pending'", (application_id,)).fetchone()
            if row is None:
                return False
            questions = json.loads(row["questions_json"])
            answers = json.loads(row["answers_json"])
            for index, raw in updates.items():
                if not 0 <= index < len(answers):
                    return False
                answer = _compact(raw)
                if not answer:
                    return False
                if questions[index]["kind"] == "choice" and answer not in [x["text"] for x in questions[index]["options"]]:
                    return False
                answers[index] = answer
            db.execute("UPDATE questionnaire SET answers_json=?,updated_at=? WHERE application_id=? AND status='pending'",
                (json.dumps(answers, ensure_ascii=False), datetime.now(timezone.utc).isoformat(), application_id))
            return True

    def transition(self, application_id: int, before: str, after: str, error: str | None = None) -> bool:
        row = self.get(application_id)
        if not row or (after == "approved" and not self.valid_answers(row["questions"], row["answers"])):
            return False
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE questionnaire SET status=?,error=?,updated_at=? WHERE application_id=? AND status=?",
                (after, error, datetime.now(timezone.utc).isoformat(), application_id, before))
            return cursor.rowcount == 1


def card_text(application_id: int, title: str, company: str, record: dict) -> str:
    lines = [
        f"{'🟢 CLEAN' if record['account_key']=='clean' else '⚪ OLD'} · 📝 <b>Анкета HH на согласование</b>",
        "", f"<b>{html.escape(title)}</b>", html.escape(company or "Компания не указана"),
        f"Application #{application_id}", "",
    ]
    for idx, (q, answer) in enumerate(zip(record["questions"], record["answers"]), 1):
        lines.extend([
            f"<b>{idx}. {html.escape(q['question'])}</b>",
            ("Варианты: " + " / ".join(html.escape(o["text"]) for o in q["options"])) if q["kind"] == "choice" else "",
            html.escape(answer) if answer else "⚠️ Не хватает подтверждённых данных — впиши ответ.",
            "",
        ])
    lines.append("⚠️ Если HH потребует изменить видимость резюме, агент не сделает это автоматически.")
    lines.append("✅ Одним подтверждением согласуешь всю анкету. Изменить можно несколько ответов разом.")
    message = "\n".join(x for x in lines if x is not None)
    if len(message) > TELEGRAM_LIMIT - 120:
        raise ValueError("Telegram questionnaire card too long")
    return message


def send_card(application_id: int, title: str, company: str, vacancy_url: str, record: dict) -> bool:
    token, chat_id = os.getenv("TELEGRAM_BOT_TOKEN", "").strip(), os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False
    try:
        message = card_text(application_id, title, company, record)
    except ValueError:
        return False
    payload = {
        "chat_id": chat_id, "text": message, "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": {"inline_keyboard": [
            [{"text": "✅ Подтвердить всё", "callback_data": f"hhq_ok:{application_id}"},
             {"text": "✏️ Изменить ответы", "callback_data": f"hhq_edit:{application_id}"}],
            [{"text": "❌ Не отправлять", "callback_data": f"hhq_reject:{application_id}"}],
            [{"text": "Открыть HH", "url": vacancy_url}],
        ]},
    }
    try:
        req = Request("https://api.telegram.org/bot" + token + "/sendMessage",
                      data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                      headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(req, timeout=15) as response:
            return response.status == 200
    except Exception as exc:
        print(f"[HH QUESTIONNAIRE] Telegram delivery failed: {type(exc).__name__}: {exc}", flush=True)
        return False


def fill_questions(page, questions: list[dict], answers: list[str]) -> bool:
    """Fill only stable task names; re-read every value before native submission."""
    if not QuestionnaireStore.valid_answers(questions, answers):
        return False
    try:
        for q, answer in zip(questions, answers):
            selector = f'form[name="vacancy_response"] [name="{q["name"]}"]'
            if q["kind"] == "text":
                field = page.locator(selector)
                if field.count() != 1 or not field.first.is_visible():
                    return False
                field.first.fill(answer)
                if field.first.input_value().strip() != answer:
                    return False
            elif q["kind"] == "choice":
                candidates = page.locator(selector)
                options = q["options"]
                index = next((i for i, o in enumerate(options) if o["text"] == answer), None)
                if index is None or candidates.count() != len(options):
                    return False
                candidates.nth(index).check()
                if not candidates.nth(index).is_checked():
                    return False
            else:
                return False
        return True
    except Exception:
        return False
