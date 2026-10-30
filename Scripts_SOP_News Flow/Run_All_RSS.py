"""
Run_All_RSS.py
--------------
Interactive launcher -- choose which pipelines to run, enter date filter once.

Pipelines:
  [1]  ClinicalTrials.gov    -- competitor + indication CT feeds
  [2]  Google Alerts         -- competitor + indication/MOA/NIT Google Alert feeds
  [3]  Company Alerts        -- company-based Google alerts + company IR/PR RSS feeds
  [4]  Media Aggregators     -- trade pubs, wire services, PubMed, MoA alerts
  [5]  Regulatory Alerts     -- US FDA, EU EMA, Japan PMDA, Australia TGA, UK MHRA, China NMPA
  [6-B, A] Combinations
  [A]  All five

Usage:
    python Run_All_RSS.py
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

_HERE = os.path.dirname(os.path.abspath(__file__))   # Scripts folder
_ROOT = os.path.dirname(_HERE)                        # RSS_New Strings_30 June

if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from rss_opml_common import run_pipeline, get_date_filters, COLUMNS, build_combined_report

# Indication (MASH/AUD/ALD-MetALD/...) is not a meaningful grouping for
# regulatory-agency feeds, company-wide "All News" alerts, or the broad
# trade-pub/wire-service feeds -- drop it from those reports only.
NO_INDICATION_COLUMNS = [c for c in COLUMNS if c != "Indication"]

PIPELINES = [
    {
        "label":       "ClinicalTrials.gov",
        "opml":        os.path.join(_ROOT, "OPML Files", "RSS_ClinicalTrials.opml"),
        "prefix":      os.path.join(_ROOT, "Output_ClinicalTrials", "CT_Report"),
        "max_workers": 12,
    },
    {
        "label":       "Google Alerts",
        "opml":        os.path.join(_ROOT, "OPML Files", "RSS_GoogleAlerts.opml"),
        "prefix":      os.path.join(_ROOT, "Output_GoogleAlerts", "GoogleAlerts_Report"),
        "max_workers": 10,
    },
    {
        "label":       "Company Alerts",
        "opml":        os.path.join(_ROOT, "OPML Files", "RSS_CompanyAlerts.opml"),
        "prefix":      os.path.join(_ROOT, "Output_CompanyAlerts", "CompanyAlerts_Report"),
        "max_workers": 8,
        "columns":     NO_INDICATION_COLUMNS,
    },
    {
        "label":       "Media Aggregators",
        "opml":        os.path.join(_ROOT, "OPML Files", "RSS_MediaAggregators.opml"),
        "prefix":      os.path.join(_ROOT, "Out_MediaAggregators", "MediaAggregators_Report"),
        "max_workers": 12,
        "columns":     NO_INDICATION_COLUMNS,
    },
    {
        "label":       "Regulatory Alerts",
        "opml":        os.path.join(_ROOT, "OPML Files", "RSS_RegulatoryAlerts.opml"),
        "prefix":      os.path.join(_ROOT, "Out_RegulatoryAlerts", "RegulatoryAlerts_Report"),
        "max_workers": 6,
        "columns":     NO_INDICATION_COLUMNS,
    },
]


def show_menu():
    while True:
        print("\n" + "=" * 62)
        print("  RSS FEED INGESTION LAUNCHER")
        print("=" * 62)
        print("  Which pipelines do you want to run?\n")
        print("  [1]  ClinicalTrials.gov only")
        print("  [2]  Google Alerts only")
        print("  [3]  Company Alerts only")
        print("  [4]  Media Aggregators only")
        print("  [5]  Regulatory Alerts only")
        print("  [6]  ClinicalTrials.gov  +  Google Alerts")
        print("  [7]  ClinicalTrials.gov  +  Company Alerts")
        print("  [8]  Google Alerts       +  Company Alerts")
        print("  [9]  ClinicalTrials.gov  +  Google Alerts  +  Company Alerts")
        print("  [B]  ClinicalTrials.gov  +  Media Aggregators")
        print("  [A]  All five pipelines")
        print("  [H]  Health Check ALL feeds (connectivity only, no report of content)")
        print("  [Q]  Quit")
        print("=" * 62)

        choice = input("\n  Enter your choice (1-9, A, B, H, or Q): ").strip().upper()

        if choice == "H":
            import subprocess
            subprocess.run([sys.executable, os.path.join(_HERE, "Check_Feed_Health.py")])
            continue

        mapping = {
            "1": [0],
            "2": [1],
            "3": [2],
            "4": [3],
            "5": [4],
            "6": [0, 1],
            "7": [0, 2],
            "8": [1, 2],
            "9": [0, 1, 2],
            "B": [0, 3],
            "A": [0, 1, 2, 3, 4],
        }
        if choice == "Q":
            return []
        if choice in mapping:
            return [PIPELINES[i] for i in mapping[choice]]

        print("  [!] Invalid choice -- please enter 1-9, A, B, H, or Q.")


if __name__ == "__main__":
    selected = show_menu()

    if not selected:
        print("\n  Exited. No pipelines were run.")
        if not os.environ.get("ENDTOEND_ORCHESTRATED"):
            input("  Press Enter to close...")
        sys.exit(0)

    print("\n  Selected pipelines:")
    for p in selected:
        print(f"    - {p['label']}")

    start_dt, end_dt = get_date_filters()

    # Persist the START of this run's date filter so Run_EndToEnd.py can pass
    # it on to draft_morning_update.py as --window-start (stage 4), making the
    # provenance "already-seen" cutoff match the extraction window you actually
    # asked for here, instead of falling back to a lookback-hours guess.
    # Best-effort only: a write failure must never abort the RSS run itself.
    try:
        import json
        window_state_path = os.path.join(_HERE, "_extraction_window_state.json")
        with open(window_state_path, "w", encoding="utf-8") as f:
            json.dump(
                {"start_dt": start_dt.strftime("%Y-%m-%d %H:%M") if start_dt else None},
                f,
            )
    except OSError:
        pass

    overall_start = datetime.datetime.now()
    results = []
    pipeline_outputs = []

    for i, p in enumerate(selected, 1):
        print(f"\n[{i}/{len(selected)}] Starting: {p['label']}")
        t0 = datetime.datetime.now()
        out_path = run_pipeline(
            opml_path     = p["opml"],
            output_prefix = p["prefix"],
            max_workers   = p["max_workers"],
            start_dt      = start_dt,
            end_dt        = end_dt,
            columns       = p.get("columns"),
        )
        elapsed = (datetime.datetime.now() - t0).seconds
        results.append({
            "label":   p["label"],
            "file":    os.path.basename(out_path) if out_path else "-- no data --",
            "exists":  out_path is not None,
            "elapsed": elapsed,
        })
        pipeline_outputs.append({"opml": p["opml"], "path": out_path})

    total_elapsed = (datetime.datetime.now() - overall_start).seconds
    print("\n" + "=" * 85)
    print("  COMPLETED")
    print("=" * 85)
    for r in results:
        status = "[OK]      " if r["exists"] else "[NO DATA] "
        print(f"  {status}  {r['label']:<26}  {r['file']}  ({r['elapsed']}s)")
    print(f"\n  Total time : {total_elapsed}s")
    print("=" * 85)

    combined_dir = os.path.join(_ROOT, "Output_Combined")
    combined_path = build_combined_report(pipeline_outputs, combined_dir)
    if combined_path:
        print(f"\n[COMBINED] Written -> '{combined_path}'")
    else:
        print("\n[COMBINED] No data from this run — combined file not created.")

    if not os.environ.get("ENDTOEND_ORCHESTRATED"):
        input("\nPress Enter to close...")
