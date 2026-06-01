# RUMMAN Core v1

RUMMAN Core v1 (نواة رمان الأولى) is a Telegram ingestion and operational intelligence system.

## Services (الخدمات)

### listener (مستقبل الرسائل)

python3 rumman_engine.py

### audio worker (عامل تفريغ الصوت)

python3 audio_worker.py

## Captured content (المحتوى المدعوم)

- Text messages (الرسائل النصية)
- Edited messages (تعديل الرسائل)
- Photos (الصور)
- Files and PDFs (الملفات وملفات PDF)
- Voice and audio (الصوتيات)
- GIFs (الصور المتحركة)
- Polls (التصويتات)

## Data storage (التخزين)

Data is stored in Supabase:
- messages
- media_files
- processing_jobs

## Environment variables (متغيرات البيئة)

TELEGRAM_API_ID=
TELEGRAM_API_HASH=
TELEGRAM_LISTENER_GHAYTH_SESSION=
TELEGRAM_BACKFILL_RAWI_SESSION=
TELEGRAM_MEDIA_IBRAHIM_SESSION=
SUPABASE_URL=
SUPABASE_KEY=
OPENAI_API_KEY=

## Do not commit (لا ترفع هذه الملفات)

- .env
- *.session
- logs/
- downloads/
