import os
import json
import asyncio
from datetime import timezone

import httpx
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import Chat, Channel
from telethon.errors import AuthKeyDuplicatedError

load_dotenv()

ENABLE_BACKFILL = False

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
SEU_TENANT_ID = "00000000-0000-0000-0000-000000000001"

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}

# college_id lookup: populated at startup from inst_colleges.telegram_chat_ids
_COLLEGE_BY_CHAT: dict[str, str] = {}

# backfill registry: normalised bare channel IDs that already have a backfill job.
# Stored as normalised positive-integer strings to prevent -100-prefix duplicates.
_BACKFILL_REGISTERED: set[str] = set()


def _norm_chat_id(chat_id: str | int) -> str:
    """Normalise a Telegram chat ID to a bare positive integer string.
    Strips the -100 MTProto prefix from full channel IDs so that
    1929233838 and -1001929233838 map to the same key."""
    n = int(chat_id)
    if n < 0:
        s = str(abs(n))
        if s.startswith("100") and len(s) > 4:
            return s[3:]
    return str(abs(n))


async def load_college_chat_map(http: httpx.AsyncClient) -> None:
    r = await http.get(
        f"{SUPABASE_URL}/rest/v1/inst_colleges",
        headers=HEADERS,
        params={"select": "id,telegram_chat_ids"},
    )
    if r.status_code >= 400:
        print(f"COLLEGE_MAP_LOAD_ERROR | status={r.status_code}", flush=True)
        return
    for row in r.json():
        for cid in (row.get("telegram_chat_ids") or []):
            _COLLEGE_BY_CHAT[str(cid)] = row["id"]
    print(f"COLLEGE_MAP_LOADED | chats={len(_COLLEGE_BY_CHAT)}", flush=True)


async def load_backfill_registry(http: httpx.AsyncClient) -> None:
    """Load normalised chat IDs for all existing backfill jobs into the in-memory registry."""
    r = await http.get(
        f"{SUPABASE_URL}/rest/v1/telegram_backfill_jobs",
        headers=HEADERS,
        params={"select": "platform_chat_id", "limit": "5000"},
    )
    if r.status_code >= 400:
        print(f"BACKFILL_REGISTRY_LOAD_ERROR | status={r.status_code}", flush=True)
        return
    for row in r.json():
        try:
            _BACKFILL_REGISTERED.add(_norm_chat_id(row["platform_chat_id"]))
        except (ValueError, TypeError):
            pass
    print(f"BACKFILL_REGISTRY_LOADED | known_chats={len(_BACKFILL_REGISTERED)}", flush=True)


async def ensure_backfill_job(
    http: httpx.AsyncClient,
    platform_chat_id: str,
    chat_name: str,
    chat_type: str,
) -> None:
    """Create a pending backfill job for this chat if one doesn't already exist.
    Uses normalised IDs so -100XXXXXXXXXX and XXXXXXXXXX don't generate duplicate jobs."""
    try:
        norm = _norm_chat_id(platform_chat_id)
    except (ValueError, TypeError):
        return
    if norm in _BACKFILL_REGISTERED:
        return
    r = await http.post(
        f"{SUPABASE_URL}/rest/v1/telegram_backfill_jobs",
        headers=HEADERS,
        json={
            "platform_chat_id": platform_chat_id,
            "chat_name": chat_name,
            "chat_type": chat_type,
            "status": "pending",
            "batch_size": 500,
            "retry_count": 0,
        },
    )
    if r.status_code in (200, 201):
        _BACKFILL_REGISTERED.add(norm)
        print(f"BACKFILL_AUTO_CREATED | chat={chat_name} | id={platform_chat_id}", flush=True)
    elif r.status_code == 409:
        _BACKFILL_REGISTERED.add(norm)
    else:
        print(f"BACKFILL_CREATE_ERROR | chat={chat_name} | status={r.status_code}", flush=True)


