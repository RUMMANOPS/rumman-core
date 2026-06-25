"""
banner_client.py — shared anonymous SEU Banner client (READ-ONLY, NO login/credentials).

Used by both scripts/banner_live_sync.py (full sync) and app/student_life_api.py
(live pre-approval re-check). Single source of truth for Banner session, fetch,
normalization, a short-TTL live cache, and CRN availability evaluation.

CRN-level server filtering is NOT supported by Banner (verified): searchResults
ignores txt_courseReferenceNumber. So live re-check = fetch ALL sections, then
filter the requested CRNs in memory. A short-TTL in-process cache (default 15s,
max 30s) collapses concurrent fetches during peak registration.
"""
import os, time, json, hashlib, threading
import httpx

BANNER     = "https://bannservices.seu.edu.sa/StudentRegistrationSsb/ssb"
SOURCE_URL = f"{BANNER}/classSearch/classSearch"
UA         = "RUMMAN-academic-tool/0.1 (student schedule helper; contact rumman.ops@gmail.com)"
PAGE_SIZE  = 300
TENANT_ID  = "00000000-0000-0000-0000-000000000001"
TERM_LABEL_DEFAULT = "Summer Term 2025-2026"
DAY_FLAGS  = [("sunday","Sunday"),("monday","Monday"),("tuesday","Tuesday"),
              ("wednesday","Wednesday"),("thursday","Thursday"),("friday","Friday"),("saturday","Saturday")]


class BannerUnavailable(Exception):
    """Raised when Banner cannot be reached/parsed (403/429/timeout/login redirect/non-JSON)."""


def live_cache_ttl():
    try:
        v = int(os.environ.get("BANNER_LIVE_RECHECK_CACHE_TTL_SECONDS", "15"))
    except Exception:
        v = 15
    return max(1, min(v, 30))   # default 15, hard max 30


# ── parsing / normalization ───────────────────────────────────────────────────
def fmt_time(t):
    if not t or len(str(t)) < 3:
        return None
    s = str(t).zfill(4)
    return f"{s[:2]}:{s[2:]}"


def build_class_meetings(section):
    out = []
    for mf in (section.get("meetingsFaculty") or []):
        mt = mf.get("meetingTime") or {}
        days = [name for flag, name in DAY_FLAGS if mt.get(flag)] or [None]
        for day in days:
            out.append({
                "day": day, "start_time": fmt_time(mt.get("beginTime")), "end_time": fmt_time(mt.get("endTime")),
                "type": mt.get("meetingTypeDescription"), "room": mt.get("room"),
                "building": mt.get("buildingDescription"), "campus": mt.get("campusDescription"),
                "start_date": mt.get("startDate"), "end_date": mt.get("endDate"),
            })
    return out


def derive_delivery(section, meetings):
    types = " ".join((m.get("type") or "") for m in meetings).lower()
    if "virtual" in types and "class" in types: return "blended"
    if "virtual" in types: return "virtual"
    return (section.get("instructionalMethodDescription") or section.get("scheduleTypeDescription") or "").strip() or None


def compute_hash(row):
    payload = {k: row[k] for k in row if k not in ("source_url", "source_term_label", "source_hash")}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def normalize_section(section, default_term=None):
    meetings = build_class_meetings(section)
    term_code = str(section.get("term") or default_term or "")
    if not term_code:
        raise ValueError(f"normalize_section: no term_code for CRN {section.get('courseReferenceNumber')}")
    row = {
        "tenant_id": TENANT_ID, "term_code": term_code,
        "crn": str(section.get("courseReferenceNumber")),
        "subject_course": section.get("subjectCourse"), "course_name": section.get("courseTitle"),
        "credit_hours": section.get("creditHourLow") or section.get("creditHours") or section.get("creditHourHigh"),
        "section_number": section.get("sequenceNumber"),
        "capacity": section.get("maximumEnrollment"), "enrolled": section.get("enrollment"),
        # NOTE: remaining_seats may be a Banner sentinel (e.g. 9999 = uncapped/virtual) — store raw,
        # but downstream must treat it as "practically available", not a literal seat count.
        "remaining_seats": section.get("seatsAvailable"), "open_section": section.get("openSection"),
        "wait_capacity": section.get("waitCapacity"), "wait_count": section.get("waitCount"),
        "wait_available": section.get("waitAvailable"), "campus": section.get("campusDescription"),
        "delivery_mode": derive_delivery(section, meetings), "part_of_term": section.get("partOfTerm"),
        "class_meetings": meetings, "source_url": SOURCE_URL,
        "source_term_label": section.get("termDesc") or TERM_LABEL_DEFAULT,
    }
    row["source_hash"] = compute_hash(row)
    return row


