"""
Check_Feed_Health.py
---------------------
Health-check for every RSS/Atom feed across all 5 OPML files, without doing
a full ingestion run (no date filter, no Excel report of article content).

For each feed it reports:
    OK        - reachable, parsed, and returned >= 1 entries
    EMPTY     - reachable and parsed but returned 0 entries
    FAILED    - unreachable / bad XML / blocked (see rss_opml_common._fetch_one
                for the exact failure taxonomy: DEAD URL, TIMEOUT, CONN ERR,
                HTTP xxx, BOZO, ERROR)

Uses the exact same fetch path as the real pipelines (_fetch_one in
rss_opml_common.py) -- same proxy, headers, retries, curl_cffi rescue -- so a
feed marked OK here will also work in Run_All_RSS.py, and a feed marked
FAILED here is a real problem, not a difference in fetch method.

Usage:
    python Check_Feed_Health.py            (checks all 5 OPML files)
    python Check_Feed_Health.py 1          (ClinicalTrials.gov only)
    python Check_Feed_Health.py 1 3        (ClinicalTrials.gov + Company Alerts)

Output:
    ..\Output_HealthCheck\FeedHealth_Report_YYYY-MM-DD_HHmmss.xlsx
        Sheet "Summary"      - one row per OPML source with OK/EMPTY/FAILED counts
        Sheet "All_Feeds"    - one row per feed with its status
        Sheet "Failures"     - only the failed feeds, with the failure detail
"""

import os
import sys
import datetime

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

os.environ.setdefault("RSS_USE_WININET_PROXY", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
from rss_opml_common import parse_opml, _fetch_one, _autofit

SOURCES = [
    {"label": "ClinicalTrials.gov",  "opml": os.path.join(_ROOT, "OPML Files", "RSS_ClinicalTrials.opml"),    "max_workers": 12},
    {"label": "Google Alerts",       "opml": os.path.join(_ROOT, "OPML Files", "RSS_GoogleAlerts.opml"),      "max_workers": 10},
    {"label": "Company Alerts",      "opml": os.path.join(_ROOT, "OPML Files", "RSS_CompanyAlerts.opml"),     "max_workers": 8},
    {"label": "Media Aggregators",   "opml": os.path.join(_ROOT, "OPML Files", "RSS_MediaAggregators.opml"),  "max_workers": 10},
    {"label": "Regulatory Alerts",   "opml": os.path.join(_ROOT, "OPML Files", "RSS_RegulatoryAlerts.opml"),  "max_workers": 6},
]

OUTPUT_PREFIX = os.path.join(_ROOT, "Output_HealthCheck", "FeedHealth_Report")


def check_source(source: dict) -> list:
    """Fetch every feed in one OPML file and return a list of result rows."""
    label = source["label"]
    feeds = parse_opml(source["opml"])
    print(f"\n{'='*85}")
    print(f"  {label}  --  {len(feeds)} feeds  (source: {os.path.basename(source['opml'])})")
    print(f"{'='*85}")

    rows = []
    with ThreadPoolExecutor(max_workers=source["max_workers"]) as pool:
        futures = {pool.submit(_fetch_one, f): f for f in feeds}
        done = 0
        for future in as_completed(futures):
            done += 1
            feed = futures[future]
            entries, failure = future.result()

            if failure:
                status, detail = failure["status"], failure["detail"]
            elif not entries:
                status, detail = "EMPTY", "0 entries returned"
            else:
                status, detail = "OK", f"{len(entries)} entries"

            rows.append({
                "Source":           label,
                "Target":           feed["Target"],
                "Type":             feed["Type"],
                "Feed_Description": feed["Feed_Description"][:150],
                "Status":           status,
                "Detail":           detail,
                "URL":              feed["URL"],
            })

            marker = "OK   " if status == "OK" else ("EMPTY" if status == "EMPTY" else "FAIL ")
            print(f"  [{done:03d}/{len(feeds)}] [{marker}] {feed['Target'][:50]:<50}  {status}")

    return rows


if __name__ == "__main__":
    choice_args = [a for a in sys.argv[1:] if a.isdigit()]
    if choice_args:
        idxs = [int(a) - 1 for a in choice_args if 1 <= int(a) <= len(SOURCES)]
        selected = [SOURCES[i] for i in idxs] if idxs else SOURCES
    else:
        selected = SOURCES

    print("=" * 85)
    print("  RSS FEED HEALTH CHECK")
    print("  Checking connectivity + parseability for every feed (no content report)")
    print("=" * 85)
    print("  Sources to check:")
    for s in selected:
        print(f"    - {s['label']}")

    t0 = datetime.datetime.now()
    all_rows = []
    for source in selected:
        all_rows.extend(check_source(source))
    elapsed = (datetime.datetime.now() - t0).seconds

    df = pd.DataFrame(all_rows)

    summary_rows = []
    for s in selected:
        sub = df[df["Source"] == s["label"]]
        summary_rows.append({
            "Source":   s["label"],
            "Total":    len(sub),
            "OK":       (sub["Status"] == "OK").sum(),
            "EMPTY":    (sub["Status"] == "EMPTY").sum(),
            "FAILED":   (~sub["Status"].isin(["OK", "EMPTY"])).sum(),
        })
    summary_df = pd.DataFrame(summary_rows)

    total     = len(df)
    total_ok  = (df["Status"] == "OK").sum()
    total_bad = total - total_ok

    print("\n" + "=" * 85)
    print("  HEALTH CHECK SUMMARY")
    print("=" * 85)
    for r in summary_rows:
        print(f"  {r['Source']:<22} Total:{r['Total']:<5} OK:{r['OK']:<5} EMPTY:{r['EMPTY']:<5} FAILED:{r['FAILED']:<5}")
    print("-" * 85)
    print(f"  TOTAL: {total} feeds checked  |  {total_ok} healthy  |  {total_bad} not healthy")
    print(f"  Time elapsed: {elapsed}s")
    print("=" * 85)

    os.makedirs(os.path.dirname(OUTPUT_PREFIX), exist_ok=True)
    date_tag = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out_path = f"{OUTPUT_PREFIX}_{date_tag}.xlsx"

    failures_df = df[~df["Status"].isin(["OK"])].copy() if not df.empty else df

    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        summary_df.to_excel(writer, index=False, sheet_name="Summary")
        _autofit(writer.sheets["Summary"])

        df.to_excel(writer, index=False, sheet_name="All_Feeds")
        _autofit(writer.sheets["All_Feeds"])

        failures_df.to_excel(writer, index=False, sheet_name="Failures")
        _autofit(writer.sheets["Failures"])

    print(f"\n[SUCCESS] Health report written -> '{os.path.abspath(out_path)}'")

    input("\nPress Enter to close...")