async def discover_and_register_groups(client: TelegramClient, http: httpx.AsyncClient) -> None:
    """
    Enumerate all Telegram dialogs the collector account is in and create pending
    backfill jobs for any group not already tracked. Called once at startup.

    This ensures that groups joined while the listener was offline are automatically
    queued for historical ingestion on next restart. Does NOT fetch messages (not
    a historical crawl — only enumerates the dialog list).
    """
    count_new = 0
    count_known = 0

    async for dialog in client.iter_dialogs():
        entity = dialog.entity

        if not isinstance(entity, (Chat, Channel)):
            continue  # skip private DMs

        chat_id = str(dialog.id)
        chat_name = getattr(entity, "title", None) or f"Chat {chat_id}"

        if isinstance(entity, Channel) and entity.broadcast:
            chat_type = "channel"
        else:
            chat_type = "group"

        try:
            norm = _norm_chat_id(chat_id)
        except (ValueError, TypeError):
            continue

        if norm in _BACKFILL_REGISTERED:
            count_known += 1
            continue

        await ensure_backfill_job(http, chat_id, chat_name, chat_type)
        count_new += 1

    print(
        f"GROUP_DISCOVERY_DONE | new_jobs={count_new} | already_known={count_known}",
        flush=True,
    )


def message_type(msg):
    if getattr(msg, "poll", None):
        return "poll"
    if msg.gif:
        return "gif"
    if msg.photo:
        return "photo"
    if msg.video:
        return "video"
    if msg.voice:
        return "voice"
    if msg.audio:
        return "audio"
    if msg.document:
        return "file"
    if msg.message:
        return "text"
    return "other"


def file_meta(msg):
    f = getattr(msg, "file", None)
    return {
        "file_name": getattr(f, "name", None),
        "mime_type": getattr(f, "mime_type", None),
        "size_bytes": getattr(f, "size", None),
    }


async def post(http, table, payload):
    r = await http.post(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers=HEADERS,
        json=payload,
    )

    if r.status_code == 409:
        return "duplicate"

    if r.status_code >= 400:
        print(f"{table.upper()}_ERROR", r.status_code, r.text)
        return "error"

    return "inserted"


async def get_current_sync_state(http, platform_chat_id):
    r = await http.get(
        f"{SUPABASE_URL}/rest/v1/telegram_sync_state",
        headers=HEADERS,
        params={
            "platform_chat_id": f"eq.{platform_chat_id}",
            "select": "total_messages_seen,oldest_message_id,newest_message_id",
            "limit": "1",
        },
    )

    if r.status_code >= 400:
        print("SYNC_STATE_READ_ERROR", r.status_code, r.text)
        return None

    rows = r.json()
    return rows[0] if rows else None


async def update_sync_state(http, payload):
    current = await get_current_sync_state(http, payload["platform_chat_id"])

    current_total = 0
    current_oldest = None

    if current:
        current_total = current.get("total_messages_seen") or 0
        current_oldest = current.get("oldest_message_id")

    message_id = int(payload["platform_message_id"])

    sync_payload = {
        "platform_chat_id": payload["platform_chat_id"],
        "chat_type": payload["telegram_chat_type"],
        "chat_name": payload["chat_name"],
        "backfill_completed": False,
        "oldest_message_id": current_oldest,
        "newest_message_id": message_id,
        "total_messages_seen": current_total + 1,
    }

    r = await http.post(
        f"{SUPABASE_URL}/rest/v1/telegram_sync_state",
        headers={
            **HEADERS,
            "Prefer": "resolution=merge-duplicates,return=representation",
        },
        params={
            "on_conflict": "platform_chat_id",
        },
        json=sync_payload,
    )

    if r.status_code >= 400:
        print("SYNC_STATE_ERROR", r.status_code, r.text)


