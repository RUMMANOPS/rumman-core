import os
import asyncio
import tempfile
from pathlib import Path

import httpx
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import PeerChannel, PeerChat

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

MAX_RETRIES = 5  # jobs with retry_count >= this are abandoned (never picked up again)

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

async def get_jobs(http):
    # retry_count filter requires processing_jobs.retry_count column (int, default 0)
    r = await http.get(
        f"{SUPABASE_URL}/rest/v1/processing_jobs",
        headers=HEADERS,
        params={
            "job_type": "eq.audio_transcribe",
            "status": "in.(pending,failed)",
            "retry_count": f"lt.{MAX_RETRIES}",
            "limit": "20",
            "order": "created_at.asc",
        }
    )
    r.raise_for_status()
    return r.json()

async def update_job(http, job_id, status, result=None, error=None, retry_count=None):
    payload = {"status": status}
    if result is not None:
        payload["result"] = result
    if error is not None:
        payload["error"] = error
    if retry_count is not None:
        payload["retry_count"] = retry_count

    r = await http.patch(
        f"{SUPABASE_URL}/rest/v1/processing_jobs?id=eq.{job_id}",
        headers=HEADERS,
        json=payload
    )
    if r.status_code >= 400:
        print(f"UPDATE_JOB_ERROR | id={job_id} | status={r.status_code} | body={r.text[:120]}", flush=True)

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
    retry_count = (job.get("retry_count") or 0)

    platform_chat_id = payload.get("platform_chat_id")
    platform_message_id = payload.get("platform_message_id")

    if not platform_chat_id or not platform_message_id:
        print(f"INVALID_JOB_PAYLOAD | id={job_id}", flush=True)
        await update_job(http, job_id, "failed", error="Missing platform ids",
                         retry_count=retry_count + 1)
        return

    print(f"JOB_START | id={job_id} | chat_id={platform_chat_id} | msg_id={platform_message_id} | attempt={retry_count + 1}", flush=True)

    try:
        await update_job(http, job_id, "processing")

        chat_type = payload.get("chat_type", "")
        cid = int(platform_chat_id)
        if chat_type in ("channel", "megagroup"):
            entity = await client.get_entity(PeerChannel(cid))
        elif chat_type == "group":
            entity = await client.get_entity(PeerChat(cid))
        else:
            try:
                entity = await client.get_entity(cid)
            except Exception:
                entity = await client.get_entity(PeerChannel(cid))
        msg = await client.get_messages(entity, ids=int(platform_message_id))

        if not msg:
            raise RuntimeError("Telegram message not found")

        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = await client.download_media(msg, file=tmpdir)

            if not file_path:
                raise RuntimeError("download failed")

            print(f"DOWNLOADED | id={job_id} | path={file_path}", flush=True)

            if str(file_path).endswith(".oga"):
                new_path = str(file_path)[:-4] + ".ogg"
                Path(file_path).rename(new_path)
                file_path = new_path

            text = await transcribe(file_path)

            print(f"TRANSCRIBED | id={job_id} | chars={len(text)}", flush=True)

            await update_media(http, platform_chat_id, platform_message_id, text)

            await update_job(
                http,
                job_id,
                "processed",
                result={"transcript": text, "char_count": len(text)},
            )

            print(f"JOB_DONE | id={job_id} | chat_id={platform_chat_id} | msg_id={platform_message_id}", flush=True)

    except Exception as e:
        new_retry_count = retry_count + 1
        abandoned = new_retry_count >= MAX_RETRIES
        print(
            f"JOB_FAILED | id={job_id} | attempt={new_retry_count} | abandoned={abandoned} | error={str(e)[:120]}",
            flush=True,
        )
        await update_job(http, job_id, "failed", error=str(e), retry_count=new_retry_count)

async def main():
    # audio_transcribe jobs are handled by telegram_download_worker.py (unified media handler).
    # Running this worker simultaneously would conflict on TELEGRAM_SESSION_STRING and race
    # on the same job queue. Sleep indefinitely — the process must stay alive for Railway.
    print(
        "AUDIO_WORKER_STANDBY | audio_transcribe handled by telegram_download_worker.py | sleeping",
        flush=True,
    )
    while True:
        await asyncio.sleep(86400)

asyncio.run(main())
