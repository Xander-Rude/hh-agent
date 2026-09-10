# Targeted Hunt

Targeted Hunt is a second acquisition channel beside mass application. Its goal is not to automate more clicks; it is to find a credible human entry point into a company and prepare a grounded outreach package.

## Principles

1. Existing apply flow stays independent and keeps running.
2. Expensive research is limited to high-value vacancies.
3. Facts require provenance. LLM reasoning may rank evidence but must not invent people or contacts.
4. User-supplied intelligence is first-class data and may override automated ranking.
5. Public/professional contact data only; no leaked databases or guessed private contact details.
6. Outreach remains human-in-the-loop: the agent prepares; the user sends.

## Data model

- `Company`: reusable company identity.
- `Person`: person associated with a company, with provenance/confidence.
- `Contact`: public contact channel and evidence.
- `Note`: user/agent knowledge attached to company, person, or vacancy.
- `Relationship`: graph edge such as reports_to / works_with / referred_by.
- `IntelligenceSource`: evidence URL/excerpt for a fact.
- `TargetedHuntCase`: one research case per vacancy.
- `EntryPoint`: ranked candidate for getting into a live conversation. A user-locked entry point outranks automation.

## Planned flow

`Evaluation -> eligibility -> TargetedHuntCase -> company research -> people discovery -> identity resolution -> entry-point ranking -> public contact discovery -> outreach draft -> Telegram -> user action -> outcome feedback`

## Configuration

- `TARGETED_HUNT_MIN_SCORE` (default `85`)
- `TARGETED_HUNT_MIN_ROLE_MATCH` (default `80`)
- `TARGETED_HUNT_MIN_SENIORITY_MATCH` (default `80`)
- `TARGETED_HUNT_MAX_RED_FLAGS` (default `0`)

The aggregate evaluation score is deliberately not enough to enter Targeted Hunt.

## Delivery plan

1. Core intelligence model + eligibility policy.
2. Telegram manual intelligence commands and user overrides.
3. Case queue + automated evidence-based research/ranking.
4. Outreach package + feedback/outcome funnel and conversion metrics.

Each stage is delivered as a focused PR with tests. Existing mass apply behavior must remain unchanged until Targeted Hunt is explicitly enabled.