async def build_payload(msg, chat_name, chat_type, chat_id, source):
    sender = await msg.get_sender()

    mt = message_type(msg)
    fm = file_meta(msg)

    return {
        "platform": "telegram_user_client",
        "platform_message_id": str(msg.id),
        "platform_chat_id": str(chat_id),
        "telegram_chat_type": chat_type,
        "chat_name": chat_name,
        "platform_user_id": str(getattr(sender, "id", "")) if sender else None,
        "platform_username": getattr(sender, "username", None) if sender else None,
        "platform_user_first_name": getattr(sender, "first_name", None) if sender else None,
        "sender_name": (
            " ".join(
                filter(
                    None,
                    [
                        getattr(sender, "first_name", None),
                        getattr(sender, "last_name", None),
                    ],
                )
            )
            if sender
            else None
        ),
        "message_text": msg.message or "",
        "message_type": mt,
        "message_date": msg.date.astimezone(timezone.utc).isoformat() if msg.date else None,
        "has_media": mt not in ["text", "poll", "other"],
        "media_type": mt if mt not in ["text", "poll", "other"] else None,
        "file_name": fm["file_name"],
        "mime_type": fm["mime_type"],
        "reply_to_message_id": str(msg.reply_to_msg_id) if msg.reply_to_msg_id else None,
        "edited_at": msg.edit_date.astimezone(timezone.utc).isoformat() if msg.edit_date else None,
        "raw_json": json.loads(json.dumps(msg.to_dict(), default=str)),
        "tenant_id": SEU_TENANT_ID,
        "metadata": {
            "source": source,
            "size_bytes": fm["size_bytes"],
            "is_forward": bool(getattr(msg, "fwd_from", None)),
            "college_id": _COLLEGE_BY_CHAT.get(str(chat_id)),
        },
    }


async def process_message(http, msg, chat_name, chat_type, chat_id, source):
    payload = await build_payload(msg, chat_name, chat_type, chat_id, source)

    result = await post(http, "messages", payload)

    print(
        f"{result.upper()} | "
        f"{chat_name} | "
        f"{payload['message_type']} | "
        f"{payload['platform_message_id']}"
    )

    if result == "inserted":
        await update_sync_state(http, payload)
        # Auto-create backfill job on first message from any previously unknown chat
        await ensure_backfill_job(http, str(chat_id), chat_name, chat_type)


async def historical_backfill(client):
    print("\nBACKFILL DISABLED\n")


async def main():
    print("\nRUMMAN ENGINE STARTING...\n")

    client = TelegramClient(
        StringSession(os.environ["TELEGRAM_SESSION_STRING"]),
        int(os.environ["TELEGRAM_API_ID"]),
        os.environ["TELEGRAM_API_HASH"],
    )

    try:
        await client.start()

        me = await client.get_me()

        print(f"LOGGED_IN: {me.id}")

        async with httpx.AsyncClient(timeout=30) as http:
            await load_backfill_registry(http)
            await load_college_chat_map(http)
            await discover_and_register_groups(client, http)

            @client.on(events.NewMessage)
            async def new_message_handler(event):
                try:
                    chat = await event.get_chat()

                    chat_name = (
                        getattr(chat, "title", None)
                        or getattr(chat, "first_name", None)
                        or "Unknown"
                    )

                    chat_type = (
                        "private"
                        if event.is_private
                        else "channel"
                        if event.is_channel
                        else "group"
                    )

                    await process_message(
                        http=http,
                        msg=event.message,
                        chat_name=chat_name,
                        chat_type=chat_type,
                        chat_id=event.chat_id,
                        source="live",
                    )

                except Exception as e:
                    print("LIVE_ERROR", str(e))

            if ENABLE_BACKFILL:
                await historical_backfill(client)
            else:
                print("\nHISTORICAL BACKFILL DISABLED\n")

            print("\nLIVE LISTENER ACTIVE\n")

            await client.run_until_disconnected()
    finally:
        # Ensure the auth key is released on Telegram's side before any retry.
        try:
            await client.disconnect()
        except Exception:
            pass


async def _run_with_retry():
    """Outer retry loop: handles AuthKeyDuplicatedError from rolling restarts."""
    RETRY_DELAY = 180  # seconds — 3 min gives Telegram time to release the auth key
    while True:
        try:
            await main()
            break  # clean exit
        except AuthKeyDuplicatedError:
            print(f"AUTH_KEY_DUPLICATED | retrying in {RETRY_DELAY}s", flush=True)
            await asyncio.sleep(RETRY_DELAY)
        except Exception as e:
            print(f"FATAL_ERROR | {e}", flush=True)
            raise

asyncio.run(_run_with_retry())
