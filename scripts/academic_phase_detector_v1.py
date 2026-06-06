#!/usr/bin/env python3
import argparse
import pandas as pd
from pathlib import Path
from datetime import timedelta

CALENDAR_PATH = "outputs/academic_calendar_1447.csv"

PHASE_EXPECTATIONS = {
    "course_registration": ["التسجيل", "الشعب", "الجدول", "التعارض", "البانر"],
    "early_registration": ["التسجيل المبكر", "الشعب", "الجدول"],
    "tuition_payment": ["السداد", "الرسوم", "الفاتورة", "مشاكل الدفع"],
    "semester_start": ["بداية الدراسة", "المواد", "البلاكبورد", "الجداول"],
    "midterm_exam": ["الميدترم", "الاختبارات", "تجميعات الميد", "Respondus"],
    "midterm_exam_firstyear": ["اختبارات السنة الأولى", "الميدترم", "تجميعات الميد", "Respondus"],
    "final_exam": ["الفاينل", "المراجعات النهائية", "تجميعات الفاينل", "الجداول", "Respondus"],
    "final_exam_firstyear": ["فاينل السنة الأولى", "المراجعات النهائية", "تجميعات الفاينل", "Respondus"],
    "withdrawal_deadline": ["الانسحاب", "الاعتذار", "الحذف", "أثره على المعدل"],
    "exam_appeal_deadline": ["الاعتراض", "الدرجات", "مراجعة النتائج"],
    "holiday_eid_fitr": ["الإجازة", "توقف الدراسة", "ما بعد الإجازة"],
    "holiday_eid_adha": ["الإجازة", "الاختبارات بعد الإجازة", "التعارضات"],
    "semester_end": ["نهاية الفصل", "الدرجات", "التسجيل القادم"],
}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--window", type=int, default=7, help="Days before/after to consider near event")
    args = ap.parse_args()

    target = pd.to_datetime(args.date)
    window = timedelta(days=args.window)

    cal = pd.read_csv(CALENDAR_PATH)
    cal["start_date"] = pd.to_datetime(cal["start_date"])
    cal["end_date"] = pd.to_datetime(cal["end_date"])

    active = []
    nearby = []

    for _, e in cal.iterrows():
        start = e["start_date"]
        end = e["end_date"]

        if start <= target <= end:
            active.append(e)
        elif start - window <= target <= end + window:
            nearby.append(e)

    print()
    print("ACADEMIC PHASE DETECTOR v1")
    print("Date:", args.date)
    print()

    if active:
        print("ACTIVE EVENTS")
        for e in active:
            print(f"- {e['event_type']} | {e['event_name_ar']} | {e['start_date'].date()} → {e['end_date'].date()}")
    else:
        print("ACTIVE EVENTS")
        print("- None")

    print()

    if nearby:
        print("NEARBY EVENTS")
        for e in nearby:
            print(f"- {e['event_type']} | {e['event_name_ar']} | {e['start_date'].date()} → {e['end_date'].date()}")
    else:
        print("NEARBY EVENTS")
        print("- None")

    print()

    event_types = [e["event_type"] for e in active] or [e["event_type"] for e in nearby]

    expected = []
    for t in event_types:
        expected.extend(PHASE_EXPECTATIONS.get(t, []))

    expected = list(dict.fromkeys(expected))

    print("EXPECTED STUDENT CONCERNS")
    if expected:
        for x in expected:
            print("-", x)
    else:
        print("- General academic questions")

    print()

    if active:
        phase = " | ".join([e["event_name_ar"] for e in active])
    elif nearby:
        phase = "Near: " + " | ".join([e["event_name_ar"] for e in nearby])
    else:
        phase = "Normal academic period"

    print("PHASE SUMMARY")
    print(phase)

    # Machine-readable output for future bot / worker integration
    import json
    payload = {
        "date": args.date,
        "phase_summary": phase,
        "active_events": [
            {
                "event_type": e["event_type"],
                "event_name_ar": e["event_name_ar"],
                "event_name_en": e["event_name_en"],
                "start_date": str(e["start_date"].date()),
                "end_date": str(e["end_date"].date()),
            }
            for e in active
        ],
        "nearby_events": [
            {
                "event_type": e["event_type"],
                "event_name_ar": e["event_name_ar"],
                "event_name_en": e["event_name_en"],
                "start_date": str(e["start_date"].date()),
                "end_date": str(e["end_date"].date()),
            }
            for e in nearby
        ],
        "expected_student_concerns": expected,
    }

    Path("outputs").mkdir(exist_ok=True)
    out_path = Path("outputs/current_academic_phase.json")
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print()
    print("JSON OUTPUT")
    print(out_path)

if __name__ == "__main__":
    main()
