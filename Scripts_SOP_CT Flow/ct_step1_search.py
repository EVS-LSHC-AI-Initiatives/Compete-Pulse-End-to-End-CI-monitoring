#!/usr/bin/env python3
"""
ct_step1_search.py
──────────────────
STEP 1 of 2 — Collect NCT IDs from ClinicalTrials.gov for every search query.

Reads "Search_Queries.xlsx" (columns: "Asset/Trial Program", "Asset String only"),
sends each query to the CT.gov v2 API (as query.term), and saves every matching
NCT ID together with the query / asset that found it.

Output (one level up, next to Change_Reports):
    CT_Exports/Step1_NCT_IDs_YYYYMMDD_HHMMSS.xlsx
        • Sheet "All Matches"  — one row per (query -> trial). Duplicates expected.
        • Sheet "Deduplicated" — one row per unique NCT ID, queries/assets merged.

Review this file, then run  ct_step2_enrich.py  to fetch the full 119-field detail set
for the deduplicated NCT IDs only.

Usage:
    python ct_step1_search.py

Requirements:
    pip install requests openpyxl
"""

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

# Number of search queries to run concurrently.
SEARCH_WORKERS = 10

import requests
import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# Reuse the loader / fetcher already defined in the batch module.
from ct_query_batch import load_queries, fetch_nct_ids, QUERIES_FILE, long_path, _safe_str


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


# (header, key, width, wrap, is_link)
_ALL_COLS = [
    ("Asset/Trial Program", "asset",  35, True,  False),
    ("Search Query",        "query",  60, True,  False),
    ("NCT ID",              "nct_id", 14, False, False),
    ("ClinicalTrials.gov Link", "link", 50, False, True),
]
_DEDUP_COLS = [
    ("NCT ID",              "nct_id", 14, False, False),
    ("Asset/Trial Program", "asset",  40, True,  False),
    ("Search Query",        "query",  70, True,  False),
    ("ClinicalTrials.gov Link", "link", 50, False, True),
]


def build_sheet(ws, title, subtitle, rows, cols):
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
    for col, (hdr, _, _, _, _) in enumerate(cols, 1):
        _hdr_cell(ws, hdr_row, col, hdr)

    for r_idx, rowdata in enumerate(rows, hdr_row + 1):
        alt = (r_idx - hdr_row) % 2 == 0
        for c_idx, (_, key, _, wrap, is_link) in enumerate(cols, 1):
            _data_cell(ws, r_idx, c_idx, rowdata.get(key, ""),
                       wrap=wrap, is_link=is_link, alt_row=alt)
        ws.row_dimensions[r_idx].height = 15

    ws.freeze_panes = ws.cell(row=hdr_row + 1, column=1)
    ws.auto_filter.ref = f"A{hdr_row}:{get_column_letter(ncols)}{hdr_row}"
    for c_idx, (_, _, w, _, _) in enumerate(cols, 1):
        ws.column_dimensions[get_column_letter(c_idx)].width = w


def main() -> bool:
    print("\n" + "=" * 72)
    print("   STEP 1 — COLLECT NCT IDs FROM CLINICALTRIALS.GOV")
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
    print(f"\n  Loaded {len(queries)} search queries.")

    print("\n" + "─" * 72)
    print("  SEARCHING")
    print("─" * 72)

    matches     = []          # list of dict rows for "All Matches"
    unique_ncts = []          # ordered unique NCT IDs
    seen        = set()
    merged_q    = {}          # nct -> [queries]
    merged_a    = {}          # nct -> [assets]

    # ── Fetch all queries in parallel ─────────────────────────────────────────
    print(f"  Running {len(queries)} queries with {SEARCH_WORKERS} workers ...\n")
    results = [None] * len(queries)   # index -> list of NCT IDs (or [] on error)

    def _fetch(idx, asset, query):
        try:
            return idx, fetch_nct_ids(query), None
        except requests.HTTPError as exc:
            code = exc.response.status_code if exc.response is not None else "?"
            return idx, [], f"API {code}"
        except Exception as exc:
            return idx, [], str(exc)

    done = 0
    with ThreadPoolExecutor(max_workers=SEARCH_WORKERS) as pool:
        futures = [pool.submit(_fetch, i, a, q)
                   for i, (a, q) in enumerate(queries)]
        for fut in as_completed(futures):
            idx, ncts, err = fut.result()
            results[idx] = ncts
            done += 1
            asset = queries[idx][0]
            if err:
                print(f"  [{done}/{len(queries)}] ⚠ {err} for «{asset[:45]}» — skipped")
            else:
                print(f"  [{done}/{len(queries)}] {len(ncts):>4} trial(s)  ·  {asset[:45]}")

    # ── Assemble in original query order (deterministic output) ────────────────
    for idx, (asset, query) in enumerate(queries):
        for n in (results[idx] or []):
            matches.append({
                "asset": asset, "query": query, "nct_id": n,
                "link":  f"https://clinicaltrials.gov/study/{n}",
            })
            if n not in seen:
                seen.add(n)
                unique_ncts.append(n)
            merged_q.setdefault(n, [])
            merged_a.setdefault(n, [])
            if query and query not in merged_q[n]:
                merged_q[n].append(query)
            if asset and asset not in merged_a[n]:
                merged_a[n].append(asset)

    print(f"\n  ✓  {len(matches)} match rows  |  {len(unique_ncts)} unique trial(s)")

    if not unique_ncts:
        print("  No trials found. Nothing to save.")
        return False

    dedup_rows = [{
        "nct_id": n,
        "asset":  " | ".join(merged_a.get(n, [])),
        "query":  " | ".join(merged_q.get(n, [])),
        "link":   f"https://clinicaltrials.gov/study/{n}",
    } for n in unique_ncts]

    # ── Export ────────────────────────────────────────────────────────────────
    out_dir = script_dir.parent / "CT_Exports"
    out_dir.mkdir(exist_ok=True)
    # YYMMDD (not DDMMYY) so filenames sort chronologically.
    timestamp = datetime.now().strftime("%y%m%d_%H%M%S")
    filepath  = out_dir / f"Step1_NCT_IDs_{timestamp}.xlsx"
    now = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")

    wb  = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "All Matches"
    build_sheet(ws1, "Step 1 — All Query Matches",
                f"Exported: {now}  |  {len(matches)} rows across {len(queries)} queries",
                matches, _ALL_COLS)

    ws2 = wb.create_sheet("Deduplicated")
    build_sheet(ws2, "Step 1 — Deduplicated NCT IDs",
                f"Exported: {now}  |  {len(dedup_rows)} unique trial(s)",
                dedup_rows, _DEDUP_COLS)

    wb.save(long_path(filepath))
    print(f"\n  ✓  Saved: {filepath.resolve()}")
    print(f"\n       Sheet 'All Matches'  : {len(matches)} rows")
    print(f"       Sheet 'Deduplicated' : {len(dedup_rows)} unique trial(s)")
    print("\n  Next: run  ct_step2_enrich.py  to fetch the full 119-field detail set.")
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
        traceback.print_exc()
    finally:
        print()
        if sys.stdin.isatty():
            input("  Press Enter to close...")
        sys.exit(0)
