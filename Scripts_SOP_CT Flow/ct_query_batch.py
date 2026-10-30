#!/usr/bin/env python3
"""
ct_query_batch.py
─────────────────
Batch Clinical Trials Extractor driven by an Excel list of search queries.
Source: ClinicalTrials.gov v2 API

What it does
------------
1. Reads "Search_Queries.xlsx" (same directory) which has two columns:
       • "Asset/Trial Program"   — a label for the asset / program
       • "Asset String only"     — a boolean search query (Essie syntax)
2. Sends each query to the CT.gov API (as query.term — advanced free-text)
   and collects every matching NCT ID.
3. Enriches every UNIQUE trial once with the full 119-field detail set (every CT.gov v2 API field, including results/AE data).
4. Writes a single Excel workbook with one sheet:
       • "Deduplicated"    — one row per unique NCT ID. Where a trial was
                             found by multiple queries, the Search Query and
                             Asset/Trial Program columns are merged
                             (pipe-separated, de-duplicated).

Usage:
    python ct_query_batch.py

Output:
    CT_Exports/CT_QueryBatch_YYYYMMDD_HHMMSS.xlsx   (one level up, next to Change_Reports)

Requirements:
    pip install requests openpyxl
"""

import hashlib
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests
import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

# Make stdout UTF-8 capable so progress bars / emoji print on Windows consoles.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# ── Config ──────────────────────────────────────────────────────────────────────

QUERIES_FILE   = "Search_Queries.xlsx"
COL_ASSET      = "Asset/Trial Program"
COL_QUERY      = "Asset String only"
MAX_PER_QUERY  = 10_000      # safety cap on trials per single query
ENRICH_WORKERS = 16          # parallel workers for Phase-2 enrichment (batched, so
                              # each worker's request now returns many trials at once)
ENRICH_BATCH_SIZE = 50       # NCT IDs fetched per HTTP request via filter.ids
                              # (was 1 trial/request — this is the main speedup:
                              # N network round-trips drops to ceil(N/50))
MAX_RETRIES    = 3           # retries for transient network/API errors
RETRY_BACKOFF  = 1.5         # seconds; doubles-ish each retry (1.5, 3, 6, ...)

# Marker embedded in brief_title when a trial's enrichment permanently fails
# (after retries). Downstream tools (e.g. ct_change_detection.py) look for
# this substring to exclude the row from comparison rather than treating a
# fetch failure as a real data change.
FETCH_ERROR_MARKER = "FETCH ERROR"

# Excel's hard per-cell character limit (SpreadsheetML spec / Excel UI cap).
# openpyxl enforces this SILENTLY on save — anything longer is truncated with
# no error and no warning, which would both corrupt the visible data and
# create a change-detection blind spot (two runs truncated to the same first
# 32767 chars would compare as "unchanged" even if they differ afterward).
# See _excel_safe_text() and HASH_FIELDS below for how this is handled.
EXCEL_CELL_LIMIT = 32767
_TRUNCATION_NOTE = (
    "\n\n[TRUNCATED — exceeds Excel's 32,767-char cell limit. If this field "
    "has a paired \"... Hash\" column, that hash covers the FULL untruncated "
    "value and will still change if anything past this point differs.]"
)


# ── API constants ──────────────────────────────────────────────────────────────

CT_BASE    = "https://clinicaltrials.gov/api/v2/studies"
CT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept":     "application/json",
}
# Lightweight field set for the search pass (we only need NCT IDs here, but
# fetching a few cheap fields keeps the calls fast).
CT_SEARCH_FIELDS = "protocolSection.identificationModule.nctId"

# A single pooled Session shared by every thread. Plain `requests.get(...)`
# opens a brand-new TCP+TLS connection per call; across thousands of trials
# that handshake overhead dwarfs the actual API response time. Reusing a
# Session with a big enough pool lets keep-alive connections be reused across
# threads, which is a large, free speed win with no behavior change.
# max_retries=0 on the adapter: retry/backoff is handled by _get_with_retry
# itself so error handling stays in one place.
_SESSION = requests.Session()
_ADAPTER = requests.adapters.HTTPAdapter(
    pool_connections=ENRICH_WORKERS, pool_maxsize=ENRICH_WORKERS, max_retries=0,
)
_SESSION.mount("https://", _ADAPTER)
_SESSION.mount("http://", _ADAPTER)

PHASE_LABEL = {
    "PHASE1": "Phase 1", "PHASE2": "Phase 2", "PHASE3": "Phase 3",
    "PHASE4": "Phase 4", "EARLY_PHASE1": "Early Phase 1", "NA": "N/A",
}


# ── Utility helpers ──────────────────────────────────────────────────────────────

def _clean_query(s: str) -> str:
    """
    Remove patterns that make the CT.gov Essie parser raise
    'inner bool query clause cannot be null':
      - leading AND / OR / NOT
      - trailing AND / OR
    """
    if not s:
        return s
    s = str(s).strip()
    s = re.sub(r"^(AND|OR|NOT)\s+", "", s, flags=re.IGNORECASE).strip()
    s = re.sub(r"\s+(AND|OR)\s*$", "", s, flags=re.IGNORECASE).strip()
    return s


def _yn(val) -> str:
    return {True: "Yes", False: "No"}.get(val, "")


def long_path(path) -> str:
    """
    Return a Windows extended-length path ("\\\\?\\...") so openpyxl/zipfile
    (which don't handle long paths themselves) can still read/write files
    under deeply nested folders such as OneDrive syncs, where the plain path
    can exceed the 260-character MAX_PATH limit. No-op on non-Windows.
    """
    s = str(Path(path).resolve())
    if sys.platform == "win32" and not s.startswith("\\\\?\\"):
        return "\\\\?\\" + s
    return s


_FORMULA_PREFIXES = ("=", "+", "-", "@")


def _safe_str(value) -> str:
    """
    Stringify a cell value, neutralizing leading =,+,-,@ characters so Excel
    never interprets externally-sourced text (trial titles, descriptions,
    etc.) as a formula when the workbook is opened, and enforcing Excel's
    32,767-char cell limit EXPLICITLY (with a visible marker) rather than
    letting openpyxl truncate it silently on save (verified: openpyxl gives
    no error/warning and just cuts the string — see HASH_FIELDS for how
    change-detection stays reliable despite this).
    """
    s = str(value) if value is not None else ""
    if s and s[0] in _FORMULA_PREFIXES:
        s = "'" + s
    if len(s) > EXCEL_CELL_LIMIT:
        s = s[: EXCEL_CELL_LIMIT - len(_TRUNCATION_NOTE)] + _TRUNCATION_NOTE
    return s


def _get_with_retry(url: str, *, params=None, timeout=30, max_retries=MAX_RETRIES):
    """
    GET with retry/backoff on transient errors (timeouts, connection errors,
    429 rate-limiting, 5xx server errors). Raises the last error if every
    attempt fails. Non-transient HTTP errors (4xx other than 429) fail fast.
    """
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            resp = _SESSION.get(url, params=params, headers=CT_HEADERS, timeout=timeout)
            resp.raise_for_status()
            return resp
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
        except requests.HTTPError as exc:
            code = exc.response.status_code if exc.response is not None else None
            if code is not None and code != 429 and code < 500:
                raise  # non-transient (e.g. 404, 400) — don't retry
            last_exc = exc
        if attempt < max_retries:
            time.sleep(RETRY_BACKOFF * (attempt + 1))
    raise last_exc


# ── Read query list from Excel ───────────────────────────────────────────────────

