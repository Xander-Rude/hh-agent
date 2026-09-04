# HH Agent — System Documentation

**Version:** 2026-09-04
**Code snapshot:** `main @ ecfc658`
**Platform:** Windows · Python 3.12 · Playwright · SQLite · Ollama · Telegram · GitHub Actions

This document describes the current production architecture and operational rules of HH Agent. The PDF in the same directory is the rendered distribution artifact; this Markdown file is the maintainable source of truth for future documentation changes.

---

## 1. Purpose

HH Agent automates vacancy discovery, evaluation, Telegram review and controlled application submission.

Current vacancy sources:

- **HH.ru** — collection and application automation;
- **Yandex Jobs** — collection and guarded application automation;
- **VK Team** — collection and guarded application automation with CAPTCHA/manual safety flow;
- **T-Bank** — collection/discovery only; no automatic apply adapter exists yet.

The system deliberately separates collection, evaluation and site-specific application adapters.

---

## 2. High-level architecture

```text
HH.ru --------------------------┐
Yandex Jobs --------------------┤
VK Team ------------------------┤--> SQLite --> Evaluation --> Telegram
T-Bank -------------------------┘                    |
                                                     +--> HH apply worker
                                                     +--> Yandex apply worker
                                                     +--> VK apply worker
```

Primary scheduled pipeline:

```text
hh_collect.py
    -> collect_careers.py       # Yandex + VK + T-Bank
    -> process_vacancies.py
    -> apply_dispatcher.py      # HH + Yandex + VK
```

Key design rule: a site UI change may affect its collector/adapter but must not silently redefine evaluation policy, permission rules or cover-letter content.

---

## 3. Data model and lifecycle

### Vacancy

Stores normalized vacancy data and source identity. The canonical identity is `source + external_id`; `hh_id` remains for legacy HH compatibility.

### Evaluation

Append-only evaluation history containing:

- overall score and decision;
- role/seniority/domain/responsibility fit;
- gaps/red flags/recommendation;
- generated cover letter;
- selected resume key and matching metadata.

Evaluation history is retained for audit and diagnostics.

### Application

Stores the explicit user decision and operational application lifecycle.

Important statuses:

| Status | Meaning |
|---|---|
| `notified` | shown in Telegram, no user decision yet |
| `approved` | user explicitly authorized an application |
| `applying` | worker started processing |
| `waiting_captcha` | manual action/CAPTCHA is required |
| `applied` | success confirmed and `applied_at` set |
| `manual_required` | automation stopped safely and needs a human |
| `apply_error` | technical failure before confirmed submission |
| `skipped` | user skipped the vacancy |
| `company_blacklist` | company marked for blacklist workflow |

---

## 4. Evaluation pipeline

Processing order:

1. hard filters;
2. structured Ollama evaluation;
3. evidence guard;
4. deterministic management policy;
5. resume matcher;
6. persistence to SQLite.

Score formula:

```text
score =
    role_match           * 0.35 +
    seniority_match      * 0.20 +
    domain_match         * 0.15 +
    responsibility_match * 0.30
```

Current defaults:

```text
LLM_MODEL=gemma4:12b
LLM_BASE_URL=http://localhost:11434
LLM_TIMEOUT=180
LLM_MAX_RETRIES=2
LLM_NUM_CTX=16384
TELEGRAM_MIN_SCORE=72
```

Cover letters use only confirmed profile/resume facts, connect a small number of relevant facts to the vacancy and avoid placeholders or invented experience.

---

## 5. Vacancy sources

### 5.1 HH.ru

`hh_collect.py` uses Playwright and a persistent HH browser profile.

Request pressure is intentionally limited:

```text
HH_RECOMMENDATION_PAGES=3
HH_FALLBACK_MIN_NEW_FROM_RECOMMENDATIONS=10
HH_DELAY_BETWEEN_VACANCIES=7
HH_DELAY_BETWEEN_PAGES=10
HH_DELAY_BETWEEN_QUERIES=15
HH_COLLECT_NAVIGATION_TIMEOUT_MS=30000
HH_COLLECT_WATCHDOG_SECONDS=120
```

Personalized recommendations are processed first. If they already produce enough new vacancies, the broader target-role fallback search is skipped.

