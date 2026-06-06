#!/usr/bin/env python3
import argparse
import re
from pathlib import Path
import pandas as pd

QUESTION_RE = re.compile(
    r"(؟|\?|كيف|وش|وين|متى|هل|اقدر|أقدر|ليش|ليه|مين|كم|ماهي|ما هو|عندي|ابي|أبي|ابغى|أبغى|ما يفتح|ماطلع|ما طلع|ساعدوني|احد يعرف|أحد يعرف)",
    re.I
)

TOPIC_RULES = [
    ("registration", r"تسجيل|الشعب|شعبة|جدول|بانر|banner|تعارض"),
    ("payment", r"سداد|رسوم|فاتورة|مدى|دفع|قسط|المبلغ"),
    ("admission", r"قبول|فرز|مستجد|رقم جامعي|قبولي"),
    ("withdrawal", r"اعتذار|انسحاب|حذف|دروب|drop"),
    ("exams", r"اختبار|امتحان|فاينل|ميد|كويز|تجميع|اسئلة|أسئلة"),
    ("blackboard", r"بلاك|بلاكبورد|blackboard|respondus|lockdown"),
    ("graduation", r"تخرج|وثيقة|الخريج|الخريجين"),
    ("step", r"ستيب|step|كفايات"),
    ("course", r"مادة|مواد|مقرر|كورس|سلايد|ملخص"),
]

NOISE_RE = re.compile(r"^(تم|طيب|اوكي|ok|👍|🙏|شكرا|شكرًا|.)$", re.I)

def norm_id(x):
    if pd.isna(x):
        return None
    s = str(x).strip()
    if not s or s.lower() == "nan":
        return None
    if s.endswith(".0"):
        s = s[:-2]
    return s

def topic_of(text):
    text = text or ""
    for topic, pat in TOPIC_RULES:
        if re.search(pat, text, re.I):
            return topic
    return "other"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv-dir", required=True)
    ap.add_argument("--limit", type=int, default=20000)
    args = ap.parse_args()

    csv_dir = Path(args.csv_dir).expanduser()
    files = sorted(csv_dir.glob("RUMMAN_CLAUDE_PART_*.csv"))
    if not files:
        raise SystemExit(f"No files found in {csv_dir}")

    usecols = [
        "message_date",
        "platform_chat_id",
        "chat_name",
        "platform_message_id",
        "reply_to_message_id",
        "sender_name",
        "message_text",
        "has_media",
        "media_type",
        "message_type",
    ]

    frames = []
    print(f"Loading {len(files)} files...")
    for f in files:
        print(" -", f.name)
        frames.append(pd.read_csv(f, dtype=str, usecols=usecols, low_memory=False))

    df = pd.concat(frames, ignore_index=True)

    df["_msg_id"] = df["platform_message_id"].map(norm_id)
    df["_reply_id"] = df["reply_to_message_id"].map(norm_id)
    df["_text"] = df["message_text"].fillna("").astype(str).str.strip()
    df["_chat"] = df["platform_chat_id"].fillna("").astype(str)
    df["_sender"] = df["sender_name"].fillna("").astype(str)
    df["_date"] = df["message_date"].fillna("").astype(str)

    id_to_idx = {mid: i for i, mid in enumerate(df["_msg_id"]) if mid}

    replies = df[df["_reply_id"].notna()].copy()
    replies = replies[replies["_reply_id"].isin(id_to_idx)]

    children = {}
    for i, row in replies.iterrows():
        children.setdefault(row["_reply_id"], []).append(i)

    rows = []
    for root_id, kid_idxs in children.items():
        root_i = id_to_idx.get(root_id)
        if root_i is None:
            continue

        q = df.at[root_i, "_text"]
        if not q or len(q) < 8:
            continue
        if not QUESTION_RE.search(q):
            continue

        answers = []
        answerers = []
        for ki in kid_idxs[:50]:
            txt = df.at[ki, "_text"]
            if not txt or len(txt) < 3:
                continue
            if NOISE_RE.match(txt):
                continue
            answers.append(txt)
            sender = df.at[ki, "_sender"]
            if sender:
                answerers.append(sender)

        if not answers:
            continue

        combined = q + "\n" + "\n".join(answers[:10])
        rows.append({
            "question_message_id": root_id,
            "chat_id": df.at[root_i, "_chat"],
            "chat_name": df.at[root_i, "chat_name"],
            "date": df.at[root_i, "_date"],
            "question_sender": df.at[root_i, "_sender"],
            "question_text": q,
            "topic": topic_of(combined),
            "reply_count_total": len(kid_idxs),
            "answer_count_clean": len(answers),
            "unique_answerers": len(set(answerers)),
            "answerers_sample": " | ".join(list(dict.fromkeys(answerers))[:10]),
            "answers_sample": "\n---\n".join(answers[:10]),
        })

        if len(rows) >= args.limit:
            break

    out = Path("outputs/qa_graph_v0.csv")
    pd.DataFrame(rows).to_csv(out, index=False, encoding="utf-8-sig")

    print("\nInput rows:", len(df))
    print("Valid replies:", len(replies))
    print("Q→A rows:", len(rows))
    print("Output:", out.resolve())

    if rows:
        print("\nTop topics:")
        print(pd.DataFrame(rows)["topic"].value_counts().head(20).to_string())

if __name__ == "__main__":
    main()
