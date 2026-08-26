from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, Preformatted, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "doc" / "HH_Agent_System_Documentation.pdf"
FONT = Path(r"C:\Windows\Fonts\arial.ttf")
FONT_BOLD = Path(r"C:\Windows\Fonts\arialbd.ttf")

pdfmetrics.registerFont(TTFont("ArialDoc", str(FONT)))
pdfmetrics.registerFont(TTFont("ArialDocBold", str(FONT_BOLD)))

styles = getSampleStyleSheet()
styles.add(ParagraphStyle(name="H1x", parent=styles["Heading1"], fontName="ArialDocBold", fontSize=23, leading=27, spaceAfter=10, textColor=colors.HexColor("#111827")))
styles.add(ParagraphStyle(name="H2x", parent=styles["Heading2"], fontName="ArialDocBold", fontSize=14, leading=18, spaceBefore=12, spaceAfter=6, textColor=colors.HexColor("#111827")))
styles.add(ParagraphStyle(name="Bodyx", parent=styles["BodyText"], fontName="ArialDoc", fontSize=9.2, leading=13, spaceAfter=5))
styles.add(ParagraphStyle(name="Smallx", parent=styles["BodyText"], fontName="ArialDoc", fontSize=8.1, leading=10.5, textColor=colors.HexColor("#475467")))
styles.add(ParagraphStyle(name="Codex", parent=styles["Code"], fontName="ArialDoc", fontSize=8, leading=10.2, backColor=colors.HexColor("#F8FAFC"), borderColor=colors.HexColor("#E4E7EC"), borderWidth=.5, borderPadding=6, spaceAfter=7))

def P(text, style="Bodyx"):
    return Paragraph(text, styles[style])

def bullets(items):
    return [P("• " + item) for item in items]

def table(rows, widths):
    t = Table(rows, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "ArialDocBold"),
        ("FONTNAME", (0, 1), (-1, -1), "ArialDoc"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.1),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2F4F7")),
        ("GRID", (0, 0), (-1, -1), .4, colors.HexColor("#D0D5DD")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("PADDING", (0, 0), (-1, -1), 4),
    ]))
    return t

def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("ArialDoc", 7)
    canvas.setFillColor(colors.HexColor("#98A2B3"))
    canvas.drawCentredString(A4[0] / 2, 9 * mm, f"HH Agent • System Documentation • main @ 79397da • 2026-08-26 • {doc.page}")
    canvas.restoreState()

story = [
    P("HH AGENT", "H1x"),
    P("<b>Автономный агент поиска, оценки, ручного согласования и безопасного отклика на вакансии</b>"),
    P("Windows · Python 3.12 · Playwright · SQLite · Ollama · Telegram · GitHub Actions", "Smallx"),
    Spacer(1, 4 * mm),
    P("01 / Назначение", "H2x"),
]
story += bullets([
    "Источники: <b>HH.ru, Yandex Jobs и VK Team</b>.",
    "SQLite хранит Vacancy / Evaluation / Application и историю решений.",
    "Hard filters → локальный LLM → deterministic policy → resume matcher.",
    "Telegram используется для просмотра, решения и runtime-команд.",
    "Source-aware apply реализован для HH, Yandex и VK.",
])
story += [P("<b>Safety gate:</b> Yandex и VK автоматически отправляются только при Application.status=approved и latest Evaluation.decision=apply."),
          P("02 / Архитектура", "H2x"),
          Preformatted("""HH.ru ---------\\
Yandex Jobs ----+--> SQLite --> Evaluation --> Telegram
VK Team -------/                    |
                                  Ollama
                                    |
                         +----------+----------+
                         |          |          |
                      HH apply  Yandex apply  VK apply""", styles["Codex"]),
          P("Основной pipeline:"),
          Preformatted("""hh_collect.py
  ↓
collect_careers.py   # Yandex + VK
  ↓
process_vacancies.py
  ↓
apply_dispatcher.py  # source-aware routing""", styles["Codex"]),
          P("03 / Источники", "H2x"),
          table([["Source", "Collector", "Механика"], ["HH", "hh_collect.py", "Playwright, persistent profile"], ["Yandex", "sources/yandex.py", "HTTP career collector"], ["VK", "sources/vk.py", "полный каталог + search/fallback"]], [24*mm, 48*mm, 100*mm]),
          P("Идентичность вакансии: <b>source + external_id</b>; hh_id оставлен как legacy unique key."),
          PageBreak(),
          P("04 / Evaluation pipeline", "H2x")]