def load_queries(path: Path) -> list:
    """
    Return a list of (asset_label, query_string) tuples from the Excel file.
    Skips the header row and any rows missing a query string.
    Raises ValueError if the expected header columns aren't found — fails
    loudly instead of silently reading the wrong columns.
    """
    wb = openpyxl.load_workbook(long_path(path), read_only=True, data_only=True)
    ws = wb.active

    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        wb.close()
        return []

    # Locate the columns by header name — fail loudly if the sheet has been
    # renamed/reordered rather than silently reading the wrong columns.
    header = [str(c).strip() if c is not None else "" for c in rows[0]]
    try:
        asset_idx = header.index(COL_ASSET)
    except ValueError:
        wb.close()
        raise ValueError(
            f"Expected column '{COL_ASSET}' not found in '{path.name}'. "
            f"Found columns: {header}"
        )
    try:
        query_idx = header.index(COL_QUERY)
    except ValueError:
        wb.close()
        raise ValueError(
            f"Expected column '{COL_QUERY}' not found in '{path.name}'. "
            f"Found columns: {header}"
        )

    out = []
    for r in rows[1:]:
        if r is None:
            continue
        asset = r[asset_idx] if asset_idx < len(r) else ""
        query = r[query_idx] if query_idx < len(r) else ""
        asset = (str(asset).strip() if asset is not None else "")
        query = (str(query).strip() if query is not None else "")
        if not query:
            continue
        out.append((asset, query))
    wb.close()
    return out


# ── Phase 1: per-query NCT ID collection ─────────────────────────────────────────

def fetch_nct_ids(query: str) -> list:
    """
    Paginate CT.gov for one query string; return list of NCT IDs.
    Each page request is retried on transient failures (timeouts, 429, 5xx);
    a persistent failure raises so the caller can decide how to report it.
    """
    nct_ids: list = []
    page_token  = ""
    q = _clean_query(query)

    while len(nct_ids) < MAX_PER_QUERY:
        params = {
            "format":     "json",
            "pageSize":   1000,
            "fields":     CT_SEARCH_FIELDS,
            "query.term": q,
        }
        if page_token:
            params["pageToken"] = page_token

        resp = _get_with_retry(CT_BASE, params=params, timeout=30)
        data = resp.json()

        for s in data.get("studies", []):
            nct = (
                s.get("protocolSection", {})
                 .get("identificationModule", {})
                 .get("nctId", "")
            )
            if nct:
                nct_ids.append(nct)

        page_token = data.get("nextPageToken", "")
        if not page_token:
            break

    if len(nct_ids) > MAX_PER_QUERY:
        print(f"  ⚠ Query hit the {MAX_PER_QUERY}-trial safety cap — results "
              f"truncated, more may exist: {query[:60]}")
    return nct_ids[:MAX_PER_QUERY]


# ── Phase 2: enrichment (full 119-field detail, incl. results/derived/document sections) ───────────────────────────────────

def _json_str(obj) -> str:
    """
    Deterministic, lossless string form of a nested JSON value for columns
    where the underlying API structure is too deeply nested to usefully
    flatten (results tables, browse trees, etc). sort_keys=True means the
    string only changes when the actual content changes, never when the API
    merely re-orders keys — so change-detection stays exact-match reliable.
    """
    if not obj:
        return ""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _content_hash(text: str) -> str:
    """
    SHA-256 of the FULL, untruncated field value (computed here, before the
    Excel-cell-limit truncation applied at write time in build_sheet()).
    A 64-char hex digest never itself risks truncation, so pairing a
    long/JSON field with its hash column guarantees change-detection catches
    edits anywhere in the value — including past character 32,767, where the
    visible cell text alone could no longer tell two versions apart.
    """
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# Fields long/nested enough to realistically exceed Excel's 32,767-char cell
# limit (huge multi-site "locations" strings, full results tables serialized
# as JSON, long free-text modules). Each gets a paired "<label> Hash" column
# in _DETAIL_COLS so a change past the visible truncation point is still
# reliably flagged by ct_change_detection.py's exact-match comparison.
HASH_FIELDS = {
    "locations", "detailed_description", "eligibility_criteria", "brief_summary",
    "large_documents",
    "results_participant_flow", "results_baseline_characteristics",
    "results_outcome_measures", "results_serious_adverse_events",
    "results_other_adverse_events", "unposted_annotation",
}


# ── Results-table flatteners ─────────────────────────────────────────────────
#
# The results tables (participantFlowModule, baselineCharacteristicsModule,
# outcomeMeasuresModule, adverseEventsModule) are deeply and irregularly
# nested — group IDs like "BG000" that only mean something once resolved
# against a separate `groups` list, multiple levels of classes/categories,
# etc. These helpers turn that into plain readable text (one line per
# measure/event, newline-separated so wrap_text renders it as a mini table
# in the cell) instead of a JSON blob.
#
# IMPORTANT: the hash used for change-detection (HASH_FIELDS, below) is
# always computed from the RAW json (via _raw_for_hash in _parse_study), not
# from this flattened text — so even if a future CT.gov schema tweak breaks
# one of these formatters or drops a field the formatter doesn't know about
# yet, the hash column still reliably catches the change. These functions
# only affect what's human-readable, never what's compared.

def _grp_lookup(groups) -> dict:
    """{groupId: title} lookup so rows can show 'Placebo=41' instead of 'BG001=41'."""
    return {g.get("id"): g.get("title", "") or g.get("id", "")
            for g in (groups or []) if g.get("id")}


def _fmt_measurement(m: dict) -> str:
    """One group's value for a measure/milestone row: '51.8±10.35', '84', etc."""
    val = m.get("value", m.get("numSubjects", ""))
    if m.get("spread"):
        return f"{val}±{m['spread']}"
    if m.get("lowerLimit") or m.get("upperLimit"):
        return f"{val} [{m.get('lowerLimit','')}-{m.get('upperLimit','')}]"
    return str(val) if val is not None else ""


def _fmt_group_values(measurements, grp_map: dict) -> str:
    return ", ".join(
        f"{grp_map.get(m.get('groupId',''), m.get('groupId',''))}={_fmt_measurement(m)}"
        for m in (measurements or [])
    )


def _fmt_classes_categories(classes, grp_map: dict) -> list:
    """Shared by baseline characteristics and outcome measures: both use the
    same classes[] -> categories[] -> measurements[] shape."""
    lines = []
    for cls in (classes or []):
        cls_title = cls.get("title", "")
        for cat in (cls.get("categories") or []):
            cat_title = cat.get("title", "")
            label = " – ".join(filter(None, [cls_title, cat_title]))
            vals = _fmt_group_values(cat.get("measurements"), grp_map)
            lines.append(f"{label}: {vals}" if label else vals)
    return lines


def _fmt_baseline(baseline_mod: dict) -> str:
    grp_map = _grp_lookup(baseline_mod.get("groups"))
    lines = []
    for meas in (baseline_mod.get("measures") or []):
        meta = " / ".join(filter(None, [
            meas.get("paramType", ""), meas.get("dispersionType", ""), meas.get("unitOfMeasure", ""),
        ]))
        cat_lines = _fmt_classes_categories(meas.get("classes"), grp_map)
        header = meas.get("title", "") + (f" ({meta})" if meta else "")
        lines.append(f"{header}: " + "; ".join(cat_lines))
    return "\n".join(lines)


def _fmt_participant_flow(flow_mod: dict) -> str:
    grp_map = _grp_lookup(flow_mod.get("groups"))
    lines = []
    if flow_mod.get("preAssignmentDetails"):
        lines.append(f"Pre-assignment: {flow_mod['preAssignmentDetails']}")
    if flow_mod.get("recruitmentDetails"):
        lines.append(f"Recruitment: {flow_mod['recruitmentDetails']}")
    for period in (flow_mod.get("periods") or []):
        ptitle = period.get("title", "")
        for ms in (period.get("milestones") or []):
            vals = _fmt_group_values(ms.get("achievements"), grp_map)
            lines.append(f"{ptitle} – {ms.get('type','')}: {vals}")
        for dw in (period.get("dropWithdraws") or []):
            vals = _fmt_group_values(dw.get("reasons"), grp_map)
            lines.append(f"{ptitle} – Not Completed ({dw.get('type','')}): {vals}")
    return "\n".join(lines)


