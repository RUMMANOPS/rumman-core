"""
heartbeat.py — Lightweight worker liveness upsert.

Usage:
    from app.heartbeat import Heartbeat
    hb = Heartbeat(http, worker_id="embed_worker", process="embed", interval_s=30)
    await hb.beat(status="running", metadata={"jobs": 5})

Writes to worker_heartbeats via PostgREST upsert (on_conflict=worker_id).
Silently skips on any error — heartbeat failures must never crash a worker.
"""

import os
import time

import httpx

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

_HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "resolution=merge-duplicates,return=minimal",
}


class Heartbeat:
    def __init__(
        self,
        http: httpx.AsyncClient,
        worker_id: str,
        process: str,
        interval_s: int = 30,
    ):
        self._http       = http
        self._worker_id  = worker_id
        self._process    = process
        self._interval_s = interval_s
        self._last_beat  = 0.0

    async def beat(self, status: str = "running", metadata: dict | None = None, force: bool = False) -> None:
        now = time.monotonic()
        if not force and (now - self._last_beat) < self._interval_s:
            return
        self._last_beat = now
        try:
            await self._http.post(
                f"{SUPABASE_URL}/rest/v1/worker_heartbeats?on_conflict=worker_id",
                headers=_HEADERS,
                json={
                    "worker_id":       self._worker_id,
                    "process":         self._process,
                    "status":          status,
                    "last_beat_at":    "now()",
                    "beat_interval_s": self._interval_s,
                    "metadata":        metadata or {},
                },
                timeout=5,
            )
        except Exception:
            pass
