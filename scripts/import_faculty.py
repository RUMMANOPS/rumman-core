#!/usr/bin/env python3
"""
import_faculty.py — One-shot import of 718 SEU faculty into kg_faculty.

Source: /Users/ibrahim../Projects/0-RUMMAN/بيانات الدكاترة/seu_academic_contacts.csv
Target: kg_faculty table in Supabase

Run once after migration 037:
    python3 scripts/import_faculty.py
    python3 scripts/import_faculty.py --dry-run
"""
from __future__ import annotations

import os
import csv
import json
import argparse
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
TENANT_ID    = "00000000-0000-0000-0000-000000000001"

HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "resolution=merge-duplicates,return=minimal",
}

# Path to CSV — try multiple locations
_CSV_CANDIDATES = [
    Path("/Users/ibrahim../Projects/0-RUMMAN/بيانات الدكاترة/seu_academic_contacts.csv"),
    Path(__file__).parent.parent.parent / "بيانات الدكاترة" / "seu_academic_contacts.csv",
]

# College code mapping from Arabic college name
_COLLEGE_MAP = {
    "كلية الحوسبة والمعلوماتية":       "CCI",
    "كلية العلوم الإدارية والمالية":    "CAFS",
    "كلية العلوم الصحية":              "CHS",
    "كلية العلوم والدراسات النظرية":    "CSTS",
    "كلية القانون":                    "LAW",
    "الكلية التطبيقية":                "APPL",
    "عمادة القبول والتسجيل":            "REG",
    "عمادة شؤون الطلاب":               "DSA",
    "عمادة التعليم الإلكتروني":         "EL",
    "رئاسة الجامعة":                   "PRES",
}


def _find_csv() -> Path:
    for p in _CSV_CANDIDATES:
        if p.exists():
            return p
    raise FileNotFoundError(
        f"seu_academic_contacts.csv not found. Tried:\n" +
        "\n".join(f"  {p}" for p in _CSV_CANDIDATES)
    )


def _map_row(row: dict) -> dict:
    """Map CSV row to kg_faculty columns."""
    college_ar = row.get("college", "") or ""
    college_code = _COLLEGE_MAP.get(college_ar.strip(), None)

    # Normalize problem_types from semicolon-separated string
    problem_types_raw = row.get("problem_types_fit", "") or ""
    problem_types = [t.strip() for t in problem_types_raw.split(";") if t.strip()]

    return {
        "tenant_id":              TENANT_ID,
        "external_id":            row.get("id") or None,
        "name_ar":                row.get("full_name_ar") or None,
        "name_en":                row.get("full_name_en") or None,
        "title":                  row.get("title_ar") or row.get("title_en") or None,
        "role_type":              row.get("role_type") or "faculty_member",
        "college_code":           college_code,
        "department":             row.get("department") or None,
        "email":                  row.get("email") or None,
        "campus":                 row.get("campus_or_branch") or None,
        "contact_channel":        row.get("direct_or_via_dept") or None,
        "contact_when":           row.get("contact_when") or None,
        "problem_types_fit":      problem_types if problem_types else None,
        "is_escalation_contact":  (row.get("first_or_escalation", "") or "").lower() == "escalation",
        "data_source":            "seu_academic_contacts_csv",
    }


def import_faculty(dry_run: bool = False) -> None:
    csv_path = _find_csv()
    print(f"Reading: {csv_path}")

    with csv_path.open(encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = [_map_row(r) for r in reader]

    print(f"Found {len(rows)} records")

    if dry_run:
        print("DRY RUN — first 3 records:")
        for r in rows[:3]:
            print(json.dumps(r, ensure_ascii=False, indent=2))
        return

    # Batch upsert in chunks of 100
    BATCH = 100
    inserted = 0
    errors   = 0

    with httpx.Client(timeout=30) as http:
        for i in range(0, len(rows), BATCH):
            batch = rows[i:i + BATCH]
            resp = http.post(
                f"{SUPABASE_URL}/rest/v1/kg_faculty",
                headers={**HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"},
                content=json.dumps(batch),
            )
            if resp.status_code in (200, 201):
                inserted += len(batch)
                print(f"  Upserted {inserted}/{len(rows)}")
            else:
                errors += len(batch)
                print(f"  ERROR batch {i//BATCH}: {resp.status_code} {resp.text[:200]}")

    print(f"\nDone — {inserted} inserted/updated, {errors} errors")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import SEU faculty into kg_faculty")
    parser.add_argument("--dry-run", action="store_true", help="Print first 3 records without writing")
    args = parser.parse_args()
    import_faculty(dry_run=args.dry_run)
