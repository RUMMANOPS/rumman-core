listener: python3 app/rumman_engine.py
backfill: python3 app/telegram_backfill_worker.py
media: python3 app/telegram_download_worker.py
embed: python3 app/embed_worker.py
search: uvicorn app.search_api:app --host 0.0.0.0 --port ${PORT:-8000}
bot: python3 app/telegram_bot.py
intelligence: python3 app/intelligence_worker.py
attribution: python3 app/attribution_worker.py
watchdog: python3 scripts/watchdog.py
