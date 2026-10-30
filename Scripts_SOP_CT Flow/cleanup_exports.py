#!/usr/bin/env python3
"""
cleanup_exports.py
───────────────────
Housekeeping: archive old CT_Exports / Change_Reports files so the working
folders don't accumulate without bound.

Keeps the KEEP_RECENT most recent files of each type (Step1_NCT_IDs,
Step2_Final_Dataset, Change_Report) in place — ranked by modification time,
not filename, since old filenames don't all sort chronologically — and MOVES
everything older into an "Archive" subfolder right next to it
(CT_Exports/Archive, Change_Reports/Archive). Nothing is ever deleted.

Safe to run any time, as often as you like — it only ever moves files that
are already past the keep-count, so re-running is a no-op if nothing new has
landed since the last run.

Usage:
    python cleanup_exports.py             # keep the 5 most recent of each type
    python cleanup_exports.py --keep 10   # keep 10 most recent of each type
"""

import argparse
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

SCRIPT_DIR = Path(__file__).parent
KEEP_RECENT_DEFAULT = 5

# (folder relative to the project root, filename glob) pairs to manage.
PATTERNS = [
    ("CT_Exports", "Step1_NCT_IDs_*.xlsx"),
    ("CT_Exports", "Step2_Final_Dataset_*.xlsx"),
    ("Change_Reports", "Change_Report_*.xlsx"),
]


def archive_old(folder: Path, pattern: str, keep: int) -> int:
    """Move all but the `keep` most-recently-modified matches into Archive/.
    Returns the number of files archived."""
    files = sorted(folder.glob(pattern), key=lambda f: f.stat().st_mtime)
    to_archive = files[:-keep] if keep > 0 else files
    if not to_archive:
        return 0

    archive_dir = folder / "Archive"
    archive_dir.mkdir(exist_ok=True)
    moved = 0
    for f in to_archive:
        dest = archive_dir / f.name
        if dest.exists():
            print(f"  !  Skipped (already in Archive): {f.name}")
            continue
        f.rename(dest)
        print(f"  ->  Archived: {f.name}")
        moved += 1
    return moved


def main() -> None:
    parser = argparse.ArgumentParser(description="Archive old CT export files.")
    parser.add_argument(
        "--keep", type=int, default=KEEP_RECENT_DEFAULT,
        help=f"Number of most recent files of each type to keep in the main "
             f"folder (default: {KEEP_RECENT_DEFAULT}).",
    )
    args = parser.parse_args()

    project_dir = SCRIPT_DIR.parent
    print("\n" + "=" * 72)
    print("   CT EXPORTS — ARCHIVE OLD FILES")
    print("=" * 72)
    print(f"\n  Project : {project_dir}")
    print(f"  Keeping the {args.keep} most recent file(s) of each type; "
          f"archiving the rest.\n")

    total = 0
    for subfolder, pattern in PATTERNS:
        folder = project_dir / subfolder
        if not folder.is_dir():
            continue
        total += archive_old(folder, pattern, args.keep)

    if total:
        print(f"\n  Done. {total} file(s) archived.")
    else:
        print("\n  Done. Nothing to archive — every folder is already within "
              "the keep-count.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        import traceback
        print(f"\n\n  X  Unexpected error: {exc}")
        traceback.print_exc()
    finally:
        print()
        if sys.stdin.isatty():
            input("  Press Enter to close...")
        sys.exit(0)
