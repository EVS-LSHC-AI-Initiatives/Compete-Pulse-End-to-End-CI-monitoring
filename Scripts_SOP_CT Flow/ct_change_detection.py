#!/usr/bin/env python3
"""
ct_change_detection.py
──────────────────────
Compare two ClinicalTrials.gov extractions and highlight what changed.

Compares the "Deduplicated" sheet (one row per unique NCT ID) of two
Step2_Final_Dataset_*.xlsx files and produces a highlighted report:

    • Changed cell           → YELLOW
    • New trial (whole row)  → GREEN
    • Removed trial          → listed on the "Change Summary" sheet

Two modes (chosen interactively on launch):

    1. Compare two existing files
       A file-picker window opens for the OLD file, then for the NEW file.
       (If no GUI is available, you are asked to type the paths instead.)

    2. Run a fresh extraction, then compare
       Runs ct_step1_search + ct_step2_enrich (in THIS folder) to produce a
       brand-new dataset, then compares it against an OLD file you specify.

Output (next to this script):
    Change_Reports/Change_Report_YYMMDD_HHMMSS.xlsx

Requirements:
    pip install requests openpyxl     (requests only needed for Mode 2)
"""

import re
import shutil
import sys
import time
import traceback
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zipfile import BadZipFile

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# ── Config ──────────────────────────────────────────────────────────────────────

SCRIPT_DIR   = Path(__file__).parent
COMPARE_SHEET = "Deduplicated"   # sheet to compare in both workbooks
KEY_COLUMN    = "NCT ID"         # unique key used to match rows

# Highlight fills
YELLOW_FILL = PatternFill("solid", fgColor="FFFF00")   # changed cell
GREEN_FILL  = PatternFill("solid", fgColor="C6EFCE")   # new trial row

# Column headers to EXCLUDE from comparison (never count as a "change").
# The CT.gov link is constant by construction; add others here if a column is
# too noisy (e.g. "Last Updated") and you don't want it flagged.
EXCLUDE_FROM_COMPARE = {
    "ClinicalTrials.gov Link",
    "Record Version Holder",
}

# Fields whose changes are considered high-priority (highlighted orange in the
# Detailed Changes sheet and counted separately in the Change Summary).
CRITICAL_FIELDS = {
    "Status", "Last Known Status", "Why Stopped",
    "Phase(s)", "Enrollment", "Primary Completion", "Study Completion",
    "Has Results", "Results First Posted", "Large Documents",
}
CRITICAL_FILL = PatternFill("solid", fgColor="FFC000")   # orange — critical field change
FETCH_ERROR_FILL = PatternFill("solid", fgColor="BFBFBF")   # gray — unreliable data, excluded

# Location-related fields. When any of these change, the raw before/after blobs
# are huge multi-site strings that are impossible to tally in a single cell, so
# they are itemized site-by-site on the dedicated "Location Changes" sheet and
# SUPPRESSED from the "Detailed Changes" sheet (they remain highlighted in the
# Deduplicated sheet). "Locations Hash" is a derived fingerprint of "Locations"
# — pure noise in a human-readable diff — so it is suppressed there too.
LOCATION_FIELD      = "Locations"
LOCATION_CONTACTS_FIELD = "Location Contacts"
LOCATION_FIELDS = {LOCATION_FIELD, LOCATION_CONTACTS_FIELD, "Locations Hash"}

# Fills reused by the Location Changes sheet (green/pink already implied elsewhere).
REMOVED_FILL = PatternFill("solid", fgColor="FFC7CE")   # pink — removed site/contact
RELABEL_FILL = PatternFill("solid", fgColor="D9E1F2")   # blue — same site, re-spelled

# Countries counted as "US" when segregating site counts US vs ex-US. Territories
# are included because they sit under the same FDA/IRB regulatory umbrella — move
# them out of this set if the client wants "US" to mean the 50 states only.
# Compared case-insensitively against the LAST comma-separated token of a site.
US_COUNTRIES = {
    "united states",
    "puerto rico",
    "guam",
    "american samoa",
    "northern mariana islands",
    "virgin islands (u.s.)",
    "u.s. virgin islands",
    "united states minor outlying islands",
}

# Site statuses reported first (in this order) in the per-status tallies; any
# other status seen in the data is appended alphabetically after these. These
# must match the rendered form ct_query_batch writes into the Locations blob:
# status.replace("_", " ").title() — so "ACTIVE_NOT_RECRUITING" arrives as
# "Active Not Recruiting" (no comma). Sites CT.gov left without a status bucket
# as UNSPECIFIED_STATUS.
STATUS_ORDER = [
    "Recruiting",
    "Not Yet Recruiting",
    "Enrolling By Invitation",
    "Active Not Recruiting",
    "Suspended",
    "Completed",
    "Terminated",
    "Withdrawn",
    "Unspecified",
]

# Region bucket for a site whose entry carries no country at all (a bare facility
# name — ~5 in 27,000 in practice). Kept distinct rather than silently folded into
# ex-US so the US/ex-US split never quietly misattributes a site.
UNKNOWN_REGION = "Country n/a"

# The complete CT.gov per-site recruitment status enum, rendered the way
# ct_query_batch writes it: status.replace("_", " ").title().
#
# A bracket in a site string is ONLY treated as the status if it matches one of
# these. Addresses legitimately carry bracketed region names — "Balears
# [Baleares], 7014, Spain", "Incheon-gwangyeoksi [Incheon]" — and a site with no
# status recorded ends on that bracket, so accepting any bracket would invent
# recruitment statuses called "Baleares" and "Incheon".
VALID_SITE_STATUSES = {
    "Recruiting", "Not Yet Recruiting", "Enrolling By Invitation",
    "Active Not Recruiting", "Suspended", "Terminated", "Completed",
    "Withdrawn", "Available", "No Longer Available",
    "Temporarily Not Available", "Approved For Marketing",
    "Withheld", "Unknown", "Unknown Status",
}

# Must match ct_query_batch.FETCH_ERROR_MARKER. Kept as a local literal (not
# imported) so Mode 1 (compare two files) never requires `requests` to be
# installed — only Mode 2 (fresh extraction) imports ct_query_batch at all.
FETCH_ERROR_MARKER = "FETCH ERROR"


def long_path(path) -> str:
    """
    Return a Windows extended-length path ("\\\\?\\...") so openpyxl/zipfile
    can read/write files under deeply nested folders (e.g. OneDrive) whose
    plain path would exceed the 260-character MAX_PATH limit. No-op on
    non-Windows platforms. (Duplicated from ct_query_batch.long_path for the
    same reason as FETCH_ERROR_MARKER above — keep Mode 1 dependency-free.)
    """
    s = str(Path(path).resolve())
    if sys.platform == "win32" and not s.startswith("\\\\?\\"):
        return "\\\\?\\" + s
    return s


# ── Excel reading helpers ────────────────────────────────────────────────────────

def read_dedup(path: Path):
    """
    Read the COMPARE_SHEET of a workbook.
    Returns (header_row, label->col map, {nct_id: {label: value}}, ordered nct list).

    Uses iter_rows() throughout — never ws.cell(row, col) — so it works correctly
    with both read-only and normal mode on large files.
    """
    # read_only=True streams the file row-by-row — fast even on large, heavily-
    # formatted files. We ONLY use iter_rows() here, never ws.cell(row,col),
    # which would hang in streaming mode.
    wb = openpyxl.load_workbook(long_path(path), read_only=True, data_only=True)
    if COMPARE_SHEET in wb.sheetnames:
        ws = wb[COMPARE_SHEET]
    else:
        ws = wb.active
        print(f"  ⚠  Sheet '{COMPARE_SHEET}' not found in {path.name}; "
              f"using '{ws.title}' instead.")

    # ── Find header row by scanning with iter_rows (no .cell() calls) ──────
    hdr_row_idx = None
    labels = {}
    for row_vals in ws.iter_rows(min_row=1, max_row=15, values_only=True):
        hdr_row_idx = (hdr_row_idx or 0) + 1
        row_labels = {}
        found = False
        for c_idx, val in enumerate(row_vals, 1):
            if val is None:
                continue
            lbl = str(val).strip()
            row_labels[lbl] = c_idx
            if lbl == KEY_COLUMN:
                found = True
        if found:
            labels = row_labels
            break
    if not labels:
        raise ValueError(
            f"Could not find a '{KEY_COLUMN}' header in sheet '{ws.title}' "
            f"of {path.name}."
        )

    key_col_idx = labels[KEY_COLUMN]

    # ── Read all data rows ──────────────────────────────────────────────────
    data = {}
    order = []
    for row_vals in ws.iter_rows(min_row=hdr_row_idx + 1, values_only=True):
        nct = row_vals[key_col_idx - 1]  # iter_rows is 0-based here
        nct = str(nct).strip() if nct is not None else ""
        if not nct:
            continue
        row = {}
        for lbl, c_idx in labels.items():
            v = row_vals[c_idx - 1] if c_idx - 1 < len(row_vals) else None
            row[lbl] = "" if v is None else str(v).strip()
        data[nct] = row
        order.append(nct)

    wb.close()
    return hdr_row_idx, labels, data, order


