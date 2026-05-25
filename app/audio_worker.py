import os
import asyncio
import tempfile
from pathlib import Path

import httpx
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

async def get_jobs(http):
    r = await http.get(
        f"{SUPABASE_URL}/rest/v1/processing_jobs",
        headers=HEADERS,
        params={
            "job_type": "eq.audio_transcribe",
            "status": "in.(pending,failed)",
            "limit": "20",
            "order": "created_at.asc"
        }
    )
    r.raise_for_status()
    return r.json()

async def update_job(http, job_id, status, result=None, error=None):
    payload = {"status": status}
    if result:
        payload["result"] = result
    if error:
        payload["error"] = error

    r = await http.patch(
        f"{SUPABASE_URL}/rest/v1/processing_jobs?id=eq.{job_id}",
        headers=HEADERS,
        json=payload
    )
    if r.status_code >= 400:
        print("UPDATE_JOB_ERROR", r.status_code, r.text)

async def update_media(http, platform_chat_id, platform_message_id, text):
    r = await http.patch(
        f"{SUPABASE_URL}/rest/v1/media_files"
        f"?platform_chat_id=eq.{platform_chat_id}"
        f"&platform_message_id=eq.{platform_message_id}",
        headers=HEADERS,
        json={
            "download_status": "processed",
            "extraction_status": "processed",
            "extracted_text": text
        }
    )
    if r.status_code >= 400:
        print("UPDATE_MEDIA_ERROR", r.status_code, r.text)

async def transcribe(file_path):
    async with httpx.AsyncClient(timeout=120) as http:
        with open(file_path, "rb") as f:
            files = {
                "file": (Path(file_path).name, f, "audio/ogg"),
                "model": (None, "gpt-4o-mini-transcribe"),
                "language": (None, "ar"),
            }

            r = await http.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                files=files
            )

            if r.status_code >= 400:
                print("OPENAI_TRANSCRIBE_ERROR_BODY", r.text)
            r.raise_for_status()
            return r.json().get("text", "")

async def process_job(client, http, job):
    job_id = job["id"]
    payload = job.get("payload") or {}

    platform_chat_id = payload.get("platform_chat_id")
    platform_message_id = payload.get("platform_message_id")

    if not platform_chat_id or not platform_message_id:
        print("INVALID_JOB_PAYLOAD")
        await update_job(http, job_id, "failed", error="Missing platform ids")
        return

    try:
        await update_job(http, job_id, "processing")

        entity = await client.get_entity(int(platform_chat_id))
        msg = await client.get_messages(entity, ids=int(platform_message_id))

        if not msg:
            raise RuntimeError("Telegram message not found")

        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = await client.download_media(msg, file=tmpdir)

            if not file_path:
                raise RuntimeError("download failed")

            print("DOWNLOADED", file_path)

            if str(file_path).endswith(".oga"):
                new_path = str(file_path)[:-4] + ".ogg"
                Path(file_path).rename(new_path)
                file_path = new_path
                print("RENAMED_TO_OGG", file_path)

            text = await transcribe(file_path)

            print("TRANSCRIBED", text[:200])

            await update_media(http, platform_chat_id, platform_message_id, text)

            await update_job(
                http,
                job_id,
                "processed",
                result={"transcript": text}
            )

    except Exception as e:
        print("TRANSCRIBE_ERROR", str(e))
        await update_job(http, job_id, "failed", error=str(e))

async def main():
    print("AUDIO_WORKER_STARTING", flush=True)

    client = TelegramClient(
        StringSession(os.environ["TELEGRAM_SESSION_STRING"]),
        int(os.environ["TELEGRAM_API_ID"]),
        os.environ["TELEGRAM_API_HASH"]
    )

    print("CONNECTING_TELEGRAM", flush=True)
    await asyncio.wait_for(client.connect(), timeout=30)

    if not await client.is_user_authorized():
        print("SESSION_NOT_AUTHORIZED", flush=True)
        return

    me = await client.get_me()
    print("AUDIO_WORKER_LOGGED_IN", me.id, flush=True)

    async with httpx.AsyncClient(timeout=60) as http:
        while True:
            jobs = await get_jobs(http)

            if not jobs:
                await asyncio.sleep(3)
                continue

            for job in jobs:
                await process_job(client, http, job)

asyncio.run(main())
