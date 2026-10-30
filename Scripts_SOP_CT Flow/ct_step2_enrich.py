#!/usr/bin/env python3
"""
ct_step2_enrich.py
──────────────────
STEP 2 of 2 — Enrich the deduplicated NCT IDs with the full 119-field detail set.

Reads the most recent  CT_Exports/Step1_NCT_IDs_*.xlsx  produced by
ct_step1_search.py (or a file path passed on the command line), enriches ONLY
the unique (deduplicated) NCT IDs once via the CT.gov v2 API, and writes the
final dataset.

Output (one level up, next to Change_Reports):
    CT_Exports/Step2_Final_Dataset_YYYYMMDD_HHMMSS.xlsx
        • Sheet "Deduplicated" — one row per unique NCT ID, enriched,
                                 with queries / assets merged.
        • Sheet "Sites"        — one row per clinical site (structured).

Usage:
    python ct_step2_enrich.py                 # uses latest Step1 file
    python ct_step2_enrich.py path/to/Step1_NCT_IDs_xxx.xlsx

Requirements:
    pip install requests openpyxl
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

import openpyxl

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# Reuse enrichment + Excel building from the batch module.
from ct_query_batch import (
    enrich_all, build_sheet, build_sites_sheet, _TRACK_COLS, _DETAIL_COLS,
    long_path, FETCH_ERROR_MARKER,
)


def find_latest_step1(out_dir: Path) -> Path | None:
    """
    Return the most recently MODIFIED Step1 file (by mtime, not filename).
    Filenames encode a DDMMYY-style timestamp for some older files, which
    does not sort chronologically across month boundaries — mtime is the
    only reliable source of truth for "latest".
    """
    files = list(out_dir.glob("Step1_NCT_IDs_*.xlsx"))
    return max(files, key=lambda f: f.stat().st_mtime) if files else None


def read_matches(path: Path):
    """
    Read the 'All Matches' sheet from a Step 1 file.
    Returns (matches, unique_ncts, merged_q, merged_a).
      matches      : list of (asset, query, nct_id) in original order
      unique_ncts  : ordered list of unique NCT IDs
      merged_q/a   : nct -> ordered unique list of queries / assets
    Raises ValueError if the expected header row / columns aren't found —
    fails loudly instead of silently returning empty results.
    """
    wb = openpyxl.load_workbook(long_path(path), read_only=True, data_only=True)
    if "All Matches" in wb.sheetnames:
        ws = wb["All Matches"]
    else:
        ws = wb.active

    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    # Header is on row 4 (rows[3]); data starts row 5.
    header_idx = None
    for i, r in enumerate(rows):
        if r and "NCT ID" in [str(c).strip() if c is not None else "" for c in r]:
            header_idx = i
            break
    if header_idx is None:
        raise ValueError(
            f"Could not find an 'NCT ID' header in the '{ws.title}' sheet of "
            f"'{path.name}'."
        )

    header = [str(c).strip() if c is not None else "" for c in rows[header_idx]]
    def col(name):
        try:
            return header.index(name)
        except ValueError:
            raise ValueError(
                f"Expected column '{name}' not found in the '{ws.title}' sheet "
                f"of '{path.name}'. Found columns: {header}"
            )
    a_i = col("Asset/Trial Program")
    q_i = col("Search Query")
    n_i = col("NCT ID")

    matches, unique, seen = [], [], set()
    merged_q, merged_a = {}, {}
    for r in rows[header_idx + 1:]:
        if not r:
            continue
        nct = str(r[n_i]).strip() if n_i < len(r) and r[n_i] else ""
        if not nct:
            continue
        asset = str(r[a_i]).strip() if a_i < len(r) and r[a_i] else ""
        query = str(r[q_i]).strip() if q_i < len(r) and r[q_i] else ""
        matches.append((asset, query, nct))
        if nct not in seen:
            seen.add(nct)
            unique.append(nct)
        merged_q.setdefault(nct, [])
        merged_a.setdefault(nct, [])
        if query and query not in merged_q[nct]:
            merged_q[nct].append(query)
        if asset and asset not in merged_a[nct]:
            merged_a[nct].append(asset)

    return matches, unique, merged_q, merged_a


def main() -> bool:
    print("\n" + "=" * 72)
    print("   STEP 2 — ENRICH DEDUPLICATED NCT IDs (119 fields)")
    print("=" * 72)

    script_dir = Path(__file__).parent
    out_dir    = script_dir.parent / "CT_Exports"

    # Locate the Step 1 input file
    if len(sys.argv) > 1:
        step1 = Path(sys.argv[1])
    else:
        step1 = find_latest_step1(out_dir)

    if not step1 or not step1.exists():
        print("\n  ✗  No Step 1 file found.")
        print("     Run ct_step1_search.py first, or pass a path:")
        print("        python ct_step2_enrich.py path/to/Step1_NCT_IDs_xxx.xlsx")
        return False

    print(f"\n  Reading: {step1.name}")
    try:
        matches, unique_ncts, merged_q, merged_a = read_matches(step1)
    except ValueError as exc:
        print(f"\n  ✗  {exc}")
        return False
    if not unique_ncts:
        print("  ✗  No NCT IDs found in the Step 1 file.")
        return False
    print(f"  {len(matches)} match rows  |  {len(unique_ncts)} unique trial(s) to enrich.")

    # ── Enrich unique trials only ─────────────────────────────────────────────
    print("\n" + "─" * 72)
    print("  ENRICHING")
    print("─" * 72 + "\n")
    detail = enrich_all(unique_ncts)
    errors = sum(1 for d in detail.values() if FETCH_ERROR_MARKER in (d.get("brief_title") or ""))
    print(f"  ✓  Done.  {len(detail) - errors} enriched  |  {errors} error(s)")
    if errors:
        print(f"     (Trials with fetch errors are marked and excluded from "
              f"change-detection comparisons — re-run to retry them.)")

    # ── Build rows ────────────────────────────────────────────────────────────
    # Only the deduplicated (one row per unique NCT ID) view is exported. A
    # per-query-match sheet used to be included too ("CT.gov Output"), but it
    # duplicated the same 130+ wide-text columns (Eligibility Criteria,
    # Detailed Description, Results tables, ...) for every extra query match,
    # roughly doubling file size for a handful of rows. Per-query/asset
    # attribution is preserved in the merged "_search_query"/"_asset_program"
    # columns below.
    dedup_rows = []
    for nct in unique_ncts:
        row = dict(detail.get(nct, {"nct_id": nct}))
        row["_search_query"]  = " | ".join(merged_q.get(nct, []))
        row["_asset_program"] = " | ".join(merged_a.get(nct, []))
        dedup_rows.append(row)

    # ── Export ────────────────────────────────────────────────────────────────
    out_dir.mkdir(exist_ok=True)
    # YYMMDD (not DDMMYY) so filenames sort chronologically.
    timestamp = datetime.now().strftime("%y%m%d_%H%M%S")
    filepath  = out_dir / f"Step2_Final_Dataset_{timestamp}.xlsx"
    cols = _TRACK_COLS + _DETAIL_COLS
    now  = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")

    print("\n  Building Excel workbook ...")
    wb  = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "Deduplicated"
    build_sheet(ws1, "Clinical Trials — Deduplicated by NCT ID",
                f"Exported: {now}  |  {len(dedup_rows)} unique trial(s)",
                dedup_rows, cols)

    # One row per clinical site, built from the API's structured location
    # objects. ct_change_detection counts sites from this sheet rather than
    # re-parsing the packed "Locations" column, which is lossy and truncates at
    # Excel's 32,767-char cell limit on large trials.
    ws2 = wb.create_sheet("Sites")
    n_sites = build_sites_sheet(ws2, dedup_rows)

    wb.save(long_path(filepath))
    print(f"\n  ✓  Saved: {filepath.resolve()}")
    print(f"\n       Sheet 'Deduplicated' : {len(dedup_rows)} unique trial(s)")
    print(f"       Sheet 'Sites'        : {n_sites} clinical site row(s)")
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