# ── Comparison ───────────────────────────────────────────────────────────────────

SITES_SHEET = "Sites"
_SITE_FIELDS = ("Facility", "City", "State", "ZIP", "Country", "Status")


def read_sites(path: Path):
    """
    Read the "Sites" sheet (one row per clinical site) written by ct_step2_enrich.

    Returns {nct_id: [ {facility, city, state, zip, country, status}, ... ]},
    or None when the sheet is absent — i.e. the file predates the Sites sheet, in
    which case callers fall back to parsing the packed "Locations" column.

    Order within a trial is preserved, and repeated identical sites are kept as
    separate entries.
    """
    wb = openpyxl.load_workbook(long_path(path), read_only=True, data_only=True)
    try:
        if SITES_SHEET not in wb.sheetnames:
            return None
        ws = wb[SITES_SHEET]
        rows = ws.iter_rows(values_only=True)
        header = next(rows, None)
        if not header:
            return None
        idx = {str(h).strip(): i for i, h in enumerate(header) if h is not None}
        if "NCT ID" not in idx or any(f not in idx for f in _SITE_FIELDS):
            return None

        def _val(row, name):
            i = idx[name]
            v = row[i] if i < len(row) else None
            if v is None:
                return ""
            s = str(v).strip()
            # ct_query_batch._safe_str prepends "'" to any value starting with
            # =,+,-,@ so Excel can't read it as a formula, and openpyxl persists
            # that apostrophe as real text. Undo it here so the field matches what
            # CT.gov actually sent — e.g. a zip of "+90" round-trips as "'+90".
            if s[:1] == "'" and s[1:2] in ("=", "+", "-", "@"):
                s = s[1:]
            return s

        out = defaultdict(list)
        for row in rows:
            nct = _val(row, "NCT ID")
            if not nct:
                continue
            out[nct].append({f.lower(): _val(row, f) for f in _SITE_FIELDS})
        return dict(out)
    finally:
        wb.close()


def _is_fetch_error(row: dict) -> bool:
    """True if this row's data came from a trial whose enrichment failed
    (e.g. a transient network error) — its fields are placeholder/blank,
    not real data, so it must never be compared as if it were."""
    return FETCH_ERROR_MARKER in (row.get("Brief Title", "") or "")


def compare(old_data: dict, new_data: dict, old_labels: dict, new_labels: dict):
    """
    Compare two {nct: {label: value}} dicts.
    Returns dict with:
      changed      : {nct: [(changed_label, old_value, new_value), ...]}
      new          : [nct, ...]
      removed      : [nct, ...]
      unchanged    : count
      fetch_errors : [nct, ...]  — excluded from changed/unchanged because
                                   one side's data is a fetch-error placeholder,
                                   not a real value (would otherwise look like
                                   every field "changed" to blank)
      compared_labels : set of labels actually compared
    """
    common_labels = (set(old_labels) & set(new_labels)) - EXCLUDE_FROM_COMPARE
    compared_labels = common_labels - {KEY_COLUMN}

    changed, new_ncts, unchanged, fetch_errors = {}, [], 0, []
    for nct, new_row in new_data.items():
        if nct not in old_data:
            new_ncts.append(nct)
            continue
        old_row = old_data[nct]
        if _is_fetch_error(old_row) or _is_fetch_error(new_row):
            fetch_errors.append(nct)
            continue
        diffs = [
            (lbl, old_row.get(lbl, ""), new_row.get(lbl, ""))
            for lbl in compared_labels
            if new_row.get(lbl, "") != old_row.get(lbl, "")
        ]
        if diffs:
            changed[nct] = diffs   # list[tuple[field, old_val, new_val]]
        else:
            unchanged += 1

    removed = [nct for nct in old_data if nct not in new_data]

    return {
        "changed": changed,
        "new": new_ncts,
        "removed": removed,
        "unchanged": unchanged,
        "fetch_errors": fetch_errors,
        "compared_labels": compared_labels,
    }


# ── Report building ──────────────────────────────────────────────────────────────

def _load_workbook_with_retry(path: Path, attempts: int = 4, delay: float = 2.0):
    """
    openpyxl.load_workbook(), retrying on transient I/O errors.

    Right after out_path is written, OneDrive may still be syncing/locking it
    (or Explorer/AV briefly holds a read lock) — a full read/write load of a
    large just-written file in that window can raise PermissionError or a
    corrupt-zip error even though the bytes on disk are fine a moment later.
    Retrying beats surfacing an "Unexpected error" that (per apply_highlights'
    write-then-build order) leaves a misleading raw-copy file behind.
    """
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            return openpyxl.load_workbook(long_path(path), data_only=True)
        except (PermissionError, OSError, BadZipFile) as exc:
            last_exc = exc
            if attempt < attempts:
                print(f"  !  Could not open report for highlighting yet "
                      f"({exc.__class__.__name__}: {exc}); retrying "
                      f"({attempt}/{attempts - 1}) ...")
                time.sleep(delay)
    raise last_exc


def apply_highlights(out_path: Path, result: dict):
    """Open the copied NEW workbook and paint changed cells / new rows."""
    wb = _load_workbook_with_retry(out_path)
    if COMPARE_SHEET in wb.sheetnames:
        ws = wb[COMPARE_SHEET]
    else:
        ws = wb.active

    # Re-read header using iter_rows (consistent with read_dedup, no .cell() stalls)
    hdr_row = None
    labels = {}
    for r_idx, row_vals in enumerate(ws.iter_rows(min_row=1, max_row=15, values_only=True), 1):
        row_labels = {}
        found = False
        for c_idx, val in enumerate(row_vals, 1):
            if val is None:
                continue
            lbl = str(val).strip()
            row_labels[lbl] = c_idx
            if lbl == KEY_COLUMN:
                found = True
        if found:
            hdr_row = r_idx
            labels = row_labels
            break
    if not labels:
        raise ValueError(f"Header row not found in {out_path.name}")

    key_col = labels[KEY_COLUMN]
    title_col = labels.get("Brief Title")
    changed = result["changed"]
    new_set = set(result["new"])
    fetch_error_set = set(result.get("fetch_errors", []))

    # Build row-index → NCT map (and NCT → title map) first, then apply fills
    nct_row_map = {}
    nct_title_map = {}
    for r_idx, row_vals in enumerate(ws.iter_rows(min_row=hdr_row + 1, values_only=True),
                                     hdr_row + 1):
        nct = row_vals[key_col - 1] if key_col - 1 < len(row_vals) else None
        nct = str(nct).strip() if nct is not None else ""
        if nct:
            nct_row_map[nct] = r_idx
            if title_col and title_col - 1 < len(row_vals):
                title = row_vals[title_col - 1]
                nct_title_map[nct] = str(title).strip() if title is not None else ""

    # Apply fills via direct cell access (workbook is open read/write here, not read-only)
    for nct, r in nct_row_map.items():
        if nct in fetch_error_set:
            for c in range(1, ws.max_column + 1):
                ws.cell(row=r, column=c).fill = FETCH_ERROR_FILL
        elif nct in new_set:
            for c in range(1, ws.max_column + 1):
                ws.cell(row=r, column=c).fill = GREEN_FILL
        elif nct in changed:
            for lbl, _old, _new in changed[nct]:
                c = labels.get(lbl)
                if c:
                    ws.cell(row=r, column=c).fill = YELLOW_FILL

    # Sheets are created at fixed indices and push existing sheets right. Build
    # Detailed Changes first (index 0), then Location Changes at index 1 (so it
    # sits just after Detailed Changes), then Change Summary at index 0 — leaving
    # final order:
    #   [Change Summary, Detailed Changes, Location Changes, Deduplicated]
    # (Location Changes is omitted entirely when no trial changed its locations.)
    nct_start_row = _add_detail_sheet(wb, result, nct_title_map)
    result["has_location_changes"] = _add_location_changes_sheet(wb, result, nct_title_map)
    _add_summary_sheet(wb, result, nct_start_row, nct_title_map)
    wb.save(long_path(out_path))


_THIN   = Side(border_style="thin", color="BFBFBF")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_HDR_FILL = PatternFill("solid", fgColor="D9D9D9")


_FORMULA_PREFIXES = ("=", "+", "-", "@")