def _fmt_outcome_measures(outcome_measures) -> str:
    lines = []
    for om in (outcome_measures or []):
        grp_map = _grp_lookup(om.get("groups"))
        meta = " / ".join(filter(None, [
            om.get("type", ""), om.get("paramType", ""), om.get("dispersionType", ""), om.get("unitOfMeasure", ""),
        ]))
        header = om.get("title", "") + (f" ({meta})" if meta else "")
        if om.get("timeFrame"):
            header += f" [{om['timeFrame']}]"
        cat_lines = _fmt_classes_categories(om.get("classes"), grp_map)
        line = header + (": " + "; ".join(cat_lines) if cat_lines else "")
        for a in (om.get("analyses") or []):
            bits = []
            if a.get("statisticalMethod"):
                bits.append(a["statisticalMethod"])
            if a.get("pValue"):
                bits.append(f"p={a['pValue']}")
            if a.get("paramType") and a.get("paramValue"):
                bits.append(f"{a['paramType']}={a['paramValue']}")
            if a.get("ciPctValue") and (a.get("ciLowerLimit") or a.get("ciUpperLimit")):
                bits.append(f"{a['ciPctValue']}% CI [{a.get('ciLowerLimit','')}, {a.get('ciUpperLimit','')}]")
            if bits:
                line += "  |  Analysis: " + ", ".join(bits)
        lines.append(line)
    return "\n".join(lines)


def _fmt_ae_stat(s: dict) -> str:
    affected, at_risk, events = s.get("numAffected", ""), s.get("numAtRisk", ""), s.get("numEvents", "")
    pct = ""
    try:
        if affected not in ("", None) and at_risk not in ("", None) and float(at_risk) > 0:
            pct = f" ({float(affected) / float(at_risk) * 100:.1f}%)"
    except (ValueError, TypeError):
        pct = ""
    out = f"{affected}/{at_risk}{pct}"
    if events and events != affected:
        out += f", {events} event(s)"
    return out


def _fmt_ae_list(events, grp_map: dict) -> str:
    lines = []
    for e in (events or []):
        header = ", ".join(filter(None, [e.get("term", ""), e.get("organSystem", "")]))
        if e.get("assessmentType"):
            header += f" [{e['assessmentType'].replace('_',' ').title()}]"
        stats = ", ".join(
            f"{grp_map.get(s.get('groupId',''), s.get('groupId',''))}={_fmt_ae_stat(s)}"
            for s in (e.get("stats") or [])
        )
        lines.append(f"{header}: {stats}")
    return "\n".join(lines)


def _fmt_groups_list(groups) -> str:
    return " | ".join(
        f"{g.get('title','')} ({g.get('id','')})" + (f": {g['description']}" if g.get("description") else "")
        for g in (groups or [])
    )


def _fmt_certain_agreement(ca: dict) -> str:
    if not ca:
        return ""
    parts = [
        f"PI/Sponsor Employee: {_yn(ca.get('piSponsorEmployee'))}",
        f"Restrictive Agreement: {_yn(ca.get('restrictiveAgreement'))}",
    ]
    if ca.get("otherDetails"):
        parts.append(f"Details: {ca['otherDetails']}")
    return "; ".join(parts)


def _fmt_unposted_annotation(annot: dict) -> str:
    if not annot:
        return ""
    parts = []
    if annot.get("unpostedResponsibleParty"):
        parts.append(f"Responsible Party: {annot['unpostedResponsibleParty']}")
    for ev in (annot.get("unpostedEvents") or []):
        date = "date unknown" if ev.get("dateUnknown") else ev.get("date", "")
        parts.append(f"{ev.get('type','')} ({date})")
    return "; ".join(parts)


def _enrich_single(nct_id: str) -> dict:
    """Fetch full detail for one trial via the single-study endpoint,
    retrying transient errors. Used as the fallback path when a trial is
    missing from a batch response (see _fetch_batch / _enrich_chunk_safe)."""
    resp = _get_with_retry(f"{CT_BASE}/{nct_id}", timeout=30)
    return _parse_study(resp.json(), nct_id)


def _fetch_batch(nct_ids: list) -> dict:
    """
    Fetch full detail for MULTIPLE trials in one HTTP request using the
    CT.gov v2 `filter.ids` parameter (documented for retrieving a specific
    set of studies by NCT ID) — this is the main enrichment speedup: what
    used to be N round-trips becomes ceil(N / ENRICH_BATCH_SIZE).
    Paginates defensively via nextPageToken in case the API ever splits a
    same-size response across pages. Returns {nct_id: row}; any ID in
    `nct_ids` that the API doesn't return is simply absent from the result —
    the caller (_enrich_chunk_safe) falls back to fetching it individually
    rather than assuming the batch was complete.
    """
    out = {}
    page_token = ""
    while True:
        params = {
            "format":     "json",
            "pageSize":   len(nct_ids),
            "filter.ids": ",".join(nct_ids),
        }
        if page_token:
            params["pageToken"] = page_token
        resp = _get_with_retry(CT_BASE, params=params, timeout=60)
        data = resp.json()
        for study in data.get("studies", []):
            nct = (
                study.get("protocolSection", {})
                     .get("identificationModule", {})
                     .get("nctId", "")
            )
            if nct:
                out[nct] = _parse_study(study, nct)
        page_token = data.get("nextPageToken", "")
        if not page_token:
            break
    return out


def _enrich_chunk_safe(chunk: list) -> dict:
    """
    Enrich one batch of NCT IDs. Tries the fast batched fetch first; any ID
    missing from the batch result (whole-batch failure, or the API silently
    dropping a since-withdrawn NCT ID) is retried individually so a slow
    fallback never turns into a silently missing trial.
    """
    try:
        results = _fetch_batch(chunk)
    except Exception:
        results = {}

    missing = [n for n in chunk if n not in results]
    for nct in missing:
        results[nct] = _enrich_single_safe(nct)
    return results


