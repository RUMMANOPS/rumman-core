#!/usr/bin/env python3
import pandas as pd
from pathlib import Path

CSV_DIR = "/Users/ibrahim../Projects/0-RUMMAN/RUMMAN_Exports/Claude_Uploads"

TOPICS = {
    "exams": r"اختبار|امتحان|فاينل|final|ميد|midterm|كويز|quiz",
    "registration": r"تسجيل|الشعب|شعبة|جدول|بانر|banner|تعارض",
    "payment": r"سداد|رسوم|فاتورة|مدى|دفع|قسط|المبلغ",
}

frames = []

for f in sorted(Path(CSV_DIR).glob("RUMMAN_CLAUDE_PART_*.csv")):
    print("Reading", f.name)

    df = pd.read_csv(
        f,
        usecols=["message_date", "message_text"],
        dtype=str,
        low_memory=False
    )

    frames.append(df)

df = pd.concat(frames, ignore_index=True)

df["message_date"] = pd.to_datetime(
    df["message_date"],
    errors="coerce"
)

df = df.dropna(subset=["message_date"])

df["month"] = df["message_date"].dt.to_period("M").astype(str)

result = []

for topic, pattern in TOPICS.items():

    mask = df["message_text"].fillna("").str.contains(
        pattern,
        case=False,
        regex=True,
        na=False
    )

    monthly = (
        df.loc[mask]
        .groupby("month")
        .size()
        .reset_index(name="count")
    )

    monthly["topic"] = topic

    result.append(monthly)

out = pd.concat(result)

out.to_csv(
    "outputs/topic_timeline.csv",
    index=False,
    encoding="utf-8-sig"
)

print()
print("Saved:")
print("outputs/topic_timeline.csv")
print()
print(out.sort_values(["topic","month"]).tail(20))