# Excel's hard per-cell character limit — openpyxl truncates silently past
# this with no error, which would otherwise hide the tail of a long old/new
# value in the "Detailed Changes" sheet. Old/new values here come from
# ct_query_batch's already-truncated cells, but a field concatenated from
# several source columns (or edited by hand) could still exceed the limit,
# so this is enforced defensively at write time too.
EXCEL_CELL_LIMIT = 32767
EXCEL_MAX_COLS = 16384      # Excel's hard per-sheet column limit (XFD)
_TRUNCATION_NOTE = "\n\n[TRUNCATED — exceeds Excel's 32,767-char cell limit]"


def _signed_cell(ws, r, c, delta: int, bold=False, fill=None):
    """
    Write a signed delta as a real NUMBER with a +/- display format, rather than
    the string "+10". A leading "+"/"-" string would trip _summary_cell's
    formula guard and be stored as "'+10", which Excel renders with a visible
    apostrophe (the escape only suppresses display when typed, not when set
    programmatically). Zero shows as an em dash.
    """
    cell = _summary_cell(ws, r, c, delta, bold=bold, fill=fill)
    cell.number_format = '+0;-0;"—"'
    return cell


def _summary_cell(ws, r, c, value, bold=False, fill=None, wrap=False):
    # Neutralize leading =,+,-,@ so externally-sourced text (titles, field
    # values) can never be interpreted as a formula when Excel opens this.
    if isinstance(value, str) and value and value[0] in _FORMULA_PREFIXES:
        value = "'" + value
    if isinstance(value, str) and len(value) > EXCEL_CELL_LIMIT:
        value = value[: EXCEL_CELL_LIMIT - len(_TRUNCATION_NOTE)] + _TRUNCATION_NOTE
    cell = ws.cell(row=r, column=c, value=value)
    cell.font = Font(name="Calibri", size=10, bold=bold)
    cell.alignment = Alignment(vertical="top", horizontal="left", wrap_text=wrap)
    cell.border = _BORDER
    if fill:
        cell.fill = fill
    return cell


# ── Location parsing / diffing ─────────────────────────────────────────────────
#
# The "Locations" field packs every site as
#     facility, city, state, zip, country, [Status], (lat,lon)
# joined by " | ". Facility names themselves can contain commas (e.g.
# "Liver transplant center, Holy family hospital"), so we NEVER split a site on
# commas — we only strip the unambiguous trailing [Status] and (geo) tokens. The
# remainder ("identity": facility, city, state, zip, country) is what we match a
# site by across the old and new extraction; the status is what typically flips.

_STATUS_RE = re.compile(r"\s*\[([^\[\]]+)\]\s*")
_GEO_RE    = re.compile(r"\s*\(-?\d[\d.]*,-?\d[\d.]*\)\s*")


# ct_query_batch appends this marker when a cell exceeds Excel's 32,767-char
# limit. 14 of ~2,500 trials hit it in practice. A truncated blob is MISSING an
# unknown number of trailing sites, so no site count derived from it can be
# trusted — such trials are reported as uncountable rather than given a wrong
# number (see _location_stats_for_trial).
TRUNCATION_MARKER = "[TRUNCATED"

# Separator for de-duplicating repeated identical site entries within one blob
# (15 trials do this; NCT06528314 lists 312 entries for 300 distinct strings).
# \x00 cannot occur in Excel text, so a key can always be split back safely.
_DUP_SEP = "\x00#"


def _is_truncated(loc_str: str) -> bool:
    return TRUNCATION_MARKER in (loc_str or "")


def _key_identity(key: str) -> str:
    """Strip the de-duplication suffix from a site key to get its identity."""
    return key.split(_DUP_SEP)[0]


def _parse_location_sites(loc_str: str) -> dict:
    """
    Parse a "Locations" blob into
        {key: {"status", "full", "identity"}}.

    identity = the site string with [Status] and (geo) removed and stray commas
    collapsed, so a site matches across old/new even when its status changes.

    key = identity, plus a "\\x00#N" suffix for the 2nd and later occurrences of
    an identical identity. Without this, a trial listing the same site string
    twice would collapse to one dict entry, silently understating its site count
    and hiding the add/remove of a duplicate.
    """
    sites = {}
    seen = Counter()
    for site in (loc_str or "").split(" | "):
        site = site.strip()
        if not site or site.startswith(TRUNCATION_MARKER):
            continue
        # Scan brackets right-to-left for the first one that is a REAL status
        # (see VALID_SITE_STATUSES). Only that bracket is stripped from the
        # identity — a bracketed region name is part of the address and stays,
        # so two sites differing only by region can never collide.
        status, status_span = "", None
        for mt in reversed(list(_STATUS_RE.finditer(site))):
            if mt.group(1).strip() in VALID_SITE_STATUSES:
                status, status_span = mt.group(1).strip(), mt.span()
                break
        identity = site if status_span is None else (
            site[:status_span[0]] + " " + site[status_span[1]:])
        identity = _GEO_RE.sub(" ", identity)
        identity = re.sub(r"\s*,(\s*,)+", ",", identity)   # collapse ", ," runs
        identity = re.sub(r"\s+", " ", identity).strip().strip(",").strip()
        if not identity:
            continue
        seen[identity] += 1
        key = identity if seen[identity] == 1 else f"{identity}{_DUP_SEP}{seen[identity]}"
        sites[key] = {"status": status, "full": site, "identity": identity}
    return sites


# ── Site geography / identity-drift reconciliation ─────────────────────────────

def _sites_from_records(recs: list) -> dict:
    """
    Build the same {key: info} structure as _parse_location_sites, but from the
    STRUCTURED "Sites" sheet records — no string parsing, so none of the packed
    column's ambiguities apply. info carries the real country/city fields, which
    _region_of and _locality_of prefer over guessing from the identity string.
    """
    sites, seen = {}, Counter()
    for rec in recs or []:
        # Strip stray commas/whitespace from each field before joining. Some
        # CT.gov facility names END with a comma ("...Aarhus University Hospital,
        # Psychiatry,"), which would otherwise yield a doubled ", ," here while
        # the legacy blob path collapses such runs — making the SAME site look
        # like an add + a remove when a structured snapshot is compared against a
        # legacy one. 43 sites in the 31 Jul extraction hit this.
        parts = [str(p).strip().strip(",").strip()
                 for p in (rec.get("facility"), rec.get("city"), rec.get("state"),
                           rec.get("zip"), rec.get("country")) if p]
        identity = ", ".join(p for p in parts if p)
        identity = re.sub(r"\s+", " ", identity).strip()
        if not identity:
            continue
        seen[identity] += 1
        key = identity if seen[identity] == 1 else f"{identity}{_DUP_SEP}{seen[identity]}"
        sites[key] = {
            "status":   rec.get("status", "") or "",
            "full":     identity,
            "identity": identity,
            "country":  rec.get("country", "") or "",
            "city":     rec.get("city", "") or "",
        }
    return sites


def _region_of(key: str, info: dict) -> str:
    """
    'US' / 'ex-US' / UNKNOWN_REGION for a site. Uses the real country field when
    the site came from the structured Sites sheet; only falls back to reading the
    last comma token when working from the legacy packed column.
    """
    country = (info.get("country") or "").strip() or _site_country(key)
    if not country:
        return UNKNOWN_REGION
    return "US" if country.lower() in US_COUNTRIES else "ex-US"


def _locality_of(key: str, info: dict) -> str:
    """'city|country' for co-location advisories, preferring real fields."""
    city, country = (info.get("city") or "").strip(), (info.get("country") or "").strip()
    if city and country:
        return f"{city.lower()}|{country.lower()}"
    return _city_country(key)


def _site_country(key: str) -> str:
    """
    Country = the LAST comma-separated token of a site identity. Read from the
    right so facility names containing commas ("Liver transplant center, Holy
    family hospital") can never be mistaken for the country.

    Returns "" for a bare facility name with no comma at all — there is no
    country to read, and returning the facility name would silently misclassify
    the site as ex-US.
    """
    parts = [p.strip() for p in _key_identity(key).split(",") if p.strip()]
    return parts[-1] if len(parts) >= 2 else ""


def _site_region(key: str) -> str:
    """'US', 'ex-US', or UNKNOWN_REGION for a site (see US_COUNTRIES)."""
    country = _site_country(key)
    if not country:
        return UNKNOWN_REGION
    return "US" if country.lower() in US_COUNTRIES else "ex-US"


# A postal-code-ish token: short, contains a digit. Only ever applied to the
# MIDDLE tokens of an identity — never the facility (which legitimately contains
# digits, e.g. "Local Institution - 0093") and never the country.
_ZIPISH = re.compile(r"^(?=.*\d)[A-Za-z0-9][A-Za-z0-9 \-]{1,9}$")