The collector has a parent watchdog. If Playwright/Chromium freezes below normal navigation timeouts, the process tree can be terminated while already committed vacancies remain in SQLite and the pipeline can continue.

### 5.2 Yandex Jobs

Collected through `sources/yandex.py` as part of `collect_careers.py`.

Applications are routed through `yandex_apply_worker.py` only after explicit `approved` and only when `YANDEX_APPLY_LIVE=true`.

### 5.3 VK Team

Collected through `sources/vk.py`.

The VK apply worker can:

- fill contact/about/social fields;
- upload the selected resume;
- handle consent;
- allow bounded manual CAPTCHA completion;
- detect success/failure markers and structural form changes.

Ambiguous post-submit results are not automatically retried.

### 5.4 T-Bank

`sources/tbank.py` implements T-Bank discovery.

Current behavior:

- scans the IT catalog and the general Moscow catalog;
- recognizes `it` and `back-office` vacancy paths;
- runs static pagination first;
- then runs dynamic Playwright discovery to cover lazy-loaded results;
- deduplicates by vacancy UUID;
- filters inactive/closed vacancies;
- applies a management/product/project title allow-list and rejects intern/junior roles;
- retries transient HTTP errors and handles `429` with bounded backoff.

T-Bank is currently a **discovery-only source**. There is no `tbank_apply_worker.py` and no automatic final submit path.

### 5.5 Career-source fault isolation

`collect_careers.py` runs Yandex, VK and T-Bank independently. A failure of one source does not stop the others; the collector returns a failure code only when every configured career source fails.

---

## 6. Application permission model

This section is safety-critical.

### 6.1 User approval is authoritative

Current `apply_dispatcher.py` behavior:

```text
Application.status == approved
```

means the user has explicitly authorized the application.

The older documented rule requiring:

```text
approved AND latest Evaluation.decision == apply
```

is obsolete. Evaluation remains audit/context data after user approval and does not act as a second veto gate for Yandex/VK.

### 6.2 Operational live switches

Yandex and VK have an additional kill switch:

```text
YANDEX_APPLY_LIVE=true
VK_APPLY_LIVE=true
```

With the relevant switch set to `false`, the dispatcher may display the approved queue but must not press the final submit control.

### 6.3 Source routing

- HH worker receives only `approved` HH applications and legacy `source IS NULL` HH rows.
- Yandex worker receives only `approved` Yandex applications.
- VK worker receives only `approved` VK applications.
- T-Bank currently has no automatic apply worker.

### 6.4 No blind retry after submit

If the site may have accepted the application but success cannot be confirmed, the system uses `manual_required` instead of automatically submitting again.

This is a hard invariant: **never repeat an ambiguous submit until a human has established that the first submit did not succeed.**

---

## 7. Resume and cover-letter handling

Resume selection and cover-letter generation happen before the site adapter layer.

The selected local PDF is validated through `app/application_assets.py` and is treated as the submission source of truth.

Yandex and VK upload the selected resume asset to their forms.

### Cover letter by vacancy URL in Telegram

`telegram_cover_letter_patch.py` and `app/vacancy_url.py` allow a vacancy URL to be sent directly to the bot.

Flow:

1. extract and canonicalize the URL;
2. identify HH/Yandex/VK when possible;
3. reuse a cached Evaluation cover letter if available;
4. otherwise fetch the vacancy;
5. generate a letter using the local resume/preferences and Ollama;
6. send vacancy metadata and a separate copy-ready cover letter message.

Known HH/Yandex/VK sites use source-aware fetchers. Other public HTTP(S) pages use a generic fetch path with protections against localhost/private/service networks, nonstandard ports and unbounded redirects.

---

## 8. Telegram control plane

Production entry point:

```text
telegram_bot_entry.py
```

Installed runtime patches:

```text
telegram_bot_link_patch.py
telegram_bot_pending_patch.py
telegram_cover_letter_patch.py
telegram_cover_letter_output_patch.py
telegram_queue_stats_patch.py
```

Main commands:

| Command | Behavior |
|---|---|
| `/health` | system/Ollama/runtime/queue health |
| `/status` | background state and approved queue breakdown |
| `/run` | trigger the pipeline now |
| `/new` | recover `manual_required`, unresolved `notified`, then show new candidates |
| `/stats` | Application status statistics |

