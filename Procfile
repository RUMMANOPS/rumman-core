listener: python3 app/rumman_engine.py
audio: python3 app/audio_worker.py
backfill: python3 app/telegram_backfill_worker.py
media: python3 app/media_worker.py
embed: python3 app/embed_worker.py
search: uvicorn app.search_api:app --host 0.0.0.0 --port ${PORT:-8000}