def _norm_identity(key: str) -> str:
    """
    Normalised form of a site identity used to recognise that an "added" and a
    "removed" entry are the SAME site whose string merely changed shape between
    extractions — CT.gov starting/stopping to supply a ZIP, or a punctuation,
    case, or whitespace edit. Drops postal-code tokens and all non-alphanumerics.

    This is deliberately a DETERMINISTIC match: the facility name and geography
    must still be the same words. It will not (and must not) merge a genuine
    re-spelling like "Mass General" → "Massachusetts General Hospital" — that is
    a judgement call, and guessing it would silently change a reported count.
    Those cases are surfaced as an advisory flag instead (see _colocation_notes).
    """
    parts = [p.strip() for p in _key_identity(key).split(",") if p.strip()]
    if len(parts) > 2:
        parts = [parts[0]] + [p for p in parts[1:-1] if not _ZIPISH.match(p)] + [parts[-1]]
    return re.sub(r"[^a-z0-9]+", " ", " ".join(parts).lower()).strip()


def _reconcile_drift(added: dict, removed: dict):
    """
    Pull out add/remove pairs that are really one site whose identity string
    changed shape (see _norm_identity). A pair is only made when EXACTLY ONE
    addition and EXACTLY ONE removal share a normalised form — with duplicates
    present we cannot tell which maps to which, so we leave them alone rather
    than risk merging two distinct sites.

    Returns (relabeled, added, removed) where relabeled is a list of
    (old_key, new_key, old_status, new_status) and the dicts are the remainder.
    """
    add_by, rem_by = defaultdict(list), defaultdict(list)
    for k in added:
        if (n := _norm_identity(k)):
            add_by[n].append(k)
    for k in removed:
        if (n := _norm_identity(k)):
            rem_by[n].append(k)

    relabeled = []
    for n, new_keys in add_by.items():
        old_keys = rem_by.get(n, [])
        if len(new_keys) == 1 and len(old_keys) == 1:
            relabeled.append((old_keys[0], new_keys[0],
                              removed[old_keys[0]], added[new_keys[0]]))

    paired_new = {n for _o, n, _os, _ns in relabeled}
    paired_old = {o for o, _n, _os, _ns in relabeled}
    added = {k: v for k, v in added.items() if k not in paired_new}
    removed = {k: v for k, v in removed.items() if k not in paired_old}
    return relabeled, added, removed


def _city_country(key: str) -> str:
    """Coarse locality of a site: 'city|country', or '' if not determinable."""
    parts = [p.strip().lower() for p in _key_identity(key).split(",") if p.strip()]
    return f"{parts[1]}|{parts[-1]}" if len(parts) >= 3 else ""


def _colocation_notes(added: dict, removed: dict,
                      old_sites: dict, new_sites: dict) -> dict:
    """
    ADVISORY ONLY — never changes a count.

    Where exactly one added and one removed site sit in the same city+country,
    they *may* be the same facility re-spelled by CT.gov rather than a real
    swap. We cannot prove it (CT.gov geocodes many sites to the city centroid,
    so ~4,000 distinct sites in this dataset share coordinates — co-location is
    not evidence of identity). So both are still counted as a genuine add and a
    genuine remove, and this note is attached for a human to adjudicate.

    Returns {site_key: note}.
    """
    add_by, rem_by = defaultdict(list), defaultdict(list)
    for k in added:
        if (c := _locality_of(k, new_sites.get(k, {}))):
            add_by[c].append(k)
    for k in removed:
        if (c := _locality_of(k, old_sites.get(k, {}))):
            rem_by[c].append(k)

    notes = {}
    for c, new_keys in add_by.items():
        old_keys = rem_by.get(c, [])
        if len(new_keys) == 1 and len(old_keys) == 1:
            new_k, old_k = new_keys[0], old_keys[0]
            notes[new_k] = ("⚠ verify: same city as removed site "
                            f"\"{_key_identity(old_k)}\" — may be a re-spelling")
            notes[old_k] = ("⚠ verify: same city as added site "
                            f"\"{_key_identity(new_k)}\" — may be a re-spelling")
    return notes


def _status_counts(sites: dict) -> Counter:
    """Count sites by status. Sites with no status recorded bucket as 'Unspecified'."""
    return Counter((info.get("status") or "Unspecified") for info in sites.values())


def _snapshot_sites(blob: str, recs):
    """
    Sites for one snapshot of one trial, as {key: info}.

    Prefers the structured "Sites" sheet records (recs). Returns None to signal
    "cannot be trusted" when no records exist AND the packed blob was truncated —
    the legacy path's unavoidable blind spot.
    """
    if recs is not None:
        return _sites_from_records(recs)
    if _is_truncated(blob):
        return None
    return _parse_location_sites(blob)


def _location_stats_for_trial(diffs: list, old_recs=None, new_recs=None,
                              mixed: bool = False) -> dict:
    """
    Quantify one trial's location delta: how many sites were added and removed
    (split US vs ex-US, with re-listings excluded from both), the before/after
    site totals, and the before/after count of sites in each recruitment status.

    Counts come from the structured "Sites" sheet when both snapshots have it.
    Only for legacy files without that sheet do we parse the packed "Locations"
    column, and there a truncated blob yields {"truncated": True} — an unknown
    number of trailing sites is missing, so no count is reported at all.

    Returns {} if this trial's "Locations" field did not change.
    """
    for lbl, old_val, new_val in diffs:
        if lbl != LOCATION_FIELD:
            continue
        if mixed:
            return {"uncountable": "mixed"}
        old_sites = _snapshot_sites(old_val, old_recs)
        new_sites = _snapshot_sites(new_val, new_recs)
        if old_sites is None or new_sites is None:
            return {"truncated": True}

        old_map = {k: v["status"] for k, v in old_sites.items()}
        new_map = {k: v["status"] for k, v in new_sites.items()}

        added   = {k: v for k, v in new_map.items() if k not in old_map}
        removed = {k: v for k, v in old_map.items() if k not in new_map}
        _relabeled, added, removed = _reconcile_drift(added, removed)

        def _split(keys, src):
            c = Counter(_region_of(k, src.get(k, {})) for k in keys)
            return c["US"], c["ex-US"], c[UNKNOWN_REGION]

        add_us, add_ex, add_unk = _split(added, new_sites)
        rem_us, rem_ex, rem_unk = _split(removed, old_sites)
        old_us, old_ex, old_unk = _split(old_map, old_sites)
        new_us, new_ex, new_unk = _split(new_map, new_sites)
        return {
            "added_total": len(added), "added_us": add_us, "added_exus": add_ex,
            "added_unknown": add_unk,
            "removed_total": len(removed), "removed_us": rem_us, "removed_exus": rem_ex,
            "removed_unknown": rem_unk,
            "net": len(added) - len(removed),
            "old_total": len(old_map), "new_total": len(new_map),
            "old_us": old_us, "new_us": new_us,
            "old_exus": old_ex, "new_exus": new_ex,
            "old_unknown": old_unk, "new_unknown": new_unk,
            "old_status": _status_counts(old_sites),
            "new_status": _status_counts(new_sites),
            "truncated": False,
        }
    return {}


def _nuance_sentence(st: dict) -> str:
    """One-line plain-English summary of a trial's site delta — the sentence that
    drops straight into the client-facing alert narrative."""
    if not st:
        return ""
    if st.get("uncountable") == "mixed":
        return ("Site counts not reported — the two files store sites differently "
                "(only one has the structured 'Sites' sheet), so counts would not "
                "be like-for-like. The site-by-site listing to the right is still "
                "accurate. Compare two extractions that both have a 'Sites' sheet "
                "to get counts.")
    if st.get("truncated"):
        return ("Site counts unavailable — the source 'Locations' cell exceeded "
                "Excel's 32,767-character limit and was truncated, so an unknown "
                "number of sites is missing. Check this trial on CT.gov directly.")
    if not st["added_total"] and not st["removed_total"]:
        n = st["new_total"]
        return (f"No net site change ({n} site{'' if n == 1 else 's'}); "
                f"recruitment status updated at existing sites only.")
    def _split_text(kind):
        n_unk = st[f"{kind}_unknown"]
        tail = f", {n_unk} country n/a" if n_unk else ""
        return (f"{st[kind + '_total']} {kind} "
                f"({st[kind + '_us']} US, {st[kind + '_exus']} ex-US{tail})")

    bits = []
    if st["added_total"]:
        bits.append(_split_text("added"))
    if st["removed_total"]:
        bits.append(_split_text("removed"))
    net = st["net"]
    sign = "+" if net > 0 else ""
    return (f"Net {sign}{net} site{'' if abs(net) == 1 else 's'} "
            f"({st['old_total']} → {st['new_total']}): " + "; ".join(bits) + ".")


def _parse_location_contacts(contacts_str: str) -> dict:
    """Parse a "Location Contacts" blob into {facility: contacts_text}."""
    out = {}
    for entry in (contacts_str or "").split(" | "):
        entry = entry.strip()
        if not entry:
            continue
        facility, sep, rest = entry.partition(": ")
        if sep:
            out[facility.strip()] = rest.strip()
        else:
            out[entry] = ""
    return out