# ── anonymous session + fetch ─────────────────────────────────────────────────
def _open_session():
    c = httpx.Client(timeout=20, headers={"User-Agent": UA}, follow_redirects=True)
    try:
        r = c.get(f"{BANNER}/classSearch/classSearch")
    except httpx.RequestError as e:
        c.close(); raise BannerUnavailable(f"network: {e}")
    if r.status_code in (403, 429):
        c.close(); raise BannerUnavailable(f"classSearch {r.status_code}")
    return c


def _get(c, url, **kw):
    last = None
    for attempt in range(3):
        try:
            r = c.get(url, **kw)
        except httpx.RequestError as e:
            last = str(e)
            if attempt == 2: raise BannerUnavailable(f"network: {e}")
            time.sleep(1.0 * (attempt + 1)); continue
        if r.status_code in (403, 429):
            raise BannerUnavailable(f"{r.status_code} on {url}")
        if r.status_code == 200:
            return r
        last = r.status_code
        time.sleep(1.0 * (attempt + 1))
    raise BannerUnavailable(f"non-200 ({last}) on {url}")


def discover_terms(c):
    return _get(c, f"{BANNER}/classSearch/getTerms", params={"searchTerm": "", "offset": 1, "max": 20}).json()


def bind_term(c, term, usid):
    try:
        r = c.post(f"{BANNER}/term/search", params={"mode": "search"},
                   data={"term": term, "studyPath": "", "studyPathText": "",
                         "startDatepicker": "", "endDatepicker": "", "uniqueSessionId": usid})
    except httpx.RequestError as e:
        raise BannerUnavailable(f"network: {e}")
    if r.status_code in (403, 429):
        raise BannerUnavailable(f"term/search {r.status_code}")


def fetch_all_sections(c, term, usid):
    rows, total, http_ok = [], None, 0
    offset = 0
    while True:
        r = _get(c, f"{BANNER}/searchResults/searchResults", params={
            "txt_term": term, "startDatepicker": "", "endDatepicker": "",
            "uniqueSessionId": usid, "pageOffset": offset, "pageMaxSize": PAGE_SIZE,
            "sortColumn": "subjectDescription", "sortDirection": "asc"})
        if "json" not in r.headers.get("content-type", ""):
            raise BannerUnavailable("searchResults not JSON (login/session redirect?)")
        http_ok += 1
        d = r.json()
        total = d.get("totalCount") if total is None else total
        batch = d.get("data") or []
        rows.extend(batch)
        offset += PAGE_SIZE
        if not batch or offset >= (total or 0):
            break
        time.sleep(1.0)
    return rows, total, http_ok


def fetch_live_normalized(term):
    """Open anonymous session, pull ALL sections, return (normalized_list, total, http_ok). May raise BannerUnavailable."""
    usid = "rmnlive" + hashlib.md5(term.encode()).hexdigest()[:8]
    c = _open_session()
    try:
        bind_term(c, term, usid)
        rows, total, http_ok = fetch_all_sections(c, term, usid)
    finally:
        c.close()
    return [normalize_section(s, default_term=term) for s in rows], total, http_ok


# ── short-TTL live cache (collapses concurrent fetches) ───────────────────────
_CACHE = {}              # term -> (ts, normalized, total)
_LOCK = threading.Lock()


def get_live_sections(term):
    """Return (normalized_list, total, cache_age_seconds, did_fetch). Honors a short TTL cache."""
    ttl = live_cache_ttl()
    ent = _CACHE.get(term)
    now = time.time()
    if ent and (now - ent[0]) <= ttl:
        return ent[1], ent[2], now - ent[0], False
    with _LOCK:                          # only one fetch in flight; concurrent callers reuse it
        ent = _CACHE.get(term); now = time.time()
        if ent and (now - ent[0]) <= ttl:
            return ent[1], ent[2], now - ent[0], False
        normalized, total, _ = fetch_live_normalized(term)
        _CACHE[term] = (time.time(), normalized, total)
        return normalized, total, 0.0, True


# ── CRN availability evaluation (pure; unit-testable) ─────────────────────────
def evaluate_crns(requested_crns, live_map):
    """
    requested_crns: list of CRN strings (already de-duplicated).
    live_map: {crn -> normalized section}.
    A CRN is available iff open_section is True AND remaining_seats is a non-zero int
    (9999 sentinel counts as available). Returns (ok, sections[], failed_crns[]).
    """
    sections, failed = [], []
    for crn in requested_crns:
        s = live_map.get(crn)
        if s is None:
            res = {"crn": crn, "ok": False, "open_section": None, "remaining_seats": None, "reason": "not_found"}
        else:
            open_ = bool(s.get("open_section"))
            seats = s.get("remaining_seats")
            available = open_ and isinstance(seats, int) and seats != 0   # 9999 -> available; 0 -> not
            res = {"crn": crn, "ok": available, "open_section": open_,
                   "remaining_seats": seats, "reason": None if available else "closed_or_full"}
        sections.append(res)
        if not res["ok"]:
            failed.append(crn)
    return (len(failed) == 0), sections, failed