def _parse_study(study: dict, nct_id: str) -> dict:
    """
    Turn one raw CT.gov v2 study JSON object into our flat row dict. Shared
    by both the single-trial fetch (_enrich_single, used as a fallback) and
    the batched fetch (_fetch_batch, the fast path) so field extraction only
    lives in one place regardless of which HTTP call produced the JSON.
    """
    ps      = study.get("protocolSection", {})
    derived = study.get("derivedSection", {})
    results = study.get("resultsSection", {})
    docs    = study.get("documentSection", {})
    annot   = study.get("annotationSection", {})
    has_results = study.get("hasResults")

    ident     = ps.get("identificationModule", {})
    status    = ps.get("statusModule", {})
    design    = ps.get("designModule", {})
    desc      = ps.get("descriptionModule", {})
    elig      = ps.get("eligibilityModule", {})
    arms      = ps.get("armsInterventionsModule", {})
    outcomes  = ps.get("outcomesModule", {})
    sponsors  = ps.get("sponsorCollaboratorsModule", {})
    locs      = ps.get("contactsLocationsModule", {})
    conds     = ps.get("conditionsModule", {})
    oversight = ps.get("oversightModule", {})
    ipd_mod   = ps.get("ipdSharingStatementModule", {})
    refs_mod  = ps.get("referencesModule", {})

    misc_mod       = derived.get("miscInfoModule", {})
    cond_browse    = derived.get("conditionBrowseModule", {})
    interv_browse  = derived.get("interventionBrowseModule", {})
    large_doc_mod  = docs.get("largeDocumentModule", {})
    unposted_annot = annot.get("unpostedAnnotation", {})

    flow_mod     = results.get("participantFlowModule", {})
    baseline_mod = results.get("baselineCharacteristicsModule", {})
    outcome_meas_mod = results.get("outcomeMeasuresModule", {})
    ae_mod       = results.get("adverseEventsModule", {})
    more_info    = results.get("moreInfoModule", {})

    nct_id       = ident.get("nctId", nct_id)
    phases       = design.get("phases", []) or []
    design_info  = design.get("designInfo") or {}
    masking_info = design_info.get("maskingInfo") or {}

    secondary_ids_str = " | ".join(
        " ".join(filter(None, [s.get("id",""), s.get("type",""), s.get("domain","")]))
        for s in (ident.get("secondaryIdInfos") or [])
    )

    conditions_str = " | ".join(conds.get("conditions") or [])
    keywords_str   = " | ".join(conds.get("keywords") or [])
    std_ages_str   = ", ".join(elig.get("stdAges") or [])

    who_masked_str = ", ".join(
        w.replace("_", " ").title() for w in (masking_info.get("whoMasked") or [])
    )

    arm_groups_str = " | ".join(
        (f"{a.get('label','')} ({a.get('type','').replace('_',' ').title()})"
         if a.get("type") else a.get("label",""))
        + (f": {a['description']}" if a.get("description") else "")
        for a in (arms.get("armGroups") or [])
    )

    intr_parts = []
    for iv in (arms.get("interventions") or []):
        part = f"{iv.get('type','').replace('_',' ').title()}: {iv.get('name','')}"
        if iv.get("otherNames"):
            part += f" (aka {', '.join(iv['otherNames'])})"
        if iv.get("description"):
            part += f" — {iv['description']}"
        intr_parts.append(part)
    intr_str = " | ".join(intr_parts)

    _ivs = arms.get("interventions") or []
    intr_names_str = " | ".join(iv.get("name", "") for iv in _ivs if iv.get("name"))
    intr_types_str = " | ".join(
        iv.get("type", "").replace("_", " ").title() for iv in _ivs if iv.get("type")
    )
    _brand_parts = []
    for iv in _ivs:
        other = iv.get("otherNames") or []
        if other:
            _brand_parts.append(f"{iv.get('name', '')}: {', '.join(other)}")
    intr_brand_str = " | ".join(_brand_parts)

    def _fmt_outcomes(lst):
        # No slicing/limit — every outcome is included so a change to the
        # 6th+ outcome is never silently dropped from comparison.
        return " | ".join(
            f"{o.get('measure','')} [{o.get('timeFrame','')}]"
            + (f": {o['description']}" if o.get("description") else "")
            for o in (lst or [])
        )

    po_str = _fmt_outcomes(outcomes.get("primaryOutcomes"))
    so_str = _fmt_outcomes(outcomes.get("secondaryOutcomes"))
    oo_str = _fmt_outcomes(outcomes.get("otherOutcomes"))

    def _fmt_geo(l):
        geo = l.get("geoPoint") or {}
        if geo.get("lat") is not None and geo.get("lon") is not None:
            return f"({geo['lat']},{geo['lon']})"
        return ""

    loc_str = " | ".join(
        ", ".join(filter(None, [
            l.get("facility",""), l.get("city",""), l.get("state",""),
            l.get("zip",""), l.get("country",""),
            f"[{l['status'].replace('_',' ').title()}]" if l.get("status") else "",
            _fmt_geo(l),
        ]))
        for l in (locs.get("locations") or [])
    )

    loc_contacts_str = " | ".join(
        f"{l.get('facility','')}: " + "; ".join(
            ", ".join(filter(None, [c.get("name",""), c.get("phone",""), c.get("email","")]))
            for c in (l.get("contacts") or [])
        )
        for l in (locs.get("locations") or []) if l.get("contacts")
    )

    # Structured per-site records, kept ALONGSIDE the packed loc_str above.
    #
    # loc_str exists for human reading, but it is lossy and ambiguous to parse
    # back: facility names contain commas, addresses contain bracketed region
    # names that look exactly like the "[Status]" token, and a trial with 300+
    # sites overruns Excel's 32,767-char cell limit and gets truncated outright.
    # Change detection therefore counts sites from THESE records — the fields as
    # the API actually gave them — and never re-parses the string.
    site_records = [
        {
            "facility": l.get("facility", "") or "",
            "city":     l.get("city", "") or "",
            "state":    l.get("state", "") or "",
            "zip":      l.get("zip", "") or "",
            "country":  l.get("country", "") or "",
            "status":   (l.get("status", "") or "").replace("_", " ").title(),
            "geo":      _fmt_geo(l),
        }
        for l in (locs.get("locations") or [])
    ]

    # ── Derived section (CT.gov-computed MeSH terms, record vintage) ────────
    cond_mesh_str = " | ".join(
        m.get("term", "") for m in (cond_browse.get("meshes") or []) if m.get("term")
    )
    cond_mesh_ancestors_str = " | ".join(
        m.get("term", "") for m in (cond_browse.get("ancestors") or []) if m.get("term")
    )
    interv_mesh_str = " | ".join(
        m.get("term", "") for m in (interv_browse.get("meshes") or []) if m.get("term")
    )
    interv_mesh_ancestors_str = " | ".join(
        m.get("term", "") for m in (interv_browse.get("ancestors") or []) if m.get("term")
    )

    # ── Document section (uploaded protocol/SAP/ICF documents) ──────────────
    large_docs_str = " | ".join(
        ", ".join(filter(None, [
            d.get("label", ""), d.get("date", ""), d.get("filename", ""),
        ]))
        for d in (large_doc_mod.get("largeDocs") or [])
    )

    # ── Results section (only present once a trial posts results) ───────────
    ae_grp_map = _grp_lookup(ae_mod.get("eventGroups"))
    ae_event_groups_str = " | ".join(
        ", ".join(filter(None, [
            g.get("title", ""),
            f"deaths {g['deathsNumAffected']}/{g['deathsNumAtRisk']}" if g.get("deathsNumAtRisk") else "",
            f"serious {g['seriousNumAffected']}/{g['seriousNumAtRisk']}" if g.get("seriousNumAtRisk") else "",
            f"other {g['otherNumAffected']}/{g['otherNumAtRisk']}" if g.get("otherNumAtRisk") else "",
        ]))
        for g in (ae_mod.get("eventGroups") or [])
    )
    results_poc = more_info.get("pointOfContact") or {}
    results_poc_str = ", ".join(filter(None, [
        results_poc.get("title", ""), results_poc.get("organization", ""),
        results_poc.get("email", ""), results_poc.get("phone", ""),
    ]))
    certain_agreement = more_info.get("certainAgreement") or {}

    central_contacts_str = " | ".join(
        ", ".join(filter(None, [c.get("name",""), c.get("phone",""), c.get("email","")]))
        for c in (locs.get("centralContacts") or [])
    )
    officials_str = " | ".join(
        ", ".join(filter(None, [
            o.get("name",""), o.get("role","").replace("_"," ").title(), o.get("affiliation",""),
        ]))
        for o in (locs.get("overallOfficials") or [])
    )

    collab_str = " | ".join(
        f"{c.get('name','')} ({c.get('class','')})" if c.get("class") else c.get("name","")
        for c in (sponsors.get("collaborators") or [])
    )

    resp_party = sponsors.get("responsibleParty") or {}

    enroll_info  = design.get("enrollmentInfo") or {}
    enroll_count = enroll_info.get("count")
    enroll_type  = enroll_info.get("type", "")
    if enroll_count is not None and enroll_type:
        enrollment_str = f"{enroll_count} ({enroll_type.title()})"
    elif enroll_count is not None:
        enrollment_str = str(enroll_count)
    else:
        enrollment_str = ""

    ref_parts = []
    for r in (refs_mod.get("references") or []):
        part = r.get("citation") or (f"PMID:{r['pmid']}" if r.get("pmid") else "")
        if r.get("type"):
            part = f"[{r['type'].title()}] {part}"
        if part:
            ref_parts.append(part)
    ref_str = "; ".join(ref_parts)

    see_also_str = " | ".join(
        " ".join(filter(None, [s.get("label",""), s.get("url","")]))
        for s in (refs_mod.get("seeAlsoLinks") or [])
    )

    bio_spec       = design.get("bioSpec") or {}
    ipd_info_types = " | ".join(ipd_mod.get("infoTypes") or [])

    row = {
        # Identification
        "nct_id":                         nct_id,
        "brief_title":                    ident.get("briefTitle", ""),
        "official_title":                 ident.get("officialTitle", ""),
        "acronym":                        ident.get("acronym", ""),
        "org_study_id":                   (ident.get("orgStudyIdInfo") or {}).get("id", ""),
        "org_study_id_type":              (ident.get("orgStudyIdInfo") or {}).get("type", ""),
        "org_study_id_link":              (ident.get("orgStudyIdInfo") or {}).get("link", ""),
        "secondary_ids":                  secondary_ids_str,
        "nct_id_aliases":                 " | ".join(ident.get("nctIdAliases") or []),
        # Status
        "overall_status":                 status.get("overallStatus", "").replace("_", " ").title(),
        "last_known_status":              status.get("lastKnownStatus", "").replace("_", " ").title(),
        "why_stopped":                    status.get("whyStopped", ""),
        "status_verified_date":           status.get("statusVerifiedDate", ""),
        "delayed_posting":                _yn(status.get("delayedPosting")),
        "expanded_access_has":            _yn((status.get("expandedAccessInfo") or {}).get("hasExpandedAccess")),
        "expanded_access_nct_id":         (status.get("expandedAccessInfo") or {}).get("nctId", ""),
        "expanded_access_status_for_nct": (status.get("expandedAccessInfo") or {}).get("statusForNctId", ""),
        # Dates
        "start_date":                     (status.get("startDateStruct") or {}).get("date", ""),
        "start_date_type":                (status.get("startDateStruct") or {}).get("type", ""),
        "primary_completion":             (status.get("primaryCompletionDateStruct") or {}).get("date", ""),
        "primary_completion_type":        (status.get("primaryCompletionDateStruct") or {}).get("type", ""),
        "study_completion":               (status.get("completionDateStruct") or {}).get("date", ""),
        "study_completion_type":          (status.get("completionDateStruct") or {}).get("type", ""),
        "study_first_submit_date":        status.get("studyFirstSubmitDate", ""),
        "study_first_submit_qc_date":     status.get("studyFirstSubmitQcDate", ""),
        "first_posted":                   (status.get("studyFirstPostDateStruct") or {}).get("date", ""),
        "results_first_submit_date":      status.get("resultsFirstSubmitDate", ""),
        "results_first_submit_qc_date":   status.get("resultsFirstSubmitQcDate", ""),
        "results_first_posted":           (status.get("resultsFirstPostDateStruct") or {}).get("date", ""),
        "disp_first_submit_date":         status.get("dispFirstSubmitDate", ""),
        "disp_first_submit_qc_date":      status.get("dispFirstSubmitQcDate", ""),
        "disp_first_posted":              (status.get("dispFirstPostDateStruct") or {}).get("date", ""),
        "last_update_submit_date":        status.get("lastUpdateSubmitDate", ""),
        "last_updated":                   (status.get("lastUpdatePostDateStruct") or {}).get("date", ""),
        # Sponsor
        "lead_sponsor":                   (sponsors.get("leadSponsor") or {}).get("name", ""),
        "funder_class":                   (sponsors.get("leadSponsor") or {}).get("class", ""),
        "collaborators":                  collab_str,
        "responsible_party_type":         resp_party.get("type", "").replace("_", " ").title(),
        "responsible_party_investigator": resp_party.get("investigatorFullName", ""),
        "responsible_party_title":        resp_party.get("investigatorTitle", ""),
        "responsible_party_affiliation":  resp_party.get("investigatorAffiliation", ""),
        # Design
        "study_type":                     design.get("studyType", "").replace("_", " ").title(),
        "phases":                         " / ".join(PHASE_LABEL.get(p, p) for p in phases) or "N/A",
        "enrollment":                     enrollment_str,
        "allocation":                     design_info.get("allocation", "").replace("_", " ").title(),
        "intervention_model":             design_info.get("interventionModel", "").replace("_", " ").title(),
        "intervention_model_description": design_info.get("interventionModelDescription", ""),
        "primary_purpose":                design_info.get("primaryPurpose", "").replace("_", " ").title(),
        "observational_model":            design_info.get("observationalModel", "").replace("_", " ").title(),
        "time_perspective":               design_info.get("timePerspective", "").replace("_", " ").title(),
        "masking":                        masking_info.get("masking", "").replace("_", " ").title(),
        "masking_description":            masking_info.get("maskingDescription", ""),
        "who_masked":                     who_masked_str,
        "patient_registry":               _yn(design.get("patientRegistry")),
        "target_duration":                design.get("targetDuration", ""),
        "biospec_retention":              bio_spec.get("retention", "").replace("_", " ").title(),
        "biospec_description":            bio_spec.get("description", ""),
        # Oversight
        "is_fda_regulated_drug":          _yn(oversight.get("isFdaRegulatedDrug")),
        "is_fda_regulated_device":        _yn(oversight.get("isFdaRegulatedDevice")),
        "is_unapproved_device":           _yn(oversight.get("isUnapprovedDevice")),
        "has_dmc":                        _yn(oversight.get("oversightHasDmc")),
        "fdaaa801_violation":             _yn(oversight.get("fdaaa801Violation")),
        "is_ppsd":                        _yn(oversight.get("isPpsd")),
        "is_us_export":                   _yn(oversight.get("isUsExport")),
        "human_subject_review_status":    oversight.get("humanSubjectReviewStatus", ""),
        # Conditions
        "conditions":                     conditions_str,
        "keywords":                       keywords_str,
        "condition_mesh_terms":           cond_mesh_str,
        "condition_mesh_ancestors":       cond_mesh_ancestors_str,
        "intervention_mesh_terms":        interv_mesh_str,
        "intervention_mesh_ancestors":    interv_mesh_ancestors_str,
        # Arms & Interventions
        "arm_groups":                     arm_groups_str,
        "all_interventions":              intr_str,
        "intervention_names":             intr_names_str,
        "intervention_types":             intr_types_str,
        "intervention_brand_names":       intr_brand_str,
        # Outcomes
        "primary_outcomes":               po_str,
        "secondary_outcomes":             so_str,
        "other_outcomes":                 oo_str,
        # Eligibility
        "eligibility_criteria":           elig.get("eligibilityCriteria", ""),
        "sex":                            elig.get("sex", "").replace("_", " ").title(),
        "gender_based":                   _yn(elig.get("genderBased")),
        "gender_description":             elig.get("genderDescription", ""),
        "min_age":                        elig.get("minimumAge", ""),
        "max_age":                        elig.get("maximumAge", ""),
        "std_ages":                       std_ages_str,
        "healthy_volunteers":             _yn(elig.get("healthyVolunteers")),
        "study_population":               elig.get("studyPopulation", ""),
        "sampling_method":                elig.get("samplingMethod", "").replace("_", " ").title(),
        # Contacts & Locations
        "central_contacts":               central_contacts_str,
        "overall_officials":              officials_str,
        "locations":                      loc_str,
        "location_contacts":              loc_contacts_str,
        # Not a spreadsheet column — consumed by build_sites_sheet(). build_sheet
        # only writes keys listed in its `cols`, so this list is ignored there.
        "_site_records":                  site_records,
        # Descriptions
        "brief_summary":                  desc.get("briefSummary", ""),
        "detailed_description":           desc.get("detailedDescription", ""),
        # IPD Sharing
        "ipd_sharing":                    ipd_mod.get("ipdSharing", "").replace("_", " ").title(),
        "ipd_sharing_description":        ipd_mod.get("description", ""),
        "ipd_sharing_info_types":         ipd_info_types,
        "ipd_sharing_time_frame":         ipd_mod.get("timeFrame", ""),
        "ipd_sharing_access_criteria":    ipd_mod.get("accessCriteria", ""),
        "ipd_sharing_url":                ipd_mod.get("url", ""),
        # References
        "references":                     ref_str,
        "see_also_links":                 see_also_str,
        # Derived (CT.gov-computed)
        "record_version_holder":          misc_mod.get("versionHolder", ""),
        # Documents
        "large_documents":                large_docs_str,
        # Results — human-readable parse of the results tables (see the
        # "_fmt_*" helpers above); the paired "... Hash" columns below are
        # computed from the RAW json, not this text, so no data is lost even
        # if a formatter misses an edge case.
        "has_results":                    _yn(has_results),
        "results_groups":                 _fmt_groups_list(flow_mod.get("groups")),
        "results_participant_flow":       _fmt_participant_flow(flow_mod),
        "results_baseline_characteristics": _fmt_baseline(baseline_mod),
        "results_outcome_measures":       _fmt_outcome_measures(outcome_meas_mod.get("outcomeMeasures")),
        "results_adverse_event_groups":   ae_event_groups_str,
        "results_serious_adverse_events": _fmt_ae_list(ae_mod.get("seriousEvents"), ae_grp_map),
        "results_other_adverse_events":   _fmt_ae_list(ae_mod.get("otherEvents"), ae_grp_map),
        "results_adverse_events_time_frame": ae_mod.get("timeFrame", ""),
        "results_limitations":            (more_info.get("limitationsAndCaveats") or {}).get("description", ""),
        "results_certain_agreement":      _fmt_certain_agreement(certain_agreement),
        "results_point_of_contact":       results_poc_str,
        # Annotations
        "unposted_annotation":            _fmt_unposted_annotation(unposted_annot),
        "link":                           f"https://clinicaltrials.gov/study/{nct_id}",
    }

    # Companion hash columns for fields that can realistically exceed Excel's
    # 32,767-char cell limit — computed on the full RAW json (not the
    # human-readable text above) BEFORE any truncation happens at write time,
    # so change-detection never misses an edit, even one a formatter above
    # doesn't know how to render or that falls past the truncation point.
    _raw_for_hash = {
        "results_participant_flow":        _json_str(flow_mod),
        "results_baseline_characteristics": _json_str(baseline_mod),
        "results_outcome_measures":        _json_str(outcome_meas_mod.get("outcomeMeasures")),
        "results_serious_adverse_events":  _json_str(ae_mod.get("seriousEvents")),
        "results_other_adverse_events":    _json_str(ae_mod.get("otherEvents")),
        "unposted_annotation":             _json_str(unposted_annot),
    }
    for field in HASH_FIELDS:
        raw = _raw_for_hash.get(field, row.get(field, ""))
        row[f"{field}_hash"] = _content_hash(raw)

    return row