def _diff_keyed(old_map: dict, new_map: dict):
    """
    Compare two {key: value} dicts. Yields (change, key, old_value, new_value)
    where change is 'Added' | 'Removed' | 'Changed'.
    For sites the value is the status; for contacts it is the contacts text.
    """
    for key in new_map:
        if key not in old_map:
            yield ("Added", key, "", new_map[key])
    for key in old_map:
        if key not in new_map:
            yield ("Removed", key, old_map[key], "")
    for key in old_map:
        if key in new_map and old_map[key] != new_map[key]:
            yield ("Changed", key, old_map[key], new_map[key])


def _location_changes_for_trial(diffs: list, old_recs=None, new_recs=None):
    """
    Given one trial's diff list [(label, old, new), ...], return a list of
    per-site/contact change rows:
        [(category, change, site_or_facility, old_value, new_value), ...]
    Empty if this trial changed no location fields.
    """
    rows = []
    for lbl, old_val, new_val in diffs:
        if lbl == LOCATION_FIELD:
            old_sites = _snapshot_sites(old_val, old_recs)
            new_sites = _snapshot_sites(new_val, new_recs)
            old_map = {k: v["status"] for k, v in (old_sites or {}).items()}
            new_map = {k: v["status"] for k, v in (new_sites or {}).items()}

            if old_sites is None or new_sites is None:
                # Cannot itemize a list that is missing an unknown tail. Emit one
                # explicit row rather than a plausible-looking partial diff.
                rows.append(("Site", "Unavailable", "(source data truncated)",
                             "", _nuance_sentence({"truncated": True})))
                continue

            # Same reconciliation the counts use, so an itemized row can never
            # contradict the tallies in the count columns.
            added   = {k: v for k, v in new_map.items() if k not in old_map}
            removed = {k: v for k, v in old_map.items() if k not in new_map}
            relabeled, added, removed = _reconcile_drift(added, removed)
            notes = _colocation_notes(added, removed, old_sites, new_sites)

            for site, status in added.items():
                rows.append(("Site", "Added", _key_identity(site),
                             notes.get(site, ""), status))
            for site, status in removed.items():
                rows.append(("Site", "Removed", _key_identity(site),
                             status, notes.get(site, "")))
            for old_k, new_k, old_s, new_s in relabeled:
                note = f"{old_s} → {new_s}" if old_s != new_s else new_s
                rows.append(("Site", "Relabeled", _key_identity(new_k),
                             f"was: {_key_identity(old_k)}", note))
            for site in old_map:
                if site in new_map and old_map[site] != new_map[site]:
                    rows.append(("Site", "Changed", _key_identity(site),
                                 old_map[site], new_map[site]))
        elif lbl == LOCATION_CONTACTS_FIELD:
            old_c = _parse_location_contacts(old_val)
            new_c = _parse_location_contacts(new_val)
            for change, facility, o, n in _diff_keyed(old_c, new_c):
                rows.append(("Contact", change, facility, o, n))
    return rows


_CHANGE_FILL = {
    "Added":       GREEN_FILL,
    "Removed":     REMOVED_FILL,
    "Changed":     YELLOW_FILL,
    "Relabeled":   RELABEL_FILL,
    "Unavailable": FETCH_ERROR_FILL,
}


def _change_label(category: str, change: str) -> str:
    """Human-readable label for a change group, e.g. 'Site Added',
    'Site Removed', 'Status Changed', 'Contact Changed'."""
    if category == "Site" and change == "Changed":
        return "Status Changed"
    if change == "Relabeled":
        return "Site Re-listed"
    if change == "Unavailable":
        return "⚠ Truncated"
    return f"{category} {change}"


def _countable(st: dict) -> bool:
    """True when a trial's location stats are safe to report as numbers."""
    return bool(st) and not st.get("truncated") and not st.get("uncountable")


def _add_location_changes_sheet(wb, result, nct_title_map: dict) -> bool:
    """
    Insert a 'Location Changes' sheet (at index 1): ONE ROW PER TRIAL, with a
    repeating "Change N / Site N / Old Value N / New Value N" group of columns
    for each site/contact change (mirrors the "Detailed Changes" layout). The
    number of groups is padded to the trial with the most location changes, so
    every row shares the same column layout (unused groups left blank). Each
    group's cells are tinted by change type (green added / pink removed / yellow
    changed). Returns True if the sheet was created, False if no trial had a
    location/contact change.
    """
    changed = result["changed"]

    # Structured per-site data, when both snapshots provide a "Sites" sheet.
    # None for either side means this trial falls back to the packed column.
    old_sites_by_nct = result.get("sites_old")
    new_sites_by_nct = result.get("sites_new")

    # Site counting requires BOTH snapshots to use the same source. If only one
    # file has the structured "Sites" sheet the two sides aren't like-for-like,
    # so counts are withheld (the itemization still works). See _nuance_sentence.
    mixed = (old_sites_by_nct is None) != (new_sites_by_nct is None)
    result["site_source"] = ("mixed" if mixed
                             else "structured" if new_sites_by_nct is not None
                             else "legacy")

    def _recs(by_nct, nct):
        if by_nct is None:
            return None
        return by_nct.get(nct, [])

    # Build the rows first so we can skip the sheet entirely when there's nothing.
    trial_rows = []  # (nct, [location-change rows], stats)
    for nct in sorted(changed):
        o_rec = _recs(old_sites_by_nct, nct)
        n_rec = _recs(new_sites_by_nct, nct)
        loc_rows = _location_changes_for_trial(changed[nct], o_rec, n_rec)
        if loc_rows:
            trial_rows.append((nct, loc_rows,
                               _location_stats_for_trial(changed[nct], o_rec, n_rec,
                                                         mixed=mixed)))
    # Record how many trials are actually itemized here so the Change Summary
    # count matches this sheet exactly (a trial whose only location diff is the
    # derived hash or a geo-only shift yields no rows and is not counted).
    result["location_change_trials"] = len(trial_rows)
    if not trial_rows:
        return False

    max_groups = max(len(loc_rows) for _nct, loc_rows, _st in trial_rows)

    # Status columns are driven by the data: every status seen in either snapshot,
    # STATUS_ORDER first, anything unexpected appended alphabetically.
    seen_statuses = set()
    for _nct, _rows, st in trial_rows:
        if _countable(st):
            seen_statuses |= set(st["old_status"]) | set(st["new_status"])
    statuses = ([s for s in STATUS_ORDER if s in seen_statuses]
                + sorted(seen_statuses - set(STATUS_ORDER)))

    # Trials whose source blob was truncated contribute NOTHING to the rollup —
    # they are listed by NCT ID on the summary instead, so a partial number can
    # never be silently folded into a portfolio total.
    truncated_ncts = [nct for nct, _rows, st in trial_rows if st.get("truncated")]
    result["location_truncated"] = truncated_ncts

    # Portfolio-wide rollup for the Change Summary sheet.
    totals = Counter()
    for _nct, _rows, st in trial_rows:
        if not _countable(st):
            continue
        for k in ("added_total", "added_us", "added_exus", "added_unknown",
                  "removed_total", "removed_us", "removed_exus", "removed_unknown"):
            totals[k] += st[k]
        for status in statuses:
            totals[f"old::{status}"] += st["old_status"].get(status, 0)
            totals[f"new::{status}"] += st["new_status"].get(status, 0)
    result["location_totals"] = totals
    result["location_statuses"] = statuses

    # Sites with no country in the record are rare; only carry the extra columns
    # when this run actually has some, so the usual report stays narrow.
    has_unknown = any(
        _countable(st) and (st["old_unknown"] or st["new_unknown"])
        for _nct, _rows, st in trial_rows
    )
    result["location_has_unknown"] = has_unknown

    ws = wb.create_sheet("Location Changes", 1)

    COUNT_HEADERS = ["Sites Added (Total)", "Sites Added (US)", "Sites Added (ex-US)"]
    if has_unknown:
        COUNT_HEADERS.append(f"Sites Added ({UNKNOWN_REGION})")
    COUNT_HEADERS += ["Sites Removed (Total)", "Sites Removed (US)",
                      "Sites Removed (ex-US)"]
    if has_unknown:
        COUNT_HEADERS.append(f"Sites Removed ({UNKNOWN_REGION})")
    COUNT_HEADERS += [
        "Net Site Change",
        "Total Sites Before", "Total Sites After",
        "US Sites (Before → After)", "ex-US Sites (Before → After)",
    ]
    if has_unknown:
        COUNT_HEADERS.append(f"{UNKNOWN_REGION} Sites (Before → After)")
    STATUS_HEADERS = [f"{s} Sites (Before → After)" for s in statuses]
    SUMMARY_HEADER = ["Site Change Summary"]

    headers = ["NCT ID", "Trial Title"] + COUNT_HEADERS + STATUS_HEADERS + SUMMARY_HEADER
    n_lead = len(headers)          # columns before the repeating change groups

    # Excel allows 16,384 columns. A trial with more site changes than fit would
    # otherwise silently produce a corrupt workbook. Cap the ITEMIZATION only —
    # the count columns and the summary sentence are unaffected, so the reported
    # numbers stay complete and correct even when the per-site listing is clipped.
    max_fit = (EXCEL_MAX_COLS - n_lead) // 4
    clipped = max(0, max_groups - max_fit)
    if clipped:
        max_groups = max_fit
        print(f"  !  {clipped} site-change column group(s) beyond Excel's "
              f"{EXCEL_MAX_COLS:,}-column limit were omitted from the itemization "
              f"on 'Location Changes'. All counts remain complete.")
    result["location_clipped_groups"] = clipped

    for i in range(1, max_groups + 1):
        headers += [f"Change {i}", f"Site / Facility {i}",
                    f"Old Value {i}", f"New Value {i}"]
    for c, h in enumerate(headers, 1):
        _summary_cell(ws, 1, c, h, bold=True, fill=_HDR_FILL)

    ws.column_dimensions["A"].width = 14
    ws.column_dimensions["B"].width = 40
    for i in range(len(COUNT_HEADERS)):
        ws.column_dimensions[get_column_letter(3 + i)].width = 13
    for i in range(len(STATUS_HEADERS)):
        ws.column_dimensions[get_column_letter(3 + len(COUNT_HEADERS) + i)].width = 15
    ws.column_dimensions[get_column_letter(n_lead)].width = 55   # Site Change Summary
    for i in range(max_groups):
        base = n_lead + 1 + i * 4
        ws.column_dimensions[get_column_letter(base)].width = 16      # Change
        ws.column_dimensions[get_column_letter(base + 1)].width = 50  # Site / Facility
        ws.column_dimensions[get_column_letter(base + 2)].width = 30  # Old Value
        ws.column_dimensions[get_column_letter(base + 3)].width = 30  # New Value

    def _arrow(old, new):
        return f"{old} → {new}" if old != new else str(new)

    r = 2
    for nct, loc_rows, st in trial_rows:
        _summary_cell(ws, r, 1, nct)
        _summary_cell(ws, r, 2, nct_title_map.get(nct, ""), wrap=True)

        c = 3
        if _countable(st):
            # Tint the added/removed totals only when non-zero, so the eye lands
            # on the trials that actually gained or lost sites.
            vals = [
                (st["added_total"], GREEN_FILL if st["added_total"] else None),
                (st["added_us"], None), (st["added_exus"], None),
            ]
            if has_unknown:
                vals.append((st["added_unknown"], None))
            vals += [
                (st["removed_total"], REMOVED_FILL if st["removed_total"] else None),
                (st["removed_us"], None), (st["removed_exus"], None),
            ]
            if has_unknown:
                vals.append((st["removed_unknown"], None))
            vals += [
                (st["net"], None),
                (st["old_total"], None), (st["new_total"], None),
                (_arrow(st["old_us"], st["new_us"]), None),
                (_arrow(st["old_exus"], st["new_exus"]), None),
            ]
            if has_unknown:
                vals.append((_arrow(st["old_unknown"], st["new_unknown"]), None))
            for val, fill in vals:
                _summary_cell(ws, r, c, val, fill=fill); c += 1
            for s in statuses:
                _summary_cell(ws, r, c, _arrow(st["old_status"].get(s, 0),
                                               st["new_status"].get(s, 0))); c += 1
            _summary_cell(ws, r, c, _nuance_sentence(st), wrap=True); c += 1
        else:
            # No trustworthy counts for this trial. Leave every count column
            # BLANK — a 0 here would read as "no sites changed", which is a
            # different (and possibly false) claim than "we cannot tell".
            c = n_lead
            _summary_cell(
                ws, r, c,
                _nuance_sentence(st) if st
                else "Site contacts updated; no site added or removed.",
                wrap=True, fill=FETCH_ERROR_FILL if st else None,
            ); c += 1

        c = n_lead + 1
        for category, change, site, old_val, new_val in loc_rows[:max_groups]:
            fill = _CHANGE_FILL.get(change)
            _summary_cell(ws, r, c,     _change_label(category, change), fill=fill)
            _summary_cell(ws, r, c + 1, site, wrap=True, fill=fill)
            _summary_cell(ws, r, c + 2, old_val, wrap=True, fill=fill)
            _summary_cell(ws, r, c + 3, new_val, wrap=True, fill=fill)
            c += 4
        r += 1

    last_row = r - 1
    last_col = n_lead + max_groups * 4
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = f"A1:{get_column_letter(last_col)}{last_row}"
    return True


