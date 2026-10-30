"""
Source_URL Dedup/Resolve Script
================================

Reads the "Priority" sheet of a Combined_Report_<timestamp>.xlsx produced by the
MASH CI RSS monitoring pipeline, resolves Google redirect links
(https://www.google.com/url?...&url=<REAL_URL>&...) back to the real article URL,
deduplicates rows that point at the same real article, and writes a clean output
workbook with just Update_Title, Summary_Payload, and the resolved URL.

By default the output is written to a "Deduped_Links" folder next to this
script (created automatically), named "<input file name>_Deduped.xlsx".
Pass --output to write somewhere else instead.

Usage:
    python dedupe_source_urls.py
        (double-click, or run with no arguments: prompts you to either pick
        a file or use the newest Combined_Report_*.xlsx found under this
        workflow folder's own Output_Combined folder)
    python dedupe_source_urls.py --input "path/to/Combined_Report_2026-07-09_110457.xlsx"
    python dedupe_source_urls.py --input report.xlsx --sheet Priority --output deduped.xlsx
"""

import argparse
import os
import re
import sys
import traceback
from datetime import datetime
from urllib.parse import urlparse, parse_qs, urlunsplit, urlsplit

import openpyxl
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M"

# Query parameters that identify the *campaign* rather than the article. Two URLs
# differing only in these point at the same page, so they must not survive as
# separate "unique articles".
TRACKING_PARAM_PREFIXES = ("utm_",)
TRACKING_PARAMS = {
    "fbclid", "gclid", "dclid", "msclkid", "igshid", "mc_cid", "mc_eid",
    "ref", "ref_src", "referrer", "source", "spm", "at_medium", "at_campaign",
    "cmpid", "ncid", "sh", "t", "s", "__twitter_impression", "guccounter",
}

# Near-duplicate title threshold. 0.6 collapses the same headline republished by a
# syndicating outlet while leaving genuinely different stories about the same
# company alone. NOTE: this cannot catch *translated* republications (a French
# rerun shares almost no tokens with its English original) -- those are handled
# downstream by the triage stage's New/Old Check and the mail stage's source
# denylist, not here.
TITLE_JACCARD_THRESHOLD = 0.6

# Dropped before comparing titles: too common to carry signal.
_TITLE_STOPWORDS = {
    "a", "an", "and", "as", "at", "be", "by", "for", "from", "in", "is", "it",
    "its", "of", "on", "or", "the", "to", "with", "after", "over", "new",
    "says", "said", "reports", "report", "announces", "announced", "update",
}
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def resolve_url(url):
    if not url:
        return url
    p = urlparse(url)
    if p.netloc.lower() in ("www.google.com", "google.com") and p.path.startswith("/url"):
        qs = parse_qs(p.query)
        if "url" in qs and qs["url"]:
            return qs["url"][0]
    return url


def canonical_url_key(url):
    """Grouping key for "same article". Lowercases the host, drops a leading
    'www.', strips tracking parameters and the fragment, and ignores a trailing
    slash -- so '.../story?utm_source=x' and '.../story/' collapse together.

    Only ever used as a dict key; the URL written to the output workbook is
    always the real resolved URL, untouched.
    """
    if not url:
        return ""
    try:
        parts = urlsplit(str(url))
    except ValueError:
        return str(url).strip().lower()
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    kept = []
    for key, value in sorted(parse_qs(parts.query, keep_blank_values=True).items()):
        low = key.lower()
        if low in TRACKING_PARAMS or low.startswith(TRACKING_PARAM_PREFIXES):
            continue
        for item in value:
            kept.append(f"{low}={item}")
    path = parts.path.rstrip("/").lower() or "/"
    return urlunsplit(("", host, path, "&".join(kept), ""))


def title_tokens(title):
    """Significant lowercase word set of a headline, for near-duplicate matching."""
    if not title:
        return frozenset()
    return frozenset(
        tok for tok in _TOKEN_RE.findall(str(title).lower())
        if tok not in _TITLE_STOPWORDS and len(tok) > 1
    )


def jaccard(a, b):
    if not a or not b:
        return 0.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def parse_timestamp(value):
    try:
        return datetime.strptime(str(value), TIMESTAMP_FORMAT)
    except (ValueError, TypeError):
        return None


def read_sheet(path, sheet_name):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    if sheet_name not in wb.sheetnames:
        sys.exit(f"Sheet '{sheet_name}' not found. Available: {wb.sheetnames}")
    ws = wb[sheet_name]
    rows_iter = ws.iter_rows(values_only=True)
    header = next(rows_iter)
    rows = []
    for values in rows_iter:
        if all(v is None for v in values):
            continue
        rows.append(dict(zip(header, values)))
    return header, rows


def dedupe(rows):
    """Group rows by resolved URL, keep the row with the earliest
    Extraction_Timestamp per group (first-occurrence order breaks ties)."""
    best_by_url = {}
    order_by_url = {}
    redirect_count = 0

    for idx, row in enumerate(rows):
        source_url = row.get("Source_URL")
        resolved = resolve_url(source_url)
        if resolved != source_url:
            redirect_count += 1

        candidate = (parse_timestamp(row.get("Extraction_Timestamp")), idx, row)

        if resolved not in best_by_url:
            best_by_url[resolved] = candidate
            order_by_url[resolved] = idx
            continue

        current_ts, current_idx, _ = best_by_url[resolved]
        candidate_ts = candidate[0]

        if current_ts is not None and candidate_ts is not None:
            if candidate_ts < current_ts:
                best_by_url[resolved] = candidate
        elif candidate_ts is not None and current_ts is None:
            best_by_url[resolved] = candidate
        # else: both unparseable, or candidate is None -> keep first-seen (already stored)

    # preserve first-occurrence order of unique resolved URLs in the output
    ordered_urls = sorted(order_by_url, key=lambda u: order_by_url[u])
    unique_rows = [(url, best_by_url[url][2]) for url in ordered_urls]
    return unique_rows, redirect_count