def _enrich_single_safe(nct_id: str) -> dict:
    try:
        return _enrich_single(nct_id)
    except Exception as exc:
        return {
            "nct_id":      nct_id,
            "brief_title": f"⚠ {FETCH_ERROR_MARKER}: {exc}",
            "link":        f"https://clinicaltrials.gov/study/{nct_id}",
        }


def enrich_all(nct_ids: list, max_workers: int = ENRICH_WORKERS,
               batch_size: int = ENRICH_BATCH_SIZE) -> dict:
    """
    Enrich a list of unique NCT IDs, batching ENRICH_BATCH_SIZE IDs per HTTP
    request (via _enrich_chunk_safe) and running ENRICH_WORKERS batches
    concurrently. This is the fast path: for 1,000 trials at the default
    batch size, that's ~20 requests instead of 1,000. Returns
    {nct_id: detail_dict} — same shape as before, so callers are unaffected.
    """
    total   = len(nct_ids)
    results = {}
    chunks  = [nct_ids[i:i + batch_size] for i in range(0, total, batch_size)]
    done    = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_enrich_chunk_safe, c): c for c in chunks}
        for future in as_completed(futures):
            chunk = futures[future]
            chunk_results = future.result()
            for nct in chunk:
                res = chunk_results.get(nct, {
                    "nct_id":      nct,
                    "brief_title": f"⚠ {FETCH_ERROR_MARKER}: missing from batch result",
                    "link":        f"https://clinicaltrials.gov/study/{nct}",
                })
                results[res.get("nct_id", nct)] = res
            done += len(chunk)
            pct = done * 100 // total if total else 100
            bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
            print(f"\r  [{bar}] {done}/{total}  ({pct}%)", end="", flush=True)
    print()
    return results