def _add_summary_sheet(wb, result, nct_start_row: dict, nct_title_map: dict):
    """Insert a 'Change Summary' sheet at position 0."""
    ws = wb.create_sheet("Change Summary", 0)
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 90
    ws.column_dimensions["C"].width = 16
    ws.column_dimensions["D"].width = 14
    ws.column_dimensions["E"].width = 14

    now = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")
    changed, new, removed = result["changed"], result["new"], result["removed"]
    fetch_errors = result.get("fetch_errors", [])
    critical_trials = [
        nct for nct, diffs in changed.items()
        if any(lbl in CRITICAL_FIELDS for lbl, _old, _new in diffs)
    ]
    # Count of trials itemized on the "Location Changes" sheet (set by
    # _add_location_changes_sheet, which runs before this sheet is built).
    location_trials = result.get("location_change_trials", 0)

    r = 1
    _summary_cell(ws, r, 1, "Clinical Trials — Change Summary", bold=True); r += 1
    _summary_cell(ws, r, 1, f"Generated: {now}"); r += 2

    _summary_cell(ws, r, 1, "OLD (previous):", bold=True)
    _summary_cell(ws, r, 2, result.get("old_name", ""), wrap=True); r += 1
    _summary_cell(ws, r, 1, "NEW (current):", bold=True)
    _summary_cell(ws, r, 2, result.get("new_name", ""), wrap=True); r += 2

    # Counts table
    _summary_cell(ws, r, 1, "Metric", bold=True, fill=_HDR_FILL)
    _summary_cell(ws, r, 2, "Count", bold=True, fill=_HDR_FILL); r += 1
    rows = [
        ("Unchanged trials", result["unchanged"]),
        ("Changed trials (yellow cells)", len(changed)),
        ("Critical field changes", len(critical_trials)),
        ("Trials with location changes", location_trials),
        ("New trials (green rows)", len(new)),
        ("Removed trials", len(removed)),
        ("Fetch errors (excluded, gray)", len(fetch_errors)),
        ("Fields compared", len(result["compared_labels"])),
    ]
    for label, val in rows:
        _summary_cell(ws, r, 1, label)
        _summary_cell(ws, r, 2, val); r += 1
    r += 1

    if location_trials and result.get("site_source") == "mixed":
        # Only one of the two files has the structured "Sites" sheet, so site
        # counts would compare a complete list against a truncated, string-parsed
        # one. Withhold the numbers entirely rather than publish a figure that
        # looks authoritative — the site-by-site itemization is still valid.
        _summary_cell(
            ws, r, 1,
            "⚠ SITE COUNTS NOT REPORTED — the two extractions store sites "
            "differently: only one has the structured 'Sites' sheet. Counting "
            "across them would not be like-for-like, so no added/removed totals "
            "are given. The site-by-site breakdown on 'Location Changes' is still "
            "accurate. For counts, compare two extractions that BOTH have a "
            "'Sites' sheet (i.e. two runs of the current script).",
            wrap=True, fill=CRITICAL_FILL,
        ); r += 2

    elif location_trials:
        totals = result.get("location_totals") or Counter()
        statuses = result.get("location_statuses") or []
        net = totals["added_total"] - totals["removed_total"]

        has_unknown = result.get("location_has_unknown")
        _summary_cell(ws, r, 1, "Clinical site changes (across all changed trials)",
                      bold=True, fill=_HDR_FILL)
        _summary_cell(ws, r, 2, "Total", bold=True, fill=_HDR_FILL)
        _summary_cell(ws, r, 3, "US", bold=True, fill=_HDR_FILL)
        _summary_cell(ws, r, 4, "ex-US", bold=True, fill=_HDR_FILL)
        if has_unknown:
            _summary_cell(ws, r, 5, UNKNOWN_REGION, bold=True, fill=_HDR_FILL)
        r += 1
        for label, key, fill in (("Sites added", "added", GREEN_FILL),
                                 ("Sites removed", "removed", REMOVED_FILL)):
            _summary_cell(ws, r, 1, label, fill=fill)
            _summary_cell(ws, r, 2, totals[f"{key}_total"])
            _summary_cell(ws, r, 3, totals[f"{key}_us"])
            _summary_cell(ws, r, 4, totals[f"{key}_exus"])
            if has_unknown:
                _summary_cell(ws, r, 5, totals[f"{key}_unknown"])
            r += 1
        _summary_cell(ws, r, 1, "Net site change", bold=True)
        _signed_cell(ws, r, 2, net, bold=True); r += 2

        if statuses:
            _summary_cell(ws, r, 1, "Sites by recruitment status", bold=True,
                          fill=_HDR_FILL)
            _summary_cell(ws, r, 2, "Before", bold=True, fill=_HDR_FILL)
            _summary_cell(ws, r, 3, "After", bold=True, fill=_HDR_FILL)
            _summary_cell(ws, r, 4, "Change", bold=True, fill=_HDR_FILL); r += 1
            for s in statuses:
                old_n, new_n = totals[f"old::{s}"], totals[f"new::{s}"]
                delta = new_n - old_n
                _summary_cell(ws, r, 1, s)
                _summary_cell(ws, r, 2, old_n)
                _summary_cell(ws, r, 3, new_n)
                _signed_cell(ws, r, 4, delta)
                r += 1
            r += 1

        clipped = result.get("location_clipped_groups") or 0
        if clipped:
            _summary_cell(
                ws, r, 1,
                f"⚠ The per-site itemization on 'Location Changes' was clipped by "
                f"{clipped} column group(s) to stay within Excel's "
                f"{EXCEL_MAX_COLS:,}-column limit. The COUNTS above and in that "
                "sheet are complete and unaffected — only the site-by-site listing "
                "is shortened for the trial(s) with the most changes.",
                wrap=True, fill=YELLOW_FILL,
            ); r += 2

        truncated = result.get("location_truncated") or []
        if truncated:
            _summary_cell(
                ws, r, 1,
                f"⚠ Site counts EXCLUDE {len(truncated)} trial(s) whose 'Locations' "
                "cell exceeded Excel's 32,767-character limit and was truncated — "
                "an unknown number of sites is missing, so no count is reported "
                "for them. Verify these on CT.gov directly:",
                wrap=True, fill=FETCH_ERROR_FILL,
            ); r += 1
            _summary_cell(ws, r, 1, ", ".join(truncated), wrap=True); r += 2

        _summary_cell(
            ws, r, 1,
            "Counts cover only trials whose site list changed in this run. A site "
            "whose status flipped (e.g. Recruiting → Withdrawn) is NOT counted as "
            "added or removed — it is an existing site and appears in the status "
            "table above. A site whose entry only changed shape (ZIP added or "
            "dropped, punctuation or spacing edited) is matched across snapshots "
            "and reported as 'Site Re-listed' rather than inflating both counts. "
            "A suspected facility RE-SPELLING is never merged automatically: both "
            "entries stay counted and the row carries a '⚠ verify' note for you to "
            "adjudicate. See the 'Location Changes' sheet for the breakdown.",
            wrap=True,
        ); r += 2

    # New NCT IDs
    _summary_cell(ws, r, 1, f"New NCT IDs ({len(new)})", bold=True, fill=GREEN_FILL); r += 1
    if new:
        _summary_cell(ws, r, 1, "NCT ID", bold=True, fill=_HDR_FILL)
        _summary_cell(ws, r, 2, "Trial Title", bold=True, fill=_HDR_FILL); r += 1
        for nct in new:
            _summary_cell(ws, r, 1, nct)
            _summary_cell(ws, r, 2, nct_title_map.get(nct, ""), wrap=True); r += 1
    else:
        _summary_cell(ws, r, 1, "(none)"); r += 1
    r += 1

    # Fetch-error NCT IDs — excluded from comparison, not real "changes"
    _summary_cell(
        ws, r, 1,
        f"Fetch errors ({len(fetch_errors)}) — excluded from comparison, re-run to retry",
        bold=True, fill=FETCH_ERROR_FILL,
    ); r += 1
    if fetch_errors:
        _summary_cell(ws, r, 1, ", ".join(fetch_errors), wrap=True); r += 1
    else:
        _summary_cell(ws, r, 1, "(none)"); r += 1
    r += 1

    # Removed NCT IDs
    _summary_cell(ws, r, 1, f"Removed NCT IDs ({len(removed)})", bold=True,
                  fill=PatternFill("solid", fgColor="FFC7CE")); r += 1
    if removed:
        _summary_cell(ws, r, 1, ", ".join(removed)); r += 1
    else:
        _summary_cell(ws, r, 1, "(none)"); r += 1
    r += 1

    # Per-trial changed fields — links into the "Detailed Changes" sheet
    _summary_cell(ws, r, 1, f"Changed trials ({len(changed)}) — click NCT ID for old/new values",
                  bold=True, fill=YELLOW_FILL); r += 1
    _summary_cell(ws, r, 1, "NCT ID", bold=True, fill=_HDR_FILL)
    _summary_cell(ws, r, 2, "Trial Title", bold=True, fill=_HDR_FILL)
    _summary_cell(ws, r, 3, "# Fields Changed (⚠ = includes critical field)", bold=True, fill=_HDR_FILL); r += 1
    if changed:
        for nct, diffs in changed.items():
            cell = _summary_cell(ws, r, 1, nct)
            target_row = nct_start_row.get(nct)
            if target_row:
                cell.hyperlink = f"#'Detailed Changes'!A{target_row}"
                cell.font = Font(name="Calibri", size=10, color="0563C1", underline="single")
            _summary_cell(ws, r, 2, nct_title_map.get(nct, ""), wrap=True)
            is_critical = any(lbl in CRITICAL_FIELDS for lbl, _old, _new in diffs)
            _summary_cell(ws, r, 3, f"{len(diffs)}{'  ⚠' if is_critical else ''}"); r += 1
    else:
        _summary_cell(ws, r, 1, "(none)"); r += 1

    ws.freeze_panes = "A2"


