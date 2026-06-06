#!/usr/bin/env python3
import pandas as pd
from pathlib import Path

pulse = pd.read_csv("outputs/academic_pulse_v0.csv")
cal = pd.read_csv("outputs/academic_calendar_1447.csv")

cal["start_date"] = pd.to_datetime(cal["start_date"])
cal["end_date"] = pd.to_datetime(cal["end_date"])

rows = []
for _, e in cal.iterrows():
    months = pd.period_range(
        e["start_date"].to_period("M"),
        e["end_date"].to_period("M"),
        freq="M"
    ).astype(str)

    for month in months:
        rows.append({
            "month": month,
            "event_type": e["event_type"],
            "event_name_ar": e["event_name_ar"],
            "event_name_en": e["event_name_en"],
            "semester": e["semester"],
        })

events = pd.DataFrame(rows)

summary = (
    events
    .groupby("month")
    .agg({
        "event_type": lambda x: " | ".join(sorted(set(x))),
        "event_name_ar": lambda x: " | ".join(sorted(set(x))),
        "event_name_en": lambda x: " | ".join(sorted(set(x))),
        "semester": lambda x: " | ".join(sorted(set(x))),
    })
    .reset_index()
)

out = pulse.merge(summary, on="month", how="left")

def explain(row):
    topic = row.get("dominant_topic", "")
    events = str(row.get("event_type", "") or "")

    if topic == "payment" and "tuition_payment" in events:
        return "expected_payment_spike"
    if topic == "registration" and ("course_registration" in events or "early_registration" in events):
        return "expected_registration_spike"
    if topic == "exams" and (
        "midterm_exam" in events
        or "midterm_exam_firstyear" in events
        or "final_exam" in events
        or "final_exam_firstyear" in events
    ):
        return "expected_exam_spike"
    if row.get("pulse_level") in ["critical", "high"] and events and events != "nan":
        return "high_signal_near_calendar_event"
    if row.get("pulse_level") in ["critical", "high"]:
        return "high_signal_without_calendar_match"
    return "normal_or_low_signal"

out["calendar_interpretation"] = out.apply(explain, axis=1)

out_path = Path("outputs/academic_pulse_with_calendar_v2.csv")
out.to_csv(out_path, index=False, encoding="utf-8-sig")

print("Saved:", out_path.resolve())
print()
print(out.sort_values("total_signal", ascending=False).head(25).to_string(index=False))
