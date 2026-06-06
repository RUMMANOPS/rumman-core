#!/usr/bin/env python3
import pandas as pd
from pathlib import Path
import re

CSV_DIR = "/Users/ibrahim../Projects/0-RUMMAN/RUMMAN_Exports/Claude_Uploads"

PATTERNS = {
    "exam_questions": r"question\s+\d+|السؤال|answer\s*:|الجواب\s*:|✅",
    "summaries": r"ملخص|تلخيص|summary",
    "assignments": r"واجب|assignment|اسايمنت",
    "midterm_final": r"فاينل|final|ميد|midterm|اختبار|exam",
    "study_guides": r"مهم|طريقة|شرح|guide|دراسة",
    "official_announcements": r"اعلان|إعلان|يرجى|طلابنا الأعزاء|تعميم",
}

files = sorted(Path(CSV_DIR).glob("RUMMAN_CLAUDE_PART_*.csv"))

counts = {k:0 for k in PATTERNS}
total = 0

for f in files:
    print("Reading", f.name)

    df = pd.read_csv(
        f,
        usecols=["message_text"],
        dtype=str,
        low_memory=False
    )

    texts = df["message_text"].fillna("").astype(str)

    total += len(texts)

    for name, pattern in PATTERNS.items():
        counts[name] += texts.str.contains(
            pattern,
            case=False,
            regex=True,
            na=False
        ).sum()

print("\nTOTAL MESSAGES:", total)
print()

for k,v in sorted(counts.items(), key=lambda x:x[1], reverse=True):
    print(f"{k}: {v:,}")
