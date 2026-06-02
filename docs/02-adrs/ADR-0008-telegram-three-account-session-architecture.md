# ADR-0008: Telegram Three-Account Session Architecture

## Status

Accepted

## Context

RUMMAN uses Telethon user clients (not bots) to interact with Telegram, because user accounts can:
- Join groups without admin permission
- Read full message history via `iter_messages`
- Download all media types

Telethon uses a `StringSession` to authenticate and maintain a persistent connection. A StringSession encodes the cryptographic authentication state for one Telegram account.

**The problem:** Telegram's MTProto protocol enforces that each account may have only one active authenticated connection per device. If two processes attempt to use the same StringSession simultaneously, Telegram immediately raises `AuthKeyDuplicatedError` and terminates both connections.

Early RUMMAN versions ran the live listener and the backfill worker on the same account. This caused cascading session conflicts: any time both processes were active, one or both would crash. Adding the media download worker (audio, images, PDFs) to the same account multiplied the collisions.

The failure mode was not intermittent — it was deterministic. Two concurrent processes = certain crash.

## Decision

RUMMAN maintains **three dedicated Telegram user accounts**, one per concurrent worker type. Each account holds exactly one StringSession, stored in a Railway environment variable. No two running processes share a session.

| Account | Identity | Session Variable | Used by |
|---|---|---|---|
| غيث | +966582282200 | `TELEGRAM_LISTENER_GHAYTH_SESSION` | `listener` (`app/rumman_engine.py`) only |
| راوي | +966590111167 | `TELEGRAM_BACKFILL_RAWI_SESSION` | `backfill` (`app/telegram_backfill_worker.py`) only |
| إبراهيم | +966560064766 | `TELEGRAM_MEDIA_IBRAHIM_SESSION` | `media` (`app/telegram_download_worker.py`) only |

**Rule:** If a new worker requires Telegram API access, it requires a new dedicated account. Never add a second process to an existing account.

**Rule:** `audio_worker.py` (formerly a separate process) was unified into `telegram_download_worker.py` specifically because both needed Telegram API access. Running them separately on the إبراهيم account caused `AuthKeyDuplicatedError`. The unified `media` process handles both `audio_transcribe` and `telegram_media` job types sequentially, eliminating the conflict.

## Session String Generation

Session strings are generated via `auth_session.py` (gitignored, never deployed). This file runs locally, performs an interactive Telegram login for a given account, and outputs the StringSession string. That string is then stored in Railway environment variables.

`auth_session.py` must never be committed to git. It contains login logic that would allow anyone with the repo to generate authenticated sessions.

## Consequences

### Positive

- `AuthKeyDuplicatedError` is eliminated — each account has exactly one active process
- Worker types are independently scalable and independently restartable
- Session expiry for one account does not affect others
- Clear operational responsibility: each account = one functional domain
- New worker types that require Telegram access have a clear pattern to follow

### Negative

- Three Telegram accounts must be maintained (verified phone numbers, periodic check-ins to avoid account suspension)
- Session string rotation requires local `auth_session.py` run and Railway env var update
- Adding a new Telegram-dependent worker requires procuring a new phone number

## Explicitly Avoided Approaches

**Bot API instead of user clients:** Telegram bots cannot join groups without admin add, cannot read message history, and cannot download all media. The core use case (ingesting community Telegram groups that RUMMAN was not invited to) requires user accounts.

**One account, serialized access:** Serializing all Telegram API calls through one account via a shared queue would eliminate session conflicts but creates a bottleneck: media downloads (which can take 30+ seconds per file) would block live message ingestion.
