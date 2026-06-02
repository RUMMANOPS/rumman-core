#!/usr/bin/env python3
"""
export_group_links.py — Export Telegram invite links for all known RUMMAN groups.

Uses TELEGRAM_LISTENER_GHAYTH_SESSION (غيث) to connect and generate an invite link
for each group in the database. Public groups → t.me/username. Private groups →
exported invite link (requires membership; does not require admin).

Output: prints to stdout + saves to data/group_links_YYYYMMDD.txt

Usage:
    python3 scripts/export_group_links.py
"""

import asyncio
import os
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import dotenv_values
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.messages import ExportChatInviteRequest
from telethon.tl.types import Channel, Chat
from telethon.errors import (
    ChatAdminRequiredError, UserNotParticipantError,
    FloodWaitError, ChannelPrivateError
)

env = dotenv_values(dotenv_path=Path(__file__).parent.parent / ".env")

API_ID   = int(env["TELEGRAM_API_ID"])
API_HASH = env["TELEGRAM_API_HASH"]
SESSION  = env["TELEGRAM_LISTENER_GHAYTH_SESSION"]

SUPABASE_URL = env["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = env["SUPABASE_KEY"]
HEADERS = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}

DELAY = 1.2  # seconds between requests — stays well under flood threshold


def canonical_key(cid: int) -> int:
    if cid < 0:
        return abs(cid) - 1_000_000_000_000
    return cid


SKIP_NAMES = {"BotFather", "Ibrahim", "Rumman", "Telegram", "Saudi Electronic University"}


async def fetch_group_ids() -> list[dict]:
    """Pull all known group IDs from Supabase."""
    async with httpx.AsyncClient(timeout=30) as http:
        r1 = await http.get(f"{SUPABASE_URL}/rest/v1/telegram_backfill_jobs",
            headers=HEADERS, params={"select": "chat_name,platform_chat_id", "limit": "500"})
        r2 = await http.get(f"{SUPABASE_URL}/rest/v1/telegram_sync_state",
            headers=HEADERS, params={"select": "chat_name,platform_chat_id", "limit": "500"})

    rows = (r1.json() if r1.status_code == 200 else []) + \
           (r2.json() if r2.status_code == 200 else [])

    by_key: dict[int, dict] = {}
    for row in rows:
        raw_id = row.get("platform_chat_id") or row.get("chat_id")
        name   = (row.get("chat_name") or "").strip()
        if not raw_id or not name:
            continue
        if name in SKIP_NAMES or name.startswith("لمّاح |") or name.startswith("https://"):
            continue
        cid = int(raw_id)
        key = canonical_key(cid)
        existing = by_key.get(key)
        if not existing or cid < 0:
            by_key[key] = {"name": name, "id": cid}

    return sorted(by_key.values(), key=lambda x: x["name"])


async def get_link(client: TelegramClient, group: dict) -> tuple[str, str]:
    """
    Returns (link, status).
    status: 'public' | 'invite' | 'no_access' | 'error:<msg>'
    """
    gid  = group["id"]
    name = group["name"]

    try:
        entity = await client.get_entity(gid)
    except (ChannelPrivateError, ValueError) as e:
        return "", f"no_access: {e}"
    except Exception as e:
        return "", f"error: {e}"

    # Public username → anyone can join directly
    username = getattr(entity, "username", None)
    if username:
        return f"https://t.me/{username}", "public"

    # Private — generate invite link
    try:
        if isinstance(entity, Channel):
            result = await client(ExportChatInviteRequest(peer=entity))
        elif isinstance(entity, Chat):
            result = await client(ExportChatInviteRequest(peer=entity))
        else:
            return "", "unsupported_type"
        return result.link, "invite"
    except ChatAdminRequiredError:
        return "", "needs_admin"
    except UserNotParticipantError:
        return "", "not_a_member"
    except FloodWaitError as e:
        print(f"  ⏳ flood wait {e.seconds}s — sleeping...")
        await asyncio.sleep(e.seconds + 2)
        return await get_link(client, group)  # retry after wait
    except Exception as e:
        return "", f"error: {e}"


async def main():
    print("Fetching group list from DB...")
    groups = await fetch_group_ids()
    print(f"Found {len(groups)} unique groups\n")

    results = []

    async with TelegramClient(StringSession(SESSION), API_ID, API_HASH) as client:
        me = await client.get_me()
        print(f"Connected as: {me.first_name} (@{me.username or me.phone})\n")
        print("─" * 70)

        for i, group in enumerate(groups, 1):
            print(f"[{i:2}/{len(groups)}] {group['name']}", end=" ... ", flush=True)
            link, status = await get_link(client, group)

            if link:
                print(f"✅ {link}")
            else:
                print(f"⚠️  {status}")

            results.append({
                "name":   group["name"],
                "id":     group["id"],
                "link":   link,
                "status": status,
            })

            await asyncio.sleep(DELAY)

    # ── Save to file ──────────────────────────────────────────────────────────
    out_path = Path(__file__).parent.parent / "data" / \
               f"group_links_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"RUMMAN Group Links — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write("=" * 70 + "\n\n")

        public  = [r for r in results if r["status"] == "public"]
        invite  = [r for r in results if r["status"] == "invite"]
        blocked = [r for r in results if r["link"] == ""]

        f.write(f"✅ Public  ({len(public)}) — join directly via link\n")
        f.write(f"🔒 Private ({len(invite)}) — invite link (share with راوي / إبراهيم)\n")
        f.write(f"⚠️  Blocked ({len(blocked)}) — needs admin or not a member\n\n")

        f.write("─" * 70 + "\n\n")

        for section, label in [(public, "PUBLIC"), (invite, "PRIVATE — INVITE LINKS"), (blocked, "BLOCKED")]:
            if not section:
                continue
            f.write(f"[{label}]\n")
            for r in section:
                if r["link"]:
                    f.write(f"{r['name']}\n{r['link']}\n\n")
                else:
                    f.write(f"{r['name']}\n  ⚠️  {r['status']}\n\n")

    print("\n" + "─" * 70)
    print(f"✅ Public  : {len(public)}")
    print(f"🔒 Private : {len(invite)}")
    print(f"⚠️  Blocked : {len(blocked)}")
    print(f"\nSaved → {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
