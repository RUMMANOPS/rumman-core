#!/usr/bin/env python3
import argparse
import re
from pathlib import Path
import pandas as pd

QUESTION_RE = re.compile(r"(؟|\?|كيف|وش|وين|متى|هل|اقدر|أقدر|ليش|ليه|مين|كم|ماهي|ما هو|عندي مشكلة|ما يفتح|ماطلع|ما طلع|ساعدوني)", re.I)
SOLUTION_RE = re.compile(r"(شكرا|شكرًا|يعطيك العافيه|يعطيك العافية|ضبط|زبط|انحلت|تمام|حليتها|لقيتها|نجح|مشي|الحمدلله|الله يجزاك|جزاك الله)", re.I)
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

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv-dir", required=True, help="Folder containing RUMMAN_CLAUDE_PART_*.csv")
    ap.add_argument("--limit", type=int, default=500)
    args = ap.parse_args()

    csv_dir = Path(args.csv_dir).expanduser()
    files = sorted(csv_dir.glob("RUMMAN_CLAUDE_PART_*.csv"))
    if not files:
        raise SystemExit(f"No RUMMAN_CLAUDE_PART_*.csv files found in: {csv_dir}")

    print(f"Loading {len(files)} files...")
    frames = []
    for f in files:
        print(" -", f.name)
        frames.append(pd.read_csv(f, dtype=str, low_memory=False))

    df = pd.concat(frames, ignore_index=True)
    print("\nRows:", len(df))
    print("Columns:", list(df.columns))

    # Guess columns
    msg_id_col = "platform_message_id" if "platform_message_id" in df.columns else None
    reply_col = "reply_to_message_id" if "reply_to_message_id" in df.columns else None
    text_col = "text" if "text" in df.columns else ("message_text" if "message_text" in df.columns else None)
    chat_col = "chat_id" if "chat_id" in df.columns else ("channel_id" if "channel_id" in df.columns else None)
    date_col = "date" if "date" in df.columns else ("created_at" if "created_at" in df.columns else None)
    sender_col = "sender_name" if "sender_name" in df.columns else ("from_name" if "from_name" in df.columns else None)

    missing = [name for name, col in {
        "platform_message_id": msg_id_col,
        "reply_to_message_id": reply_col,
        "text/message_text": text_col,
    }.items() if col is None]
    if missing:
        raise SystemExit(f"Missing required columns: {missing}")

    df["_msg_id"] = df[msg_id_col].map(norm_id)
    df["_reply_id"] = df[reply_col].map(norm_id)
    df["_text"] = df[text_col].fillna("").astype(str).str.strip()
    df["_chat"] = df[chat_col].fillna("").astype(str) if chat_col else ""
    df["_date"] = df[date_col].fillna("").astype(str) if date_col else ""
    df["_sender"] = df[sender_col].fillna("").astype(str) if sender_col else ""

    id_to_idx = {mid: i for i, mid in enumerate(df["_msg_id"]) if mid}
    replies = df[df["_reply_id"].notna()].copy()
    replies["_parent_exists"] = replies["_reply_id"].isin(id_to_idx)

    print("\nReply stats")
    print("Replies:", len(replies))
    print("Replies with parent:", int(replies["_parent_exists"].sum()))
    print("Parent match rate:", round(float(replies["_parent_exists"].mean() * 100), 2), "%")

    children = {}
    for i, row in replies[replies["_parent_exists"]].iterrows():
        children.setdefault(row["_reply_id"], []).append(i)

    roots = []
    for mid, kids in children.items():
        root_i = id_to_idx.get(mid)
        if root_i is None:
            continue
        root_text = df.at[root_i, "_text"]
        if len(root_text) < 8:
            continue
        if not QUESTION_RE.search(root_text):
            continue

        thread_rows = [root_i] + kids[:20]
        thread_texts = [df.at[j, "_text"] for j in thread_rows]
        combined = " || ".join(thread_texts)

        if not SOLUTION_RE.search(combined):
            continue

        non_noise_replies = [
            t for t in thread_texts[1:]
            if len(t) >= 8 and not NOISE_RE.match(t)
        ]

        if not non_noise_replies:
            continue

        roots.append({
            "root_message_id": df.at[root_i, "_msg_id"],
            "chat_id": df.at[root_i, "_chat"],
            "date": df.at[root_i, "_date"],
            "sender": df.at[root_i, "_sender"],
            "root_question": root_text,
            "reply_count": len(kids),
            "non_noise_reply_count": len(non_noise_replies),
            "solution_signal": "yes",
            "sample_replies": "\n---\n".join(non_noise_replies[:8]),
            "thread_preview": "\n---\n".join(thread_texts[:12]),
        })

        if len(roots) >= args.limit:
            break

    out = Path("outputs/solution_threads_sample.csv")
    pd.DataFrame(roots).to_csv(out, index=False, encoding="utf-8-sig")

    print("\nCandidate solution threads:", len(roots))
    print("Output:", out.resolve())

if __name__ == "__main__":
    main()
