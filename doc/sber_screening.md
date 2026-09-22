# Sber GigaRecruiter copilot

## Purpose

The module helps Alexander Rudenko pass Sber's automated GigaRecruiter Telegram
screening without treating the bot workflow as a human interview.

The first production mode is intentionally human-in-the-loop:

1. HH Agent has already submitted an application to a Sber vacancy.
2. In the HH Agent Telegram bot run /sber_arm or /sber_arm APPLICATION_ID.
3. Open the unique GigaRecruiter deep link received from Sber and press Start.
4. A local Telethon client, authorized as the user's own Telegram account,
   receives messages only from @Giga_recruiter_bot.
5. Text questions are sent to the local LLM with the exact application,
   vacancy, selected resume title, confirmed evaluation strengths and canonical
   verified candidate facts.
6. The proposed answer is shown in the existing HH Agent bot.
7. Nothing is sent to GigaRecruiter until the owner confirms the answer or uses
   /sber_answer TURN_ID | text.
8. Inline choices from GigaRecruiter are mirrored into HH Agent and also
   require explicit confirmation.

## Security model

- Telegram user authorization is a local Telethon session under data/secrets/.
- Sber screening state and Q/A history are stored in a separate local SQLite
  file under data/secrets/.
- Neither file is part of the main hh_agent.db observability snapshot.
- Deep-link start tokens are not required by the worker and are not stored.
  The user opens the deep link manually in the first release.
- Sber screening commands in the HH Agent bot are owner-only and require
  TELEGRAM_CHAT_ID.
- Auto-send is disabled in the first release.

## One-time setup

Telegram requires an API ID and API hash for an MTProto user client.

1. Create/get credentials at https://my.telegram.org/apps
2. Add the following values to C:\hh-agent\.env:

    TELEGRAM_API_ID=123456
    TELEGRAM_API_HASH=...

3. Run PowerShell interactively:

    cd C:\hh-agent
    .\setup_sber_screening.ps1

4. Telethon will request the Telegram phone/code and, if enabled, the account
   2FA password. The resulting authorization key remains local.

The setup script installs Telethon, authorizes the local user session, enables
HH_SBER_SCREENING_ENABLED=true, creates the hidden scheduled task
HH Agent - Sber Screening, and starts it.

## Commands

- /sber_arm
  Arms the latest confirmed Sber application.
- /sber_arm 1752
  Arms a specific application.
- /sber_status
  Shows the active screening and pending confirmations.
- /sber_answer TURN_ID | text
  Approves a custom answer for the pending GigaRecruiter question.

## Fail-closed behavior

The LLM prompt explicitly forbids inventing experience, employers, dates,
technologies, metrics or personal circumstances. If confirmed facts are
insufficient, it must return needs_user. In that case the agent does not offer
an automatic send button and asks for a manual answer.