story += bullets(["Hard filters до LLM.", "VacancyEvaluator через Ollama.", "Evidence guard для ложных gaps.", "Management policy для management cases.", "Resume matcher.", "Persistence Evaluation/Application state."])
story += [Preformatted("""score = role_match * 0.35
      + seniority_match * 0.20
      + domain_match * 0.15
      + responsibility_match * 0.30""", styles["Codex"]),
          P("Production LLM: <b>gemma4:12b</b>. Telegram threshold: <b>72</b>."),
          P("05 / Apply architecture", "H2x"),
          P("<b>HH:</b> background_apply.py → apply_worker.py. Получает только HH Applications."),
          P("<b>Yandex/VK:</b> background_pipeline.py → apply_dispatcher.py → соответствующий worker."),
          Preformatted("Application.status == approved\nAND latest Evaluation.decision == apply", styles["Codex"]),
          P("<b>VK:</b> persistent profile, выбранный PDF resume, поля «Расскажи о себе», social_links, agree, ручная CAPTCHA в headful. После submit worker ждёт фактический результат и не делает blind retry при неоднозначности."),
          P("06 / Application status", "H2x"),
          table([["Status", "Значение"], ["notified", "карточка показана, решения нет"], ["approved", "отклик разрешён пользователем"], ["applying", "worker начал обработку"], ["waiting_captcha", "VK ждёт ручную CAPTCHA"], ["applied", "success подтверждён, applied_at заполнен"], ["manual_required", "безопасная остановка"], ["apply_error", "техническая ошибка"], ["skipped", "пользователь пропустил"], ["company_blacklist", "blacklist workflow"]], [40*mm, 132*mm]),
          PageBreak(),
          P("07 / Telegram", "H2x"),
          P("Production entry point: <b>telegram_bot_entry.py</b>. Он подключает source-aware link patch и /new pending patch.")]
story += bullets(["/health — healthcheck.", "/status — runtime states.", "/run — запуск pipeline.", "/new — новые + все notified без решения.", "/stats — статистика статусов."])
story += [P("Карточка: Откликнуться → approved; Пропустить → skipped; Компания в blacklist → company_blacklist; ссылка Открыть HH / Yandex / VK."),
          P("Надёжность /new", "H2x")]
story += bullets(["Сначала выбираются все Application.status=notified независимо от score.", "Затем добавляются новые выше TELEGRAM_MIN_SCORE.", "До 4 попыток доставки при NetworkError / TimedOut / RetryAfter.", "Пауза между сообщениями; ошибка одной карточки не ломает пачку.", "Диагностика пишется в logs/telegram.log."])
story += [P("<b>Security:</b> бот пока работает в public mode; ограничение доступа остаётся backlog item."),
          P("08 / Windows Scheduler", "H2x"),
          table([["Task", "Период", "Entry point"], ["HH Agent - Pipeline", "30 мин", "background_pipeline.py"], ["HH Agent - Apply", "10 мин", "background_apply.py"], ["HH Agent - Resume Raise", "30 мин", "background_resume_raise.py"], ["HH Agent - Telegram", "logon/restart", "run_telegram_hidden.vbs → telegram_bot_entry.py"]], [50*mm, 30*mm, 92*mm]),
          P("Pipeline / Apply / Resume Raise используют общий AgentLock для предотвращения параллельных browser jobs."),
          PageBreak(),
          P("09 / Profiles, runtime, logs", "H2x"),
          P("Persistent profiles: HH → browser-profile; Yandex → yandex-browser-profile; VK → vk-browser-profile."),
          Preformatted("""logs/pipeline_supervisor.log
logs/careers_collector.log
logs/apply_dispatcher.log
logs/yandex_apply_worker*.log
logs/vk_apply_worker*.log
logs/telegram.log""", styles["Codex"]),
          P("10 / Environment", "H2x"),
          Preformatted("""LLM_MODEL=gemma4:12b
LLM_TIMEOUT=180
LLM_NUM_CTX=16384
TELEGRAM_MIN_SCORE=72
YANDEX_APPLY_LIVE=false
VK_APPLY_LIVE=false
VK_APPLY_HEADLESS=false
VK_APPLY_CAPTCHA_WAIT_SECONDS=300
VK_APPLY_SUCCESS_WAIT_SECONDS=10""", styles["Codex"]),
          P("11 / CI", "H2x")]
story += bullets(["GitHub Actions: .github/workflows/ci.yml.", "windows-latest, Python 3.12.", "git diff --check, compileall, unittest discover.", "Запуск на pull request и push в main."])
story += [PageBreak(), P("12 / Safety invariants", "H2x")]
story += bullets(["Source separation: каждый worker получает только свой source.", "Yandex/VK auto = apply only: approved недостаточно.", "No blind retry after submit.", "Local resume source of truth.", "Evaluation history не удаляется.", "AgentLock обязателен для background browser jobs.", "/new не меняет решения: повторяет notified и создаёт Application только для новых карточек."])
story += [P("13 / Технический долг", "H2x")]
story += bullets(["Ограничить Telegram public mode.", "Консолидировать telegram runtime patch-модули в основной модуль.", "Добавить word-boundary regression coverage для коротких role markers.", "Провести public sanitization перед открытием репозитория.", "Добавить production CD на Windows self-hosted runner."])
story += [P("14 / Release checklist", "H2x")]
story += bullets(["Читать актуальный main перед изменением.", "Работать через отдельную branch + PR.", "Проверять syntax/imports и unit tests.", "Проверять source routing и latest-decision guard.", "Для live submit использовать targeted Application ID.", "После live test проверять status/applied_at и логи.", "Не делать повторный submit при неоднозначном результате.", "При изменении архитектуры обновлять README и этот PDF."])
story += [Spacer(1, 4*mm), P("HH AGENT / rudenko.one", "Smallx"), P("Документация актуализирована для main @ 79397da (2026-08-26).", "Smallx")]

doc = SimpleDocTemplate(str(OUT), pagesize=A4, rightMargin=16*mm, leftMargin=16*mm, topMargin=15*mm, bottomMargin=16*mm)
doc.build(story, onFirstPage=footer, onLaterPages=footer)
print(OUT)