def write_output(path, unique_rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Deduplicated_Links"

    header = ["Update_Title", "Summary_Payload", "Resolved_URL"]
    ws.append(header)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws.freeze_panes = "A2"

    for resolved_url, row in unique_rows:
        ws.append([row.get("Update_Title"), row.get("Summary_Payload"), resolved_url])

    ws.auto_filter.ref = ws.dimensions
    ws.column_dimensions[get_column_letter(1)].width = 60
    ws.column_dimensions[get_column_letter(2)].width = 80
    ws.column_dimensions[get_column_letter(3)].width = 70

    wb.save(path)


def find_default_input():
    """Search this workflow's own folder tree for Output_Combined folders and
    return the most recently modified Combined_Report_*.xlsx (excluding our
    own output files), so double-clicking this script with no arguments
    still works. Scoped to this self-contained workflow folder only (one
    level up from Scripts) -- does not look outside it."""
    workflow_root = os.path.dirname(SCRIPT_DIR)  # .../EndtoEnd_9 July workflow
    candidates = []
    for root, _dirs, files in os.walk(workflow_root):
        if os.path.basename(root) != "Output_Combined":
            continue
        for name in files:
            if name.startswith("Combined_Report_") and name.endswith(".xlsx") and "_Deduped" not in name:
                candidates.append(os.path.join(root, name))
    if not candidates:
        return None
    candidates.sort(key=os.path.getmtime, reverse=True)
    return candidates[0]


def pause_if_interactive():
    if os.environ.get("ENDTOEND_ORCHESTRATED"):
        return
    if sys.stdin.isatty():
        try:
            input("\nPress Enter to close this window...")
        except EOFError:
            pass


def browse_for_file():
    """Open a native file-picker dialog. Falls back to typed path entry if
    tkinter isn't available (e.g. a minimal Python install)."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError:
        typed = input("Enter the full path to the Combined_Report xlsx file: ").strip().strip('"')
        return typed or None

    initial_dir = os.path.dirname(find_default_input() or SCRIPT_DIR)
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    path = filedialog.askopenfilename(
        title="Select Combined_Report xlsx file",
        initialdir=initial_dir,
        filetypes=[("Excel files", "*.xlsx")],
    )
    root.destroy()
    return path or None


def prompt_for_input():
    print("No --input provided. What would you like to do?")
    print("  1) Choose a Combined_Report file")
    print("  2) Use the latest file in the Output_Combined folder")
    while True:
        choice = input("Select an option (1 or 2): ").strip()
        if choice == "1":
            path = browse_for_file()
            if path:
                return path
            print("No file selected.")
        elif choice == "2":
            latest = find_default_input()
            if latest:
                return latest
            print("No Combined_Report_*.xlsx found automatically under Output_Combined.")
        else:
            print("Please enter 1 or 2.")


def main():
    parser = argparse.ArgumentParser(description="Resolve Google redirect URLs and dedupe the Priority sheet")
    parser.add_argument("--input", default=None, help="Path to Combined_Report xlsx (prompted if omitted)")
    parser.add_argument("--sheet", default="Priority", help="Sheet name to process")
    parser.add_argument("--output", default=None, help="Output xlsx path")
    args = parser.parse_args()

    if not args.input:
        if sys.stdin.isatty():
            args.input = prompt_for_input()
        else:
            args.input = find_default_input()
            if not args.input:
                sys.exit(
                    "No --input given and no Combined_Report_*.xlsx found under any "
                    "Output_Combined folder in RSS_Alerts.\n"
                    'Run again with: python dedupe_source_urls.py --input "path\\to\\Combined_Report.xlsx"'
                )
            print(f"No --input given; auto-detected newest report: {args.input}")

    if not os.path.exists(args.input):
        sys.exit(f"Input file not found: {args.input}")

    if args.output:
        output_path = args.output
    else:
        output_dir = os.path.join(os.path.dirname(SCRIPT_DIR), "Deduped_Links")
        os.makedirs(output_dir, exist_ok=True)
        base, ext = os.path.splitext(os.path.basename(args.input))
        output_path = os.path.join(output_dir, f"{base}_Deduped{ext}")

    print(f"Reading '{args.sheet}' from {args.input} ...")
    _, rows = read_sheet(args.input, args.sheet)
    print(f"Loaded {len(rows)} rows.")

    unique_rows, redirect_count = dedupe(rows)

    print(f"Google-redirect URLs resolved: {redirect_count}")
    print(f"Direct (non-redirect) URLs: {len(rows) - redirect_count}")
    print(f"Unique articles after dedup: {len(unique_rows)}")
    print(f"Duplicate rows dropped: {len(rows) - len(unique_rows)}")

    print(f"Writing output workbook to {output_path} ...")
    try:
        write_output(output_path, unique_rows)
    except PermissionError:
        sys.exit(
            f"Could not write to {output_path} -- it looks like the file is "
            "currently open in Excel. Close it and run this again."
        )
    print("Done.")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        pause_if_interactive()
        raise
    except Exception:
        traceback.print_exc()
        pause_if_interactive()
        sys.exit(1)
    else:
        pause_if_interactive()