# ── Column definitions ───────────────────────────────────────────────────────────

# Two tracking columns prepended to every sheet
_TRACK_COLS = [
    ("Search Query",        "_search_query",   55),
    ("Asset/Trial Program", "_asset_program",  35),
]

_DETAIL_COLS = [
    ("NCT ID",                          "nct_id",                          14),
    ("Brief Title",                     "brief_title",                     45),
    ("Official Title",                  "official_title",                  50),
    ("Acronym",                         "acronym",                         14),
    ("Org Study ID",                    "org_study_id",                    20),
    ("Org Study ID Type",               "org_study_id_type",               18),
    ("Org Study ID Link",               "org_study_id_link",               35),
    ("Secondary IDs",                   "secondary_ids",                   35),
    ("NCT ID Aliases",                  "nct_id_aliases",                  20),
    ("Status",                          "overall_status",                  22),
    ("Last Known Status",               "last_known_status",               22),
    ("Why Stopped",                     "why_stopped",                     35),
    ("Status Verified Date",            "status_verified_date",            18),
    ("Delayed Posting",                 "delayed_posting",                 14),
    ("Expanded Access Available",       "expanded_access_has",             18),
    ("Expanded Access NCT ID",          "expanded_access_nct_id",          18),
    ("Expanded Access Status",          "expanded_access_status_for_nct",  20),
    ("Start Date",                      "start_date",                      14),
    ("Start Date Type",                 "start_date_type",                 14),
    ("Primary Completion",              "primary_completion",              16),
    ("Primary Completion Type",         "primary_completion_type",         16),
    ("Study Completion",                "study_completion",                16),
    ("Study Completion Type",           "study_completion_type",           16),
    ("First Submitted",                 "study_first_submit_date",         16),
    ("First Submitted (QC)",            "study_first_submit_qc_date",      16),
    ("First Posted",                    "first_posted",                    14),
    ("Results First Submitted",         "results_first_submit_date",       18),
    ("Results First Submitted (QC)",    "results_first_submit_qc_date",    18),
    ("Results First Posted",            "results_first_posted",            16),
    ("Disposition First Submitted",     "disp_first_submit_date",          18),
    ("Disposition First Submitted (QC)", "disp_first_submit_qc_date",      18),
    ("Disposition First Posted",        "disp_first_posted",               18),
    ("Last Update Submitted",           "last_update_submit_date",         18),
    ("Last Updated",                    "last_updated",                    14),
    ("Lead Sponsor",                    "lead_sponsor",                    35),
    ("Funder Class",                    "funder_class",                    16),
    ("Collaborators",                   "collaborators",                   40),
    ("Responsible Party Type",          "responsible_party_type",          22),
    ("Responsible Party Investigator",  "responsible_party_investigator",  30),
    ("Responsible Party Title",         "responsible_party_title",         28),
    ("Responsible Party Affiliation",   "responsible_party_affiliation",   35),
    ("Study Type",                      "study_type",                      18),
    ("Phase(s)",                        "phases",                          18),
    ("Enrollment",                      "enrollment",                      16),
    ("Allocation",                      "allocation",                      18),
    ("Intervention Model",              "intervention_model",              22),
    ("Intervention Model Description",  "intervention_model_description",  40),
    ("Primary Purpose",                 "primary_purpose",                 20),
    ("Observational Model",             "observational_model",             22),
    ("Time Perspective",                "time_perspective",                18),
    ("Masking",                         "masking",                         16),
    ("Masking Description",             "masking_description",             35),
    ("Who Masked",                      "who_masked",                      28),
    ("Patient Registry",                "patient_registry",                14),
    ("Target Duration",                 "target_duration",                 16),
    ("Biospecimen Retention",           "biospec_retention",               20),
    ("Biospecimen Description",         "biospec_description",             40),
    ("FDA Regulated Drug",              "is_fda_regulated_drug",           16),
    ("FDA Regulated Device",            "is_fda_regulated_device",         16),
    ("Unapproved Device",               "is_unapproved_device",            16),
    ("Has DMC",                         "has_dmc",                         12),
    ("FDAAA 801 Violation",             "fdaaa801_violation",              16),
    ("Is PPSD",                         "is_ppsd",                         12),
    ("Is US Export",                    "is_us_export",                    14),
    ("Human Subject Review Status",     "human_subject_review_status",     22),
    ("Conditions",                      "conditions",                      40),
    ("Keywords",                        "keywords",                        35),
    ("Condition MeSH Terms",            "condition_mesh_terms",            40),
    ("Condition MeSH Ancestors",        "condition_mesh_ancestors",        40),
    ("Intervention MeSH Terms",         "intervention_mesh_terms",         40),
    ("Intervention MeSH Ancestors",     "intervention_mesh_ancestors",     40),
    ("Arm Groups",                      "arm_groups",                      55),
    ("All Interventions",               "all_interventions",               55),
    ("Intervention Names",              "intervention_names",              45),
    ("Intervention Types",              "intervention_types",              30),
    ("Brand / Other Names (aka)",       "intervention_brand_names",        50),
    ("Primary Outcomes",                "primary_outcomes",                55),
    ("Secondary Outcomes",              "secondary_outcomes",              55),
    ("Other Outcomes",                  "other_outcomes",                  45),
    ("Eligibility Criteria",            "eligibility_criteria",            60),
    ("Eligibility Criteria Hash",        "eligibility_criteria_hash",       18),
    ("Sex",                             "sex",                             10),
    ("Gender Based",                    "gender_based",                    12),
    ("Gender Description",              "gender_description",              30),
    ("Min Age",                         "min_age",                         12),
    ("Max Age",                         "max_age",                         12),
    ("Std Ages",                        "std_ages",                        22),
    ("Healthy Volunteers",              "healthy_volunteers",              16),
    ("Study Population",                "study_population",                40),
    ("Sampling Method",                 "sampling_method",                 22),
    ("Central Contacts",                "central_contacts",                45),
    ("Overall Officials",               "overall_officials",               45),
    ("Locations",                       "locations",                       55),
    ("Locations Hash",                  "locations_hash",                  18),
    ("Location Contacts",               "location_contacts",               45),
    ("Brief Summary",                   "brief_summary",                   60),
    ("Brief Summary Hash",              "brief_summary_hash",              18),
    ("Detailed Description",            "detailed_description",            70),
    ("Detailed Description Hash",       "detailed_description_hash",       18),
    ("IPD Sharing",                     "ipd_sharing",                     14),
    ("IPD Sharing Description",         "ipd_sharing_description",         45),
    ("IPD Sharing Info Types",          "ipd_sharing_info_types",          30),
    ("IPD Sharing Time Frame",          "ipd_sharing_time_frame",          30),
    ("IPD Sharing Access Criteria",     "ipd_sharing_access_criteria",     45),
    ("IPD Sharing URL",                 "ipd_sharing_url",                 35),
    ("References",                      "references",                      55),
    ("See Also Links",                  "see_also_links",                  45),
    ("Record Version Holder",           "record_version_holder",           16),
    ("Large Documents",                 "large_documents",                 55),
    ("Large Documents Hash",            "large_documents_hash",            18),
    ("Has Results",                     "has_results",                     12),
    ("Results Groups",                  "results_groups",                  55),
    ("Results Participant Flow",        "results_participant_flow",        55),
    ("Results Participant Flow Hash",   "results_participant_flow_hash",   18),
    ("Results Baseline Characteristics", "results_baseline_characteristics", 55),
    ("Results Baseline Characteristics Hash", "results_baseline_characteristics_hash", 18),
    ("Results Outcome Measures",        "results_outcome_measures",        55),
    ("Results Outcome Measures Hash",   "results_outcome_measures_hash",   18),
    ("Results Adverse Event Groups",    "results_adverse_event_groups",    55),
    ("Results Serious Adverse Events",  "results_serious_adverse_events",  55),
    ("Results Serious Adverse Events Hash", "results_serious_adverse_events_hash", 18),
    ("Results Other Adverse Events",    "results_other_adverse_events",    55),
    ("Results Other Adverse Events Hash", "results_other_adverse_events_hash", 18),
    ("Results AE Time Frame",           "results_adverse_events_time_frame", 30),
    ("Results Limitations",             "results_limitations",             45),
    ("Results Certain Agreement",       "results_certain_agreement",       45),
    ("Results Point of Contact",        "results_point_of_contact",        45),
    ("Unposted Annotation",             "unposted_annotation",             45),
    ("Unposted Annotation Hash",        "unposted_annotation_hash",        18),
    ("ClinicalTrials.gov Link",         "link",                            50),
]

