#!/usr/bin/env python3
"""
test_ct_scripts.py
───────────────────
Regression tests for the clinical-trials pipeline, focused on the bugs found
during the production-readiness review (2026-07-03):

  - compare() must exclude fetch-error trials rather than reporting every
    field as "changed" (would otherwise flood the report with false positives).
  - "latest file" lookups must be immune to the old DDMMYY filename format,
    which does not sort chronologically across month boundaries.
  - long_path() must produce a usable extended-length path on Windows.
  - Cell values must never be written as literal Excel formulas.
  - load_queries()/read_matches() must fail loudly on unexpected headers
    instead of silently reading the wrong column.

Run with:  python test_ct_scripts.py
(No pytest dependency — keeps the project's install to `pip install -r requirements.txt`.)
"""

import tempfile
import time
import unittest
from pathlib import Path

import openpyxl

import ct_change_detection as cd
import ct_query_batch as qb
import ct_step2_enrich as s2


class CompareTests(unittest.TestCase):
    def test_basic_changed_new_removed_unchanged(self):
        old = {
            "NCT1": {"NCT ID": "NCT1", "Status": "Recruiting"},
            "NCT2": {"NCT ID": "NCT2", "Status": "Active"},
        }
        new = {
            "NCT1": {"NCT ID": "NCT1", "Status": "Completed"},  # changed
            "NCT3": {"NCT ID": "NCT3", "Status": "Recruiting"},  # new
            # NCT2 removed
        }
        labels = {"NCT ID": 1, "Status": 2}
        result = cd.compare(old, new, labels, labels)
        self.assertIn("NCT1", result["changed"])
        self.assertEqual(result["new"], ["NCT3"])
        self.assertEqual(result["removed"], ["NCT2"])
        self.assertEqual(result["fetch_errors"], [])

    def test_fetch_error_trial_excluded_not_reported_as_changed(self):
        old = {"NCT1": {"NCT ID": "NCT1", "Brief Title": "Real Title",
                         "Status": "Recruiting", "Enrollment": "500"}}
        new = {"NCT1": {"NCT ID": "NCT1", "Brief Title": "FETCH ERROR: timeout",
                         "Status": "", "Enrollment": ""}}
        labels = {"NCT ID": 1, "Brief Title": 2, "Status": 3, "Enrollment": 4}
        result = cd.compare(old, new, labels, labels)
        self.assertEqual(result["fetch_errors"], ["NCT1"])
        self.assertNotIn("NCT1", result["changed"],
                          "a fetch-error placeholder must never be reported as a real change")

    def test_genuine_change_still_detected_alongside_a_fetch_error(self):
        old = {
            "NCT1": {"NCT ID": "NCT1", "Brief Title": "Real Title", "Status": "Recruiting"},
            "NCT2": {"NCT ID": "NCT2", "Brief Title": "Other Trial", "Status": "Recruiting"},
        }
        new = {
            "NCT1": {"NCT ID": "NCT1", "Brief Title": "FETCH ERROR: timeout", "Status": ""},
            "NCT2": {"NCT ID": "NCT2", "Brief Title": "Other Trial", "Status": "Active"},
        }
        labels = {"NCT ID": 1, "Brief Title": 2, "Status": 3}
        result = cd.compare(old, new, labels, labels)
        self.assertIn("NCT1", result["fetch_errors"])
        self.assertIn("NCT2", result["changed"])

    def test_critical_field_flagging(self):
        self.assertIn("Status", cd.CRITICAL_FIELDS)
        self.assertIn("Enrollment", cd.CRITICAL_FIELDS)


class LatestFileLookupTests(unittest.TestCase):
    def test_find_latest_step1_uses_mtime_not_filename(self):
        """
        Regression test for the DDMMYY sort bug: a file named with an
        earlier-in-month timestamp (e.g. "010726" = 01 Jul) sorts BEFORE
        a same-run later-month file ("250626" = 25 Jun) alphabetically,
        even though 01 Jul is chronologically later. find_latest_step1
        must pick by actual file modification time, not filename string.
        """
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            older = d / "Step1_NCT_IDs_250626_183304.xlsx"  # 25 Jun, created first
            older.write_bytes(b"x")
            time.sleep(0.05)
            newer = d / "Step1_NCT_IDs_010726_181115.xlsx"  # 01 Jul, created second
            newer.write_bytes(b"x")
            picked = s2.find_latest_step1(d)
            self.assertEqual(picked.name, newer.name)


class LongPathTests(unittest.TestCase):
    def test_long_path_prefix_on_windows(self):
        import sys
        p = qb.long_path("some_file.xlsx")
        if sys.platform == "win32":
            self.assertTrue(p.startswith("\\\\?\\"))
        self.assertNotIn("\\\\?\\\\\\?\\", p, "must not double-prefix an already-prefixed path")


class FormulaInjectionTests(unittest.TestCase):
    def test_dangerous_prefixes_neutralized(self):
        for bad in ["=SUM(A1:A9)", "+cmd", "-2+3", "@SUM(1)"]:
            self.assertTrue(qb._safe_str(bad).startswith("'"))

    def test_normal_values_untouched(self):
        self.assertEqual(qb._safe_str("Recruiting"), "Recruiting")
        self.assertEqual(qb._safe_str(None), "")


class FailLoudHeaderTests(unittest.TestCase):
    def test_load_queries_raises_on_unexpected_headers(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "bad.xlsx"
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.append(["Wrong Col A", "Wrong Col B"])
            ws.append(["x", "y"])
            wb.save(str(path))
            with self.assertRaises(ValueError):
                qb.load_queries(path)

    def test_read_matches_raises_on_missing_nct_id_header(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "bad_step1.xlsx"
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "All Matches"
            ws.append(["Not", "The", "Right", "Headers"])
            wb.save(str(path))
            with self.assertRaises(ValueError):
                s2.read_matches(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