def _add_detail_sheet(wb, result, nct_title_map: dict) -> dict:
    """
    Insert a 'Detailed Changes' sheet: ONE ROW PER TRIAL, with a repeating
    "Field N / Old Value N / New Value N" group of columns for each changed
    field. The number of groups is padded to the trial with the most changes
    in this run, so every row has the same column layout (unused groups are
    left blank for trials with fewer changes). Cells for a CRITICAL_FIELDS
    entry are highlighted orange.

    Returns {nct_id: row_number} so callers can build working hyperlinks
    from the Change Summary sheet into this one.
    """
    ws = wb.create_sheet("Detailed Changes", 0)
    changed = result["changed"]

    # Location fields are itemized on the dedicated "Location Changes" sheet, so
    # their bulky multi-site blobs are suppressed here (see LOCATION_FIELDS).
    def _detail_diffs(diffs):
        return [d for d in diffs if d[0] not in LOCATION_FIELDS]

    max_fields = max((len(_detail_diffs(diffs)) for diffs in changed.values()),
                     default=0)

    headers = ["NCT ID", "Trial Title"]
    for i in range(1, max_fields + 1):
        headers += [f"Field {i}", f"Old Value {i}", f"New Value {i}"]
    for c, h in enumerate(headers, 1):
        _summary_cell(ws, 1, c, h, bold=True, fill=_HDR_FILL)

    ws.column_dimensions["A"].width = 14
    ws.column_dimensions["B"].width = 45
    for i in range(max_fields):
        ws.column_dimensions[get_column_letter(3 + i * 3)].width = 22   # Field
        ws.column_dimensions[get_column_letter(4 + i * 3)].width = 45   # Old Value
        ws.column_dimensions[get_column_letter(5 + i * 3)].width = 45   # New Value

    nct_start_row = {}
    r = 2
    for nct in sorted(changed):
        nct_start_row[nct] = r
        _summary_cell(ws, r, 1, nct)
        _summary_cell(ws, r, 2, nct_title_map.get(nct, ""), wrap=True)
        c = 3
        for lbl, old_val, new_val in _detail_diffs(changed[nct]):
            fill = CRITICAL_FILL if lbl in CRITICAL_FIELDS else None
            _summary_cell(ws, r, c, lbl, fill=fill)
            _summary_cell(ws, r, c + 1, old_val, wrap=True, fill=fill)
            _summary_cell(ws, r, c + 2, new_val, wrap=True, fill=fill)
            c += 3
        r += 1

    last_row = r - 1
    last_col = 2 + max_fields * 3
    if last_row >= 1:
        ws.freeze_panes = "C2"
        ws.auto_filter.ref = f"A1:{get_column_letter(last_col)}{last_row}"

    return nct_start_row


# ── Input prompts ────────────────────────────────────────────────────────────────

def _prompt_path(label: str) -> Path:
    while True:
        raw = input(f"  {label}: ").strip().strip('"').strip("'")
        if not raw:
            print("    Path cannot be empty.")
            continue
        p = Path(raw)
        if not p.exists():
            print(f"    File not found: {p}")
            continue
        return p