_WRAP_FIELDS = {
    "_search_query", "_asset_program",
    "brief_title", "official_title", "why_stopped", "intervention_model_description",
    "masking_description", "biospec_description", "conditions", "keywords",
    "condition_mesh_terms", "condition_mesh_ancestors",
    "intervention_mesh_terms", "intervention_mesh_ancestors",
    "arm_groups", "all_interventions", "intervention_names", "intervention_brand_names",
    "primary_outcomes", "secondary_outcomes", "other_outcomes", "eligibility_criteria",
    "study_population", "central_contacts", "overall_officials", "locations",
    "location_contacts",
    "brief_summary", "detailed_description", "ipd_sharing_description",
    "ipd_sharing_access_criteria", "references", "see_also_links",
    "large_documents", "results_groups", "results_participant_flow",
    "results_baseline_characteristics", "results_outcome_measures",
    "results_adverse_event_groups", "results_serious_adverse_events",
    "results_other_adverse_events", "results_limitations",
    "results_certain_agreement", "results_point_of_contact", "unposted_annotation",
}


# ── Excel styling ────────────────────────────────────────────────────────────────

_THIN        = Side(border_style="thin", color="BFBFBF")
_CELL_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_HDR_FILL    = PatternFill("solid", fgColor="D9D9D9")
_ALT_FILL    = PatternFill("solid", fgColor="F5F5F5")
_WHT_FILL    = PatternFill("solid", fgColor="FFFFFF")


def _hdr_cell(ws, row, col, value):
    c = ws.cell(row=row, column=col, value=value)
    c.font      = Font(name="Calibri", bold=True, size=10)
    c.fill      = _HDR_FILL
    c.border    = _CELL_BORDER
    c.alignment = Alignment(vertical="center", horizontal="left")
    return c


def _data_cell(ws, row, col, value, wrap=False, is_link=False, alt_row=False):
    is_url = is_link and value and str(value).startswith("http")
    cell_value = str(value) if is_url else _safe_str(value)
    c = ws.cell(row=row, column=col, value=cell_value)
    c.fill      = _ALT_FILL if alt_row else _WHT_FILL
    c.border    = _CELL_BORDER
    c.alignment = Alignment(vertical="top", wrap_text=wrap, horizontal="left")
    if is_url:
        c.hyperlink = str(value)
        c.font = Font(name="Calibri", size=10, color="0563C1", underline="single")
    else:
        c.font = Font(name="Calibri", size=10)
    return c


def build_sheet(ws, title, subtitle, rows, cols):
    """Generic table builder: title row, subtitle row, header, data."""
    ncols   = len(cols)
    hdr_row = 4

    ws.merge_cells(f"A1:{get_column_letter(ncols)}1")
    ws["A1"] = title
    ws["A1"].font      = Font(name="Calibri", bold=True, size=13)
    ws["A1"].alignment = Alignment(vertical="center", horizontal="left")
    ws.row_dimensions[1].height = 22

    ws.merge_cells(f"A2:{get_column_letter(ncols)}2")
    ws["A2"] = subtitle
    ws["A2"].font      = Font(name="Calibri", size=10, color="595959")
    ws["A2"].alignment = Alignment(vertical="center", horizontal="left")
    ws.row_dimensions[2].height = 16
    ws.row_dimensions[3].height = 5

    ws.row_dimensions[hdr_row].height = 18
    for col, (hdr, _, _) in enumerate(cols, 1):
        _hdr_cell(ws, hdr_row, col, hdr)

    for r_idx, rowdata in enumerate(rows, hdr_row + 1):
        alt = (r_idx - hdr_row) % 2 == 0
        max_lines = 1
        for c_idx, (_, field, _) in enumerate(cols, 1):
            val = rowdata.get(field, "") or ""
            _data_cell(
                ws, r_idx, c_idx, val,
                wrap=(field in _WRAP_FIELDS),
                is_link=(field == "link"),
                alt_row=alt,
            )
            if field in _WRAP_FIELDS and val:
                max_lines = max(max_lines, str(val).count("\n") + 1)
        # Results tables (participant flow, baseline characteristics, outcome
        # measures, adverse events) are now newline-separated per-measure
        # text rather than a single JSON blob, so the fixed 15pt row used for
        # single-line fields would clip them. Grow the row to fit, capped so
        # one huge adverse-event list can't blow out the whole sheet's
        # scannability — Excel's row-border double-click still auto-fits
        # further if needed.
        ws.row_dimensions[r_idx].height = min(15 * max_lines, 400)

    ws.freeze_panes = ws.cell(row=hdr_row + 1, column=1)
    ws.auto_filter.ref = f"A{hdr_row}:{get_column_letter(ncols)}{hdr_row}"
    for c_idx, (_, _, w) in enumerate(cols, 1):
        ws.column_dimensions[get_column_letter(c_idx)].width = w


