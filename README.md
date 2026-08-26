<div align="center">

# HH AGENT

**Автономный агент поиска, оценки, ручного согласования и безопасного отклика на вакансии**  
Windows · Python 3.12 · Playwright · SQLite · Ollama · Telegram · GitHub Actions

[rudenko.one](https://rudenko.one/) · `main @ 79397da`

</div>

---

## 01 / Что это

HH Agent автоматизирует полный цикл работы с вакансиями:

- собирает вакансии с **HH.ru**, **Yandex Jobs** и **VK Team**;
- сохраняет вакансии, историю оценок и жизненный цикл отклика в **SQLite**;
- применяет hard filters до LLM;
- оценивает fit локальной моделью через **Ollama**;
- детерминированно корректирует management-кейсы;
- выбирает подходящее резюме и формирует vacancy-aware текст;
- показывает вакансии и runtime через **Telegram**;
- повторно показывает через `/new` карточки со статусом `notified`, пока пользователь не примет решение;
- выполняет отклики на **HH**, **Yandex** и **VK** с разными safety-политиками.

> [!IMPORTANT]
> Для **Yandex** и **VK** автоматическая отправка разрешена только когда `Application.status=approved` и **latest Evaluation = `apply`**. `review` и `reject` автоматически не отправляются.

---

## 02 / Архитектура

```mermaid
flowchart LR
    HH[HH.ru\nPlaywright] --> DB[(SQLite)]
    YA[Yandex Jobs\nHTTP] --> DB
    VK[VK Team\nHTTP/catalog] --> DB
    DB --> EVAL[Evaluation pipeline\nfilters → LLM → policy → resume]
    EVAL <--> LLM[Ollama / Gemma 4 12B]
    EVAL --> DB
    DB <--> TG[Telegram]
    DB --> HHA[HH apply worker]
    DB --> YAA[Yandex apply worker]
    DB --> VKA[VK apply worker]
```

Основной pipeline:

```text
hh_collect.py
    ↓
collect_careers.py       # Yandex + VK
    ↓
process_vacancies.py
    ↓
apply_dispatcher.py      # source-aware routing
```

Ключевой принцип: **collectors, evaluation и site adapters разделены**. UI конкретного сайта не должен определять scoring, policy или содержание текста отклика.

---

## 03 / Источники вакансий

| Source | Collector | Механика |
|---|---|---|
| HH | `hh_collect.py` | Playwright, persistent profile |
| Yandex | `sources/yandex.py` | HTTP career collector |
| VK | `sources/vk.py` | полный каталог + поисковые/fallback проходы |

`collect_careers.py` запускает Yandex и VK независимо: падение одного источника не ломает второй.

Идентичность вакансии: `source + external_id`; поле `hh_id` сохранено как legacy unique key.

---

## 04 / Модель данных

```mermaid
erDiagram
    Vacancy ||--o{ Evaluation : has
    Vacancy ||--o{ Application : has

    Vacancy {
        int id
        string hh_id
        string source
        string external_id
        string title
        string company
        string url
        bool processed
    }

    Evaluation {
        int id
        int vacancy_id
        int score
        string decision
        int role_match
        int seniority_match
        int domain_match
        int responsibility_match
        string cover_letter
        string selected_resume_key
        int selected_resume_score
    }

    Application {
        int id
        int vacancy_id
        string status
        string cover_letter
        string selected_resume_key
        datetime applied_at
    }
```

### Vacancy
Сырой объект вакансии.

### Evaluation
История оценки: score, decision, 4 измерения fit, gaps/red flags, recommendation, текст отклика и выбранное резюме.

### Application
Состояние конкретного отклика и фактически используемые resume/cover-letter данные.

---

## 05 / Evaluation pipeline

Порядок обработки:

1. **Hard filters** — быстрые однозначные запреты до LLM.
2. **VacancyEvaluator** — structured response от Ollama.
3. **Evidence guard** — убирает ложные gaps, если профиль подтверждает опыт.
4. **Management policy** — детерминированная корректировка management cases.
5. **Resume matcher** — выбирает одно из существующих резюме.
6. **Persistence** — сохраняет Evaluation и Application-state для дальнейшего Telegram/apply workflow.

Итоговый score:

```text
score =
    role_match           * 0.35 +
    seniority_match      * 0.20 +
    domain_match         * 0.15 +
    responsibility_match * 0.30
```

Текущая production-модель: `gemma4:12b` через Ollama. Telegram notification threshold по умолчанию: `72`.

---

## 06 / Резюме и текст отклика

Выбор резюме и генерация текста выполняются **до adapter layer**.

- используются только подтверждённые факты профиля;
- 2–3 наиболее релевантных факта связываются с задачами вакансии;
- запрещены placeholders и third-person формулировки;
- выбранный локальный PDF валидируется через `app/application_assets.py`;
- Yandex и VK перед отправкой загружают выбранный PDF в форму;
- VK поле `description` трактуется как **«Расскажи о себе»**;
- VK отдельно заполняет `social_links` и согласие `agree`.

---

## 07 / Apply architecture

### HH

```text
HH Agent - Apply
    ↓
background_apply.py
    ↓
apply_worker.py
```

HH worker получает только HH Applications.

### Yandex и VK

```text
background_pipeline.py
    ↓
apply_dispatcher.py
    ├─ yandex_apply_worker.py
    └─ vk_apply_worker.py
```

Для обоих внешних источников dispatcher применяет guard:

```text
Application.status == approved
AND latest Evaluation.decision == apply
```

`*_APPLY_LIVE=false` никогда не нажимает финальный submit.

### VK safety flow

VK worker использует persistent profile `vk-browser-profile`, валидирует обязательные поля, поддерживает ручное прохождение CAPTCHA в headful-режиме и после submit ждёт фактический результат.

Успех определяется по:

- явным success-маркерам;
- исчезновению формы/submit после отправки;
- отсутствию failure-маркеров.

Если submit уже был нажат, но результат неоднозначен, worker **не делает повторный submit** и переводит кейс в `manual_required`.

---

## 08 / Статусы Application

| Status | Значение |
|---|---|
| `notified` | карточка показана в Telegram, решение ещё не принято |
| `approved` | пользователь разрешил отклик |
| `applying` | worker начал обработку |
| `waiting_captcha` | VK ждёт ручного прохождения CAPTCHA |
| `applied` | успех подтверждён, `applied_at` заполнен |
| `manual_required` | автоматика остановилась безопасно |
| `apply_error` | техническая ошибка |
| `skipped` | пользователь пропустил вакансию |
| `company_blacklist` | компания отмечена для blacklist workflow |

---

## 09 / Telegram

Production entry point: `telegram_bot_entry.py`.

Он устанавливает source-aware ссылку и patch `/new` поверх основного `telegram_bot.py`.

Команды:

| Command | Что делает |
|---|---|
| `/health` | healthcheck проекта, Ollama, runtime и очередей |
| `/status` | текущие background states |
| `/run` | запускает pipeline сейчас |
| `/new` | **новые вакансии + все `notified` без решения** |
| `/stats` | статистика Application status |

Карточка содержит:

- `✅ Откликнуться` → `approved`;
- `❌ Пропустить` → `skipped`;
- `🚫 Компания в blacklist` → `company_blacklist`;
- source-aware ссылку `Открыть HH / Yandex / VK`.

### Надёжность `/new`

`telegram_bot_pending_patch.py`:

- сначала выбирает **все** `Application.status=notified` независимо от текущего score;
- затем добавляет новые вакансии выше `TELEGRAM_MIN_SCORE`;
- не создаёт повторные Application;
- делает до 4 попыток доставки при `NetworkError`, `TimedOut`, `RetryAfter`;
- выдерживает паузу между сообщениями;
- ошибка одной карточки не прерывает всю пачку;
- пишет диагностику в `telegram.log`.

> [!CAUTION]
> Telegram bot сейчас работает в **public mode**: команды принимаются из любого Telegram chat. Ограничение доступа остаётся security backlog item.

---

## 10 / Windows Scheduler

| Task | Период | Entry point |
|---|---|---|
| `HH Agent - Pipeline` | каждые 30 минут | `background_pipeline.py` |
| `HH Agent - Apply` | каждые 10 минут | `background_apply.py` |
| `HH Agent - Resume Raise` | каждые 30 минут | `background_resume_raise.py` |
| `HH Agent - Telegram` | at logon + restart on failure | `run_telegram_hidden.vbs` → `telegram_bot_entry.py` |

`Pipeline`, `Apply` и `Resume Raise` используют общий **AgentLock** и не запускают параллельно конфликтующие browser jobs.

Перезапуск Telegram:

```powershell
Stop-ScheduledTask -TaskName "HH Agent - Telegram"
Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -match "telegram_bot_entry\.py" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Start-ScheduledTask -TaskName "HH Agent - Telegram"
```

---

## 11 / Persistent profiles

| Source | Profile |
|---|---|
| HH | `C:\hh-agent\browser-profile` |
| Yandex | `C:\hh-agent\yandex-browser-profile` |
| VK | `C:\hh-agent\vk-browser-profile` |

Профили, `.env`, БД, runtime-data и логи не должны попадать в репозиторий.

---

## 12 / Runtime и логи

Runtime state: `data/runtime/`.

Основные логи:

```text
logs/pipeline_supervisor.log
logs/collector.log
logs/careers_collector.log
logs/processor.log
logs/apply_dispatcher.log
logs/apply_supervisor.log
logs/apply_worker_runtime.log
logs/yandex_apply_worker.log
logs/yandex_apply_worker_attention.log
logs/vk_apply_worker.log
logs/vk_apply_worker_attention.log
logs/telegram.log
logs/resume_raise_supervisor.log
logs/resume_raise_worker.log
```

Полезные команды:

```powershell
Get-Content C:\hh-agent\logs\telegram.log -Tail 160
Get-Content C:\hh-agent\logs\apply_dispatcher.log -Tail 150
Get-Content C:\hh-agent\data\runtime\pipeline.json
```

---

## 13 / Environment

Ключевые переменные:

```text
LLM_PROVIDER=ollama
LLM_MODEL=gemma4:12b
LLM_BASE_URL=http://localhost:11434
LLM_TIMEOUT=180
LLM_MAX_RETRIES=2
LLM_THINK=false
LLM_NUM_CTX=16384

TELEGRAM_MIN_SCORE=72

HH_APPLY_HEADLESS=false
HH_APPLY_MAX_PER_RUN=10

YANDEX_APPLY_LIVE=false
YANDEX_APPLY_HEADLESS=true
YANDEX_APPLY_MAX_PER_RUN=5
YANDEX_APPLY_APPLICATION_ID=

VK_APPLY_LIVE=false
VK_APPLY_HEADLESS=false
VK_APPLY_MAX_PER_RUN=5
VK_APPLY_DELAY_SECONDS=3
VK_APPLY_APPLICATION_ID=
VK_APPLY_CAPTCHA_WAIT_SECONDS=300
VK_APPLY_SUCCESS_WAIT_SECONDS=10
VK_APPLY_NAME=
VK_APPLY_FIRST_NAME=
VK_APPLY_LAST_NAME=
VK_APPLY_EMAIL=
VK_APPLY_PHONE=
VK_APPLY_SOCIAL_LINKS=

APPLY_DISPATCH_HH=true
```

Не коммитить реальные токены, credentials и персональные значения form fields.

---

## 14 / CI

GitHub Actions workflow: `.github/workflows/ci.yml`.

Windows-only checks на `windows-latest` / Python 3.12:

1. `git diff --check`;
2. `python -m compileall -q .`;
3. `python -m unittest discover -s tests -p "test_*.py" -v`.

CI запускается на pull request и push в `main`.

---

## 15 / Структура репозитория

```text
app/
  db.py
  evaluator.py
  evaluation_policy.py
  hard_filters.py
  role_filter.py
  resume_matcher.py
  application_assets.py

sources/
  base.py
  yandex.py
  vk.py

hh_collect.py
collect_careers.py
process_vacancies.py

apply_worker.py
apply_dispatcher.py
yandex_apply_worker.py
vk_apply_worker.py

background_common.py
background_pipeline.py
background_apply.py
background_resume_raise.py

telegram_bot.py
telegram_bot_entry.py
telegram_bot_link_patch.py
telegram_bot_pending_patch.py

.github/workflows/ci.yml

tests/
doc/HH_Agent_System_Documentation.pdf
```

---

## 16 / Safety invariants

1. **Source separation** — каждый worker получает только свой source.
2. **Yandex/VK auto = `apply` only** — одного `approved` недостаточно.
3. **No blind retry after submit** — неоднозначный результат не должен приводить к повторной отправке.
4. **Local resume source of truth** — перед отправкой валидируется и загружается выбранный PDF.
5. **Не удалять Evaluation history** — latest + история нужны для audit/safety.
6. **AgentLock обязателен** для background browser jobs.
7. **`/new` не меняет решения** — он только повторно показывает `notified` и создаёт Application для действительно новых карточек.

---

## 17 / Технический долг

- Telegram public mode требует ограничения доступа.
- `telegram_bot.py` пока дополняется runtime patch-модулями; позже имеет смысл консолидировать их в основной модуль.
- `role_filter.py`: substring-проверка некоторых коротких ролей требует word-boundary regression coverage.
- Public sanitization перед открытием репозитория: история коммитов, персональные filenames/fixtures/docs.
- Нет production CD на Windows self-hosted runner; сейчас есть CI, но deployment остаётся отдельным этапом.

---

## 18 / Release checklist

Перед merge изменений в collectors / evaluator / apply / Telegram:

- читать актуальный файл `main` перед изменением;
- работать через отдельную branch + PR;
- проверить syntax/imports и unit tests;
- проверить source routing и latest-decision guard;
- для live submit использовать targeted Application ID;
- после live test проверить `status / applied_at` и логи;
- не делать повторный submit при неоднозначном результате;
- при изменении архитектуры обновлять **README** и `doc/HH_Agent_System_Documentation.pdf`.

---

<div align="center">

**HH AGENT / rudenko.one**

Документация актуализирована для `main @ 79397da` (2026-08-26).

</div>
