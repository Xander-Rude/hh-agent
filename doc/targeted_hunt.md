# Targeted Hunt

Targeted Hunt is a second acquisition channel beside mass application. Its goal is not to automate more clicks; it is to find a credible human entry point into a company and prepare a grounded outreach package.

## Principles

1. Existing apply flow stays independent and keeps running.
2. Expensive research is limited to high-value vacancies.
3. Facts require provenance. LLM reasoning may rank evidence but must not invent people or contacts.
4. User-supplied intelligence is first-class data and may override automated ranking.
5. Public/professional contact data only; no leaked databases or guessed private contact details.
6. Outreach remains human-in-the-loop: the agent prepares; the user sends.
7. Research respects the existing Ollama GPU guard and defers when the local GPU is busy.

## Data model

- `Company`: reusable company identity.
- `Person`: person associated with a company, with provenance/confidence.
- `Contact`: public or user-supplied professional contact channel and evidence.
- `Note`: knowledge attached to exactly one company, person, or vacancy.
- `Relationship`: graph edge such as reports_to / works_with / referred_by.
- `IntelligenceSource`: evidence URL/excerpt for a fact.
- `TargetedHuntCase`: one research case per vacancy.
- `EntryPoint`: ranked candidate for getting into a live conversation. A user-locked entry point outranks automation.
- `OutreachAttempt`: human-sent outreach state and outcome.

Manual people are deduplicated within a company by normalized name. Contacts are unique per person/channel/value. Manual entry points are validated against the vacancy company before being locked.

## Flow

`Evaluation -> eligibility -> TargetedHuntCase -> public evidence research -> people discovery/ranking -> Telegram review -> user-supplied intelligence/override -> human outreach -> outcome feedback`

Research candidates are persisted only when cited search evidence contains the person's name. No evidence means retry/manual research, never a hallucinated fallback.

## Configuration

- `TARGETED_HUNT_ENABLED` (default `false`)
- `TARGETED_HUNT_MAX_CASES_PER_RUN` (default `3`)
- `TARGETED_HUNT_MIN_SCORE` (default `85`)
- `TARGETED_HUNT_MIN_ROLE_MATCH` (default `80`)
- `TARGETED_HUNT_MIN_SENIORITY_MATCH` (default `80`)
- `TARGETED_HUNT_MAX_RED_FLAGS` (default `0`)
- `TARGETED_HUNT_SEARCH_TIMEOUT` (default `15` seconds)

The aggregate evaluation score is deliberately not enough to enter Targeted Hunt.

## Telegram commands

- `/person Компания | Имя Фамилия | Должность` — add or enrich a person from the user's own research.
- `/contact PERSON_ID | telegram/email/linkedin/... | value | optional source URL` — attach a professional contact.
- `/note company:NAME | text`, `/note person:ID | text`, `/note vacancy:ID | text` — save intelligence.
- `/intel Компания` — inspect accumulated company intelligence.
- `/entry VACANCY_ID | PERSON_ID | rationale` — manually lock the preferred entry point.
- `/hunt VACANCY_ID` — render the best current entry point and known contacts.
- `/outreach VACANCY_ID | CONTACT_ID | sent/replied/call/interview/final/offer/closed | optional note` — record funnel progress.

The bot never sends an external message on the user's behalf.

## Rollout

The implementation is intentionally stacked:

1. Core intelligence model and eligibility policy.
2. Telegram manual intelligence and overrides.
3. Evidence-based research worker.
4. Human-in-the-loop outreach funnel and hardening.

Keep `TARGETED_HUNT_ENABLED=false` until the stack is merged and smoke-tested on the target Windows installation. Manual intelligence commands do not require automated research to be enabled.

Recommended smoke test after merge:

1. Start the existing Telegram runtime and verify `/health` and `/new` still behave normally.
2. Add a test company/person with `/person`, inspect it with `/intel`, then attach a contact and note.
3. Link that person to a real vacancy with `/entry` and verify `/hunt` renders the manual choice.
4. Record a test outreach state and verify the same attempt advances rather than creating duplicates.
5. Enable `TARGETED_HUNT_ENABLED=true` only after the manual path is healthy; run one bounded research pass with `TARGETED_HUNT_MAX_CASES_PER_RUN=1`.
6. Confirm busy-GPU behavior defers research instead of competing with a game/Ollama workload.

## Test scope

Focused tests cover eligibility, Telegram parsing, evidence-query construction, outreach rendering/statuses, and normalization used by manual intelligence deduplication. Full runtime smoke testing still belongs on the Windows host because the scheduler, Telegram token, browser profile, HH session, Ollama and GPU are host-specific.