# ── Sites sheet (long format: one row per clinical site) ─────────────────────────
#
# This sheet is the AUTHORITATIVE source for site counting in
# ct_change_detection. Because every field sits in its own small cell, it has
# none of the failure modes of the packed "Locations" column:
#
#   • no 32,767-char truncation, however many sites a trial has
#   • no ambiguity between a bracketed region name and the "[Status]" token
#   • no guessing the country from the last comma-separated token
#   • repeated identical sites stay as separate rows instead of collapsing
#
# Header is on row 1 (not row 4 like build_sheet) so it reads as a plain data
# table for pivots and formulas.

SITE_COLS = [
    ("NCT ID",   "nct_id",   14),
    ("Site #",   "site_no",   8),
    ("Facility", "facility", 50),
    ("City",     "city",     22),
    ("State",    "state",    20),
    ("ZIP",      "zip",      12),
    ("Country",  "country",  20),
    ("Status",   "status",   22),
    ("Geo",      "geo",      22),
]


def build_sites_sheet(ws, rows) -> int:
    """
    Write one row per clinical site across all trials in `rows`.
    Returns the number of site rows written.
    """
    for col, (hdr, _f, w) in enumerate(SITE_COLS, 1):
        _hdr_cell(ws, 1, col, hdr)
        ws.column_dimensions[get_column_letter(col)].width = w

    r = 2
    for rowdata in rows:
        nct = rowdata.get("nct_id", "") or ""
        for i, site in enumerate(rowdata.get("_site_records") or [], 1):
            rec = dict(site, nct_id=nct, site_no=i)
            alt = r % 2 == 0
            for c_idx, (_h, field, _w) in enumerate(SITE_COLS, 1):
                _data_cell(ws, r, c_idx, rec.get(field, "") or "", alt_row=alt)
            r += 1

    ws.freeze_panes = "C2"
    last = max(r - 1, 1)
    ws.auto_filter.ref = f"A1:{get_column_letter(len(SITE_COLS))}{last}"
    return r - 2


# ── Main ─────────────────────────────────────────────────────────────────────────

def main() -> bool:
    print("\n" + "=" * 72)
    print("   CLINICAL TRIALS — BATCH QUERY EXTRACTOR")
    print("   Source : ClinicalTrials.gov v2 API")
    print("=" * 72)

    script_dir   = Path(__file__).parent
    queries_path = script_dir / QUERIES_FILE
    if not queries_path.exists():
        print(f"\n  ✗  Could not find '{QUERIES_FILE}' in {script_dir}")
        return False

    try:
        queries = load_queries(queries_path)
    except ValueError as exc:
        print(f"\n  ✗  {exc}")
        return False
    if not queries:
        print(f"\n  ✗  No queries found in '{QUERIES_FILE}'.")
        return False
    print(f"\n  Loaded {len(queries)} search queries from '{QUERIES_FILE}'.")

    # ── Phase 1: collect NCT IDs per query ────────────────────────────────────
    print("\n" + "─" * 72)
    print("  PHASE 1 — SEARCH (collecting NCT IDs per query)")
    print("─" * 72)

    # matches: list of (asset, query, nct_id) in query order
    matches      = []
    unique_ncts  = []
    seen_nct     = set()

    for i, (asset, query) in enumerate(queries, 1):
        try:
            ncts = fetch_nct_ids(query)
        except requests.HTTPError as exc:
            code = exc.response.status_code if exc.response is not None else "?"
            print(f"  [{i}/{len(queries)}] ⚠ API {code} for «{asset}» — skipped")
            continue
        except Exception as exc:
            print(f"  [{i}/{len(queries)}] ⚠ error for «{asset}»: {exc} — skipped")
            continue

        for n in ncts:
            matches.append((asset, query, n))
            if n not in seen_nct:
                seen_nct.add(n)
                unique_ncts.append(n)

        print(f"  [{i}/{len(queries)}] {len(ncts):>4} trial(s)  ·  {asset[:50]}")

    print(f"\n  ✓  {len(matches)} total match rows  |  {len(unique_ncts)} unique trial(s)")

    if not unique_ncts:
        print("  No trials found across any query. Nothing to export.")
        return False

    # ── Phase 2: enrich unique trials once ────────────────────────────────────
    print("\n" + "─" * 72)
    print("  PHASE 2 — ENRICHMENT (119 fields per unique trial)")
    print("─" * 72)
    print(f"  Enriching {len(unique_ncts)} unique trials with {ENRICH_WORKERS} workers ...\n")
    detail = enrich_all(unique_ncts)
    errors = sum(1 for d in detail.values() if FETCH_ERROR_MARKER in (d.get("brief_title") or ""))
    print(f"  ✓  Done.  {len(detail) - errors} enriched  |  {errors} error(s)")
    if errors:
        print(f"     (Trials with fetch errors are marked and excluded from "
              f"change-detection comparisons — re-run to retry them.)")

    # ── Build sheet rows ──────────────────────────────────────────────────────
    # Only the deduplicated (one row per unique NCT ID) view is exported. A
    # per-query-match sheet used to be included too ("CT.gov Output"), but it
    # duplicated the same 130+ wide-text columns (Eligibility Criteria,
    # Detailed Description, Results tables, ...) for every extra query match,
    # roughly doubling file size for a handful of rows. Per-query/asset
    # attribution is preserved in the merged "_search_query"/"_asset_program"
    # columns below.
    merged_q  = {}   # nct -> list of queries (ordered, unique)
    merged_a  = {}   # nct -> list of assets  (ordered, unique)
    for asset, query, nct in matches:
        merged_q.setdefault(nct, [])
        merged_a.setdefault(nct, [])
        if query and query not in merged_q[nct]:
            merged_q[nct].append(query)
        if asset and asset not in merged_a[nct]:
            merged_a[nct].append(asset)

    dedup_rows = []
    for nct in unique_ncts:
        row = dict(detail.get(nct, {"nct_id": nct}))
        row["_search_query"]  = " | ".join(merged_q.get(nct, []))
        row["_asset_program"] = " | ".join(merged_a.get(nct, []))
        dedup_rows.append(row)

    # ── Export ────────────────────────────────────────────────────────────────
    print("\n" + "─" * 72)
    print("  EXPORT")
    print("─" * 72)

    out_dir = script_dir.parent / "CT_Exports"
    out_dir.mkdir(exist_ok=True)
    # YYMMDD (not DDMMYY) so filenames sort chronologically, both for humans
    # browsing the folder and for any code that sorts by filename.
    timestamp = datetime.now().strftime("%y%m%d_%H%M%S")
    filepath  = out_dir / f"CT_QueryBatch_{timestamp}.xlsx"

    cols = _TRACK_COLS + _DETAIL_COLS
    now  = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")

    print("  Building Excel workbook ...")
    wb  = openpyxl.Workbook()

    ws1 = wb.active
    ws1.title = "Deduplicated"
    build_sheet(
        ws1, "Clinical Trials — Deduplicated by NCT ID",
        f"Exported: {now}  |  {len(dedup_rows)} unique trial(s)",
        dedup_rows, cols,
    )

    wb.save(long_path(filepath))

    abs_path = filepath.resolve()
    print(f"\n  ✓  File saved successfully!")
    print(f"\n  📁  Location : {abs_path}")
    print(f"\n       Sheet 'Deduplicated' : {len(dedup_rows)} unique trial(s)")
    print()
    return True


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n  Interrupted by user.")
    except Exception as exc:
        import traceback
        print(f"\n\n  ✗  Unexpected error: {exc}")
        print()
        traceback.print_exc()
    finally:
        print()
        if sys.stdin.isatty():
            input("  Press Enter to close...")
        sys.exit(0)