`/health` and `/status` show approved queue totals by HH, Yandex, VK and T-Bank.

### `/new` reliability

`telegram_bot_pending_patch.py`:

1. resurfaces `manual_required` applications;
2. resurfaces unresolved `notified` applications regardless of the current score filter;
3. adds genuinely new vacancies above `TELEGRAM_MIN_SCORE`;
4. avoids duplicate Application rows;
5. retries transient Telegram `NetworkError`, `TimedOut` and `RetryAfter` failures;
6. pauses between messages;
7. does not abort the entire batch when one card fails.

### `manual_required` notifications

`application_notifications.py` sends a best-effort Telegram alert containing:

- vacancy title and company;
- reason for manual intervention;
- Application ID;
- button to open the vacancy manually.

If this background alert fails, `/new` still recovers the `manual_required` card later.

### Security backlog

Telegram currently operates in public mode and accepts commands from any Telegram chat. An allow-list/access-control layer remains a security backlog item.

---

## 9. Windows scheduler

Current production cadence:

| Task | Schedule | Main entry point | StartWhenAvailable |
|---|---|---|---|
| `HH Agent - Pipeline` | every 2 hours | `background_pipeline.py` | yes |
| `HH Agent - Apply` | every 10 minutes | `background_apply.py` | yes |
| `HH Agent - Resume Raise` | every 2 hours | `background_resume_raise.py` | yes |
| `HH Agent - Telegram` | at logon | `telegram_bot_entry.py` | yes |

Pipeline, Apply and Resume Raise use the shared `AgentLock` so conflicting background browser jobs do not run concurrently.

Telegram task additionally uses:

- `MultipleInstances=IgnoreNew`;
- restart every 1 minute after failure;
- `RestartCount=999`;
- no forced 72-hour execution limit;
- battery-friendly settings.

`install_resume_raise_task.ps1` explicitly sets `StartWhenAvailable=True` after `schtasks /Create`, because the `schtasks` creation command does not expose this property directly.

---

## 10. Resume Raise reliability

`resume_raise_worker_v2.py` now retries temporary HH navigation failures.

Defaults:

```text
HH_RESUME_RAISE_HEADLESS=true
HH_RESUME_RAISE_NAV_TIMEOUT_MS=30000
HH_RESUME_RAISE_NAV_RETRIES=3
HH_RESUME_RAISE_NAV_RETRY_DELAY_MS=15000
```

Retry classification includes:

- `net::ERR_NAME_NOT_RESOLVED`;
- `net::ERR_INTERNET_DISCONNECTED`;
- `net::ERR_NETWORK_CHANGED`;
- `net::ERR_CONNECTION_RESET`;
- `net::ERR_CONNECTION_TIMED_OUT`;
- `net::ERR_TIMED_OUT`;
- `net::ERR_PROXY_CONNECTION_FAILED`;
- `net::ERR_TUNNEL_CONNECTION_FAILED`.

Unexpected Playwright errors remain visible and are not silently swallowed.

---

## 11. Browser profiles, runtime and logs

Persistent browser profiles:

```text
HH      C:\hh-agent\browser-profile
Yandex  C:\hh-agent\yandex-browser-profile
VK      C:\hh-agent\vk-browser-profile
```

Runtime state:

```text
data/runtime/pipeline.json
data/runtime/apply.json
data/runtime/resume_raise.json
data/runtime/telegram.json
```

Main logs:

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

Browser profiles, `.env`, SQLite data, runtime state and logs are local operational data and must not be committed.

---

## 12. Environment reference

Important variables:

```text
LLM_PROVIDER=ollama
LLM_MODEL=gemma4:12b
LLM_BASE_URL=http://localhost:11434
LLM_TIMEOUT=180
LLM_MAX_RETRIES=2
LLM_NUM_CTX=16384

TELEGRAM_MIN_SCORE=72
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

HH_RECOMMENDATION_PAGES=3
HH_FALLBACK_MIN_NEW_FROM_RECOMMENDATIONS=10
HH_DELAY_BETWEEN_VACANCIES=7
HH_DELAY_BETWEEN_PAGES=10
HH_DELAY_BETWEEN_QUERIES=15
HH_COLLECT_NAVIGATION_TIMEOUT_MS=30000
HH_COLLECT_WATCHDOG_SECONDS=120

HH_APPLY_HEADLESS=false
HH_APPLY_MAX_PER_RUN=10

HH_RESUME_RAISE_HEADLESS=true
HH_RESUME_RAISE_NAV_TIMEOUT_MS=30000
HH_RESUME_RAISE_NAV_RETRIES=3
HH_RESUME_RAISE_NAV_RETRY_DELAY_MS=15000

YANDEX_APPLY_LIVE=false
YANDEX_APPLY_APPLICATION_ID=

VK_APPLY_LIVE=false
VK_APPLY_APPLICATION_ID=
VK_APPLY_CAPTCHA_WAIT_SECONDS=300
VK_APPLY_SUCCESS_WAIT_SECONDS=10

APPLY_DISPATCH_HH=true
```

Never commit real tokens, credentials, browser sessions or personal form values.

---

## 13. Safety invariants

The following rules must survive refactoring:

1. **Source separation** — a worker must not accidentally consume another source queue.
2. **User approval is authoritative** — `approved` means explicit authorization.
3. **External live switch** — Yandex/VK final submit additionally requires `*_APPLY_LIVE=true`.
4. **No blind retry after submit** — ambiguous submit results are not automatically repeated.
5. **Local resume is the submission source of truth.**
6. **Evaluation history remains auditable** and is not deleted for convenience.
7. **AgentLock is required** for conflicting background browser jobs.
8. **`/new` does not rewrite decisions**; it recovers unresolved cards and adds new ones.
9. **T-Bank is not an automatic-apply source** until a dedicated adapter is implemented.
10. **Changes to `main` go through a branch and PR.**

---

## 14. Changes since documentation snapshot `79397da`

Major accumulated changes now reflected here:

- T-Bank added as a career source;
- T-Bank discovery expanded to static + dynamic full discovery across IT/back-office paths;
- regression tests added for T-Bank discovery;
- `app/vacancy_url.py` added;
- Telegram cover-letter generation from vacancy URLs added;
- source queue breakdown added to `/health` and `/status`;
- Telegram notification for `manual_required` added;
- `/new` recovery of `manual_required` added and fixed in production;
- Windows CI hardened for UTF-8 output;
- `LICENSE` added;
- HH request pressure reduced through lower scheduler cadence, recommendation-first fallback and navigation delays;
- Resume Raise moved to a two-hour cadence and preserves missed runs with `StartWhenAvailable=True`;
- Resume Raise now retries transient DNS/network navigation failures;
- documentation corrected to the current Yandex/VK permission model where `approved` is the user's final authorization.

---

## 15. Repository structure additions

Important current files include:

```text
app/vacancy_url.py
application_notifications.py
sources/tbank.py
telegram_cover_letter_patch.py
telegram_cover_letter_output_patch.py
telegram_queue_stats_patch.py
tests/test_application_notifications.py
tests/test_tbank_source.py
tests/test_telegram_cover_letter_patch.py
tests/test_telegram_cover_letter_output_patch.py
tests/test_telegram_manual_required_recovery.py
```

Documentation:

```text
doc/HH_Agent_System_Documentation.md
doc/HH_Agent_System_Documentation.pdf
```

---

## 16. Engineering workflow

Required repository workflow:

```text
branch -> commits -> PR -> checks/review -> merge
```

Do not commit directly to `main`.

Before merging changes to collectors, evaluator, apply workers or Telegram:

- review the actual current target files;
- check whitespace/diff quality;
- run syntax/import checks and unit tests;
- verify source routing and permission behavior;
- use targeted Application IDs for live submission testing;
- verify `status`, `applied_at` and logs after a live test;
- never repeat an ambiguous submit;
- update README and `doc/HH_Agent_System_Documentation.*` when architecture or operations change.

---

## 17. Technical debt / backlog

- consolidate Telegram runtime patch modules into a cleaner primary module structure;
- add Telegram access control/allow-list;
- implement a T-Bank apply adapter only with explicit safety semantics;
- add more word-boundary regression coverage for short role markers;
- implement production CD on a Windows self-hosted runner if desired;
- keep Markdown and PDF documentation synchronized in the same PR.