def _gui_pick_file(title: str) -> Path | None:
    """
    Open a native "Open file" dialog and return the chosen path.

    Returns None if no GUI is available (headless machine, tkinter missing) so
    the caller can fall back to typing a path. Raises SystemExit if the user
    actively cancels the dialog — cancelling means "I don't want to continue",
    not "ask me to type it instead".
    """
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception:
        return None

    try:
        root = tk.Tk()
    except Exception:
        # No display / no window manager — caller falls back to typed input.
        return None

    try:
        root.withdraw()
        root.attributes("-topmost", True)   # keep the dialog above the console
        # Default to CT_Exports (where extractions land) when it exists, so the
        # user usually doesn't have to navigate at all.
        initial = SCRIPT_DIR.parent / "CT_Exports"
        chosen = filedialog.askopenfilename(
            parent=root,
            title=title,
            initialdir=str(initial if initial.is_dir() else SCRIPT_DIR),
            filetypes=[("Excel files", "*.xlsx *.xlsm"), ("All files", "*.*")],
        )
    finally:
        try:
            root.destroy()
        except Exception:
            pass

    if not chosen:
        raise SystemExit("\n  Cancelled — no file selected. Nothing was changed.")
    return Path(chosen)


def _select_file(label: str, title: str) -> Path:
    """Pick a file via the GUI dialog, falling back to a typed path."""
    print(f"\n  Opening file picker: {title}")
    picked = _gui_pick_file(title)
    if picked is not None:
        print(f"    Selected: {picked}")
        return picked
    print("    (No graphical file picker available on this machine.)")
    return _prompt_path(label)


def _run_fresh_extraction() -> Path:
    """Run step 1 + step 2 in THIS folder; return the newly-created Step2 file path."""
    print("\n  Running fresh extraction (this may take a while)...\n")
    # Import here so Mode 1 doesn't require requests/the step modules.
    import time
    import ct_step1_search
    import ct_step2_enrich

    run_start = time.time()

    if not ct_step1_search.main():
        raise RuntimeError(
            "Step 1 (search) did not complete successfully — see the messages "
            "above for details. Aborting rather than comparing against stale data."
        )
    if not ct_step2_enrich.main():
        raise RuntimeError(
            "Step 2 (enrichment) did not complete successfully — see the "
            "messages above for details. Aborting rather than comparing "
            "against stale data."
        )

    # Only consider files actually written during THIS run (by mtime, not by
    # filename — filenames aren't guaranteed to sort chronologically) so a
    # failed/incomplete step can never cause a stale file to be silently
    # reused as if it were the fresh extraction.
    exports = SCRIPT_DIR.parent / "CT_Exports"
    files = [f for f in exports.glob("Step2_Final_Dataset_*.xlsx")
             if f.stat().st_mtime >= run_start]
    if not files:
        raise FileNotFoundError(
            "No Step2_Final_Dataset_*.xlsx was created during this run — "
            "extraction may have failed silently."
        )
    newest = max(files, key=lambda f: f.stat().st_mtime)
    print(f"\n  ✓  Fresh dataset created: {newest.name}")
    return newest


# ── Main ─────────────────────────────────────────────────────────────────────────

def main() -> None:
    print("\n" + "=" * 72)
    print("   CLINICAL TRIALS — CHANGE DETECTION")
    print("=" * 72)
    print("\n  Mode:")
    print("    1 = Compare two existing files")
    print("    2 = Run a fresh extraction, then compare to an old file")
    mode = input("\n  Choose mode [1/2] : ").strip()

    if mode == "2":
        new_path = _run_fresh_extraction()
        print("\n  Now choose the OLD (previous) file to compare against.")
        old_path = _select_file("OLD file path (previous extraction)",
                                "Select the OLD file (previous extraction)")
    else:
        print("\n  Select the two files to compare.")
        old_path = _select_file("OLD file path (previous extraction)",
                                "Select the OLD file (previous extraction)")
        new_path = _select_file("NEW file path (current extraction)",
                                "Select the NEW file (current extraction)")

    print(f"\n  OLD : {old_path.name}")
    print(f"  NEW : {new_path.name}")
    print(f"  Comparing sheet '{COMPARE_SHEET}' by '{KEY_COLUMN}' ...")

    # Read both
    _, old_labels, old_data, _ = read_dedup(old_path)
    _, new_labels, new_data, _ = read_dedup(new_path)
    print(f"  OLD: {len(old_data)} trial(s)  |  NEW: {len(new_data)} trial(s)")

    # Structured per-site data for exact site counting. Absent (None) on files
    # written before the "Sites" sheet existed — those fall back to parsing the
    # packed "Locations" column, which cannot count truncated trials.
    old_sites = read_sites(old_path)
    new_sites = read_sites(new_path)
    for label, s in (("OLD", old_sites), ("NEW", new_sites)):
        if s is None:
            print(f"  {label}: no '{SITES_SHEET}' sheet (older format).")
        else:
            print(f"  {label}: {sum(len(v) for v in s.values())} site row(s) "
                  f"from '{SITES_SHEET}' sheet.")
    if (old_sites is None) != (new_sites is None):
        print(f"\n  !  Only ONE file has a '{SITES_SHEET}' sheet, so site COUNTS "
              f"are withheld\n     (they would not be like-for-like). The "
              f"site-by-site breakdown is still produced.\n     For counts, "
              f"compare two extractions that both have a '{SITES_SHEET}' sheet.")

    # Compare
    result = compare(old_data, new_data, old_labels, new_labels)
    result["old_name"] = old_path.name
    result["new_name"] = new_path.name
    result["sites_old"] = old_sites
    result["sites_new"] = new_sites

    print("\n  ── Results ──")
    print(f"    Unchanged : {result['unchanged']}")
    print(f"    Changed   : {len(result['changed'])}  (cells highlighted yellow)")
    print(f"    New       : {len(result['new'])}  (rows highlighted green)")
    print(f"    Removed   : {len(result['removed'])}  (see Change Summary sheet)")
    if result["fetch_errors"]:
        print(f"    ⚠ Fetch errors : {len(result['fetch_errors'])}  (excluded from "
              f"comparison, gray rows — re-run to retry)")

    # Save reports one level UP (in the parent folder) to stay well under
    # the Windows 260-character path limit.
    reports_dir = SCRIPT_DIR.parent / "Change_Reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    # YYMMDD (not DDMMYY) so filenames sort chronologically.
    timestamp = datetime.now().strftime("%y%m%d_%H%M%S")
    out_path = reports_dir / f"Change_Report_{timestamp}.xlsx"

    print("\n  Building highlighted report ...")
    # Read source as bytes and write directly — avoids any path-string quoting
    # issues with shutil.copy on long OneDrive paths with special characters.
    out_path.write_bytes(new_path.read_bytes())
    try:
        apply_highlights(out_path, result)
    except Exception:
        # apply_highlights failing leaves out_path as a bare copy of the NEW
        # file — no Change Summary/Detailed Changes sheets, indistinguishable
        # from a real report by filename alone. Rename it out of the way and
        # write the traceback next to it so a failure is never silent or
        # mistaken for a completed (if empty) comparison.
        error_log = reports_dir / f"Change_Report_{timestamp}.ERROR.log"
        error_log.write_text(traceback.format_exc(), encoding="utf-8")
        broken_path = out_path.with_name(out_path.stem + ".INCOMPLETE" + out_path.suffix)
        try:
            out_path.replace(broken_path)
        except OSError:
            broken_path = out_path  # rename itself failed; leave it in place
        print(f"\n  ✗  Report build failed while adding the comparison sheets.")
        print(f"     Incomplete file : {broken_path}")
        print(f"     Error details   : {error_log}")
        raise

    print(f"\n  ✓  Report saved: {out_path}")
    print(f"\n       Sheet 'Change Summary'   : counts + new/removed/changed lists")
    print(f"                                  (click a NCT ID to jump to its old/new values)")
    print(f"       Sheet 'Detailed Changes' : old vs. new value for every changed field")
    if result.get("has_location_changes"):
        print(f"       Sheet 'Location Changes' : site-by-site added/removed/changed breakdown")
    print(f"       Sheet '{COMPARE_SHEET}'       : NEW data with yellow/green highlights")
    print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n  Interrupted by user.")
    except SystemExit as exc:
        # e.g. the file picker was cancelled — show the reason, don't traceback.
        if exc.code and not isinstance(exc.code, int):
            print(exc.code)
    except Exception as exc:
        import traceback
        print(f"\n\n  ✗  Unexpected error: {exc}")
        traceback.print_exc()
    finally:
        print()
        if sys.stdin.isatty():
            input("  Press Enter to close...")
        sys.exit(0)
