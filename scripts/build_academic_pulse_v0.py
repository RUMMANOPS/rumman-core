#!/usr/bin/env python3
import pandas as pd
from pathlib import Path

timeline = pd.read_csv("outputs/topic_timeline.csv")

pivot = (
    timeline
    .pivot_table(index="month", columns="topic", values="count", fill_value=0)
    .reset_index()
)

for col in ["exams", "registration", "payment"]:
    if col not in pivot.columns:
        pivot[col] = 0

pivot["total_signal"] = pivot["exams"] + pivot["registration"] + pivot["payment"]

def dominant(row):
    vals = {
        "exams": row["exams"],
        "registration": row["registration"],
        "payment": row["payment"],
    }
    return max(vals, key=vals.get)

pivot["dominant_topic"] = pivot.apply(dominant, axis=1)

pivot["pulse_level"] = pd.cut(
    pivot["total_signal"],
    bins=[-1, 100, 500, 2000, 10000, 999999999],
    labels=["quiet", "low", "medium", "high", "critical"]
)

out = Path("outputs/academic_pulse_v0.csv")
pivot.to_csv(out, index=False, encoding="utf-8-sig")

print("Saved:", out.resolve())
print()
print(pivot.sort_values("total_signal", ascending=False).head(20).to_string(index=False))
