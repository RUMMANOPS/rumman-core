#!/usr/bin/env python3
import pandas as pd
from pathlib import Path

CSV_DIR = "/Users/ibrahim../Projects/0-RUMMAN/RUMMAN_Exports/Claude_Uploads"

TOPICS = {
    "registration": r"تسجيل|الشعب|شعبة|جدول|بانر|banner|تعارض|الجدول",
    "payment": r"سداد|رسوم|فاتورة|مدى|دفع|قسط|المبلغ|الفاتورة",
    "admission": r"قبول|فرز|مستجد|رقم جامعي|قبولي|انقبلت|انقبل",
    "withdrawal": r"اعتذار|انسحاب|حذف|دروب|drop|حذف ترم",
    "exams": r"اختبار|امتحان|فاينل|final|ميد|midterm|كويز|quiz",
    "blackboard": r"بلاك|بلاكبورد|blackboard|respondus|lockdown",
    "graduation": r"تخرج|وثيقة|الخريج|الخريجين|التخرج",
    "step": r"ستيب|step|كفايات",
    "support_ticket": r"تذكرة|تذكره|دعم|مساعد التسجيل|support",
    "transfer": r"تحويل|تغيير تخصص|تغيير التخصص",
    "equivalency": r"معادلة|يعادل|تعادل|اعفاء|إعفاء",
    "internship": r"تدريب|تعاوني|امتياز",
}

files = sorted(Path(CSV_DIR).glob("RUMMAN_CLAUDE_PART_*.csv"))
total = 0
counts = {k: 0 for k in TOPICS}

for f in files:
    print("Reading", f.name)
    df = pd.read_csv(f, usecols=["message_text"], dtype=str, low_memory=False)
    texts = df["message_text"].fillna("").astype(str)
    total += len(texts)

    for topic, pattern in TOPICS.items():
        counts[topic] += texts.str.contains(pattern, case=False, regex=True, na=False).sum()

print("\nTOTAL MESSAGES:", total)
print()
for k, v in sorted(counts.items(), key=lambda x: x[1], reverse=True):
    print(f"{k}: {v:,}")
