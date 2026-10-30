"""
Parallel AI Triage Agent (Task Group API)
==========================================

Reads a "*_Deduped.xlsx" workbook produced by dedupe_source_urls.py
(columns: Update_Title, Summary_Payload, Resolved_URL), drops any row whose
host is in source_denylist (mail_settings.json, shared with
draft_morning_update.py) before spending anything on it, submits the rest as
one batch to the Parallel AI Task Group API
(client.task_group.create/add_runs/retrieve/get_runs from the "parallel-web"
SDK) for MASH CI triage, and writes an output workbook with columns:

    #, Update, Core Takeaway, Priority, Disposition, Type, Rationale,
    Source Check, Source

The Parallel API key is read from "Parallel API Key.txt" next to this script.

By default the output is written to an "Output_Triage_Agent" folder next to
this script, named "<input file name>_Triaged_<YYYY-MM-DD_HHMMSS>.xlsx" (the
timestamp marks when that run's output was generated, so re-running doesn't
silently overwrite a previous batch). Pass --output to write somewhere else
(and skip the auto-timestamp) instead.

Usage:
    python parallel_triage_agent.py
        (double-click, or run with no arguments: prompts you to either pick
        a file or use the newest *_Deduped.xlsx found under Deduped_Links)
    python parallel_triage_agent.py --input "path/to/report_Deduped.xlsx"
    python parallel_triage_agent.py --input report_Deduped.xlsx --processor core
    python parallel_triage_agent.py --limit 3          # cheap smoke test

NOTE: there is no checkpoint/cache -- every run re-submits and re-pays for
every row in the input, every time.
"""

import argparse
import hashlib
import json
import os
import sys
import time
import traceback
from datetime import datetime
from urllib.parse import urlsplit

import re

import openpyxl
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from parallel import Parallel
from parallel.types import RunInputParam, SourcePolicy

# Article text (feed summaries, LLM output) occasionally carries stray
# control characters that are illegal in XML and make openpyxl refuse to
# write the cell, killing the whole output write after triage has already
# been paid for. Strip them before every ws.append().
_BAD_XML_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _xlsx_safe(value):
    if isinstance(value, str):
        return _BAD_XML_CHARS.sub("", value)
    return value

# Windows consoles default stdout to cp1252, which cannot encode characters
# like an em dash or a Greek letter that legitimately show up in article
# titles. Without this, a single non-ASCII title crashes the whole run with a
# UnicodeEncodeError from a plain print(). Run_EndToEnd.py already sets
# PYTHONIOENCODING=utf-8 for orchestrated runs; this covers running the
# script directly too.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
API_KEY_FILE = os.path.join(SCRIPT_DIR, "Parallel API Key.txt")
DEDUPED_LINKS_DIR = os.path.join(ROOT_DIR, "Deduped_Links")
OUTPUT_DIR = os.path.join(ROOT_DIR, "Output_Triage_Agent")
# Shared with draft_morning_update.py -- source_denylist is applied HERE too
# (post-dedup, pre-triage) so a denylisted host never spends triage credit at
# all, not just mail-drafting credit. mail_settings.json already documents
# itself as settings for more than one script.
MAIL_SETTINGS_FILE = os.path.join(SCRIPT_DIR, "mail_settings.json")

PROCESSOR_CHOICES = ["lite", "base", "core"]
DEFAULT_PROCESSOR = "base"
GROUP_CHUNK_SIZE = 500  # max rows enqueued into a single task group per call

PROCESSOR_PRICE_PER_1000 = {"lite": 5, "base": 10, "core": 25}  # USD

POLL_INTERVAL_SECONDS = 5
STATUS_LOG_INTERVAL_SECONDS = 30
# A batch of 99 rows on 2026-07-17 needed ~1000s to finish processing but
# got cut off at the old 900s (15 min) limit, which wrongly marked every
# still-in-flight row as ERROR (Parallel keeps running them server-side
# regardless of whether this script is still polling). MAX_WAIT_SECONDS is
# now generous, and wait_and_fetch() retries the wait instead of giving up
# on the first client-side timeout.
MAX_WAIT_SECONDS = 1800
WAIT_RETRY_ATTEMPTS = 4

TRIAGE_GUIDELINES = r"""# MASH CI Triage Rules

## Role

You are a MASH CI Triage Agent. Your job is to review incoming updates and categorize each item by priority and disposition.

You do not create final alerts. A separate alert-writing agent handles alert drafting. Your role is only to decide whether each update should become a standalone alert, monthly roundup item, tracker-only item, or no action.

## Scope

In scope: MASH/MASLD, ALD/MetALD, AUD, and Obesity -- but only when the update concerns Novo Nordisk, Eli Lilly, or Roche and their products. Obesity, type 2 diabetes and weight management news about any other company remains OUT OF SCOPE as of 13 Aug 2026 -- such items are filtered out upstream and should not reach you; if one does, treat it as Low priority / No Action. A Novo Nordisk/Eli Lilly/Roche obesity item that does reach you is genuinely in scope: triage it on its own merits using the normal Priority rules below, same as any MASH/ALD/AUD item.

## Critical Grounding Rule (overrides everything below)

Every single call you receive contains EXACTLY ONE update to triage: the one described by the `update_title`, `summary_payload`, and `source_url` input fields. There is never more than one item in a call, no matter how this document reads elsewhere. You must:

- Base every output field ONLY on the specific `update_title` + `summary_payload` provided, using `source_url` only to confirm or add factual detail about that same, single item.
- NEVER invent, recall from training data, or substitute a different company, asset, trial, approval, or event that is not the one named in `update_title`/`summary_payload` -- even if it is a real, well-known MASH/MASLD milestone. If the real news you "remember" about that company/drug is not literally what `update_title`/`summary_payload` describes, you are hallucinating; stop and re-read the input.
- NEVER produce a "batch," a list of multiple items, or language like "the batch contains..." -- output describes the one item only.
- If `source_url` cannot be fetched, is inaccessible, or its content does not clearly match `update_title`/`summary_payload`, triage from `update_title` + `summary_payload` alone. If that text is too thin to assess, say so plainly (e.g. Core Takeaway: "not reported") rather than filling the gap with unrelated content you know about the space.
- Before finalizing your answer, check: does your Update / Core Takeaway name the same company, asset, and event as the input `update_title`/`summary_payload`? If not, discard your draft answer and redo it grounded in the actual input.

## Core Task

For every update, assign:

- Update
- Core Takeaway
- Priority
- Disposition
- Milestone Type
- Rationale
- Source Check Needed

Note: whether this page is the original publisher or a republication of news that
broke elsewhere is NOT your concern. A separate downstream stage establishes that
with open-web access you do not have. Do not reason about it, and do not let a
suspicion that an item is a rerun change any field you produce here.

## General Rules

- Be selective. Not every update deserves an alert.
- First state what the update is, then explain why it matters or does not matter.
- Separate evidence milestones from visibility, positioning, and promotional updates.
- Do not treat company claims, investor framing, or expert opinion as fact unless source-supported.
- Do not upgrade an item only because it mentions a priority company or asset.
- If information is incomplete, classify conservatively and flag the missing detail.
- If an item is repetitive, stale, promotional, or lacks new MASH-specific relevance, assign Low.
- If source verification is needed, state what must be checked, but do not create a full alert.

## Core Takeaway Rules

The Core Takeaway should:

- State the actual news in one sentence.
- Capture the crux: company, asset, event, population/stage, geography, and what changed, when available.
- Be factual, not interpretive.
- Avoid priority language unless the update itself concerns priority status.
- Avoid promotional language.
- Avoid unsupported implications.
- Use "not reported" if a critical detail is missing.

## Priority Companies And Assets

Treat these as priority assets, but do not automatically assign High priority:

- Boehringer Ingelheim: survodutide
- Novo Nordisk: Wegovy, Kayshild, semaglutide, efruxifermin
- Eli Lilly: tirzepatide, Mounjaro, retatrutide
- Madrigal: resmetirom, Rezdiffra
- Inventiva: lanifibranor
- Roche / 89bio: pegozafermin
- GSK: efimosfermin alfa, gatuzosiran, GSK4532990
- Altimmune: pemvidutide
- Merck: efinopegdutide
- D&D Pharmatech: DD01, zabopegdutide
- Hanmi: efocipegtrutide
- AstraZeneca: AZD2389, AZD2693
- Corcept: miricorilant
- Sagimet: denifanstat
- Ionis: ION224
- Viking: VK2809
- Pfizer: PF-06865571 + PF-05221304
- Novo Nordisk / Gilead combinations: semaglutide + cilofexor/firsocostat; semaglutide + cagrilintide or zalfermin

## Disease And Population Weighting

- F2-F3 MASH: highest relevance; core competitive and regulatory battleground.
- cF4 / compensated cirrhosis: high relevance due to unmet need, outcomes risk, and lifecycle-expansion potential.
- ALD / MetALD: medium to high relevance when the update shows credible expansion beyond classic MASH.
- F0-F1 MASLD: usually low to medium unless linked to prevention, patient finding, payer strategy, or major commercial repositioning.
- Unspecified liver-metabolic population: de-escalate unless clearly tied to MASH, MASLD, cF4, ALD, or MetALD strategy.

## High Priority

Assign High priority when the update is likely to materially change competitive, clinical, regulatory, commercial, diagnostic, access, portfolio, or timeline interpretation.

Typical triggers include:

- New Phase 2b, Phase 3, pivotal, registrational, or outcomes readout
- Primary endpoint met or failed
- New fibrosis, MASH resolution, cF4, portal hypertension, decompensation, transplant, mortality, or liver-related outcome data
- Regulatory approval, rejection, filing, filing acceptance, advisory committee, CHMP opinion, PMDA/MHLW action, TGA/Health Canada/NMPA/MHRA milestone, CRL, or label update
- New or changed breakthrough, fast track, PRIME, priority review, accelerated approval, conditional approval, or equivalent designation
- Trial initiation for a pivotal F2-F3 or cF4 program
- Trial termination, withdrawal, suspension, clinical hold, restart, redesign, or material protocol amendment
- Meaningful change in primary endpoint, key secondary endpoint, endpoint hierarchy, biopsy/NIT strategy, population, comparator, treatment duration, or sample size
- Meaningful shift in SSD, PCD, SCD, enrollment, or completion timing that affects filing, approval, launch, or competitive sequencing assumptions
- Major safety, tolerability, discontinuation, dose-limiting, or risk-benefit signal
- Major BD/M&A, licensing, option deal, co-development, asset acquisition, or strategic partnership involving a priority asset or mechanism
- Market access, pricing, reimbursement, payer, HTA, formulary, or coverage decision with meaningful commercial implications
- Diagnostic, pathology, imaging, or NIT milestone that could affect pivotal trial design, endpoint reproducibility, patient finding, central reads, or regulatory evidence generation
- Late-breaker, major oral, or high-profile congress presentation with new clinically or competitively material data

Disposition for High priority: Standalone Alert.

## Medium Priority

Assign Medium priority when the update is relevant and worth tracking, but does not materially change the competitive view.

Typical triggers include:

- Early-stage, interim, exploratory, biomarker, subgroup, NIT, or pharmacodynamic data without definitive endpoint readout
- Enrollment update without clear timeline impact
- Trial status change that is expected and not competitively material
- Minor SSD, PCD, or SCD movement that does not affect filing assumptions
- Trial registry update confirming already-known design
- New publication, poster, oral presentation, or congress update using previously disclosed or incremental data
- Company strategy, investor presentation, earnings commentary, or pipeline update with MASH-relevant content but limited new evidence
- Regional regulatory or access update with limited immediate commercial impact
- Partnership, platform integration, or research collaboration without clear transaction terms or asset-level development impact
- Diagnostic, pathology, imaging, or NIT update that is operationally relevant but lacks regulatory, pivotal-study, or adoption evidence
- Conference/forum visibility that may improve partner access or development-readiness perception but does not disclose new data
- ALD, MetALD, or F0-F1 MASLD update that suggests strategic expansion but remains early, indirect, or exploratory

Disposition for Medium priority: Monthly Roundup.

## Low Priority

Assign Low priority when the update is low-signal, repetitive, promotional, unsupported, or not meaningfully MASH-specific.

Typical triggers include:

- Repackaged old news
- Awareness campaigns
- Routine conference attendance
- Panel participation, invited talk, poster acceptance, or showcase listing without new evidence or meaningful strategic read-through
- Promotional interviews without new data
- Non-specific corporate presentations
- Secondary media coverage repeating known information
- General obesity, diabetes, cardiometabolic, or liver-health update without clear MASH/MASLD relevance (this applies to non-Novo/Lilly/Roche obesity items that still slipped through upstream filtering -- see Scope above)
- Preclinical or discovery-stage update without credible clinical path
- Administrative registry maintenance
- Assumed trial timeline without new source support
- Update for a withdrawn, terminated, inactive, or no-development program unless it changes discontinuation assumptions
- Diagnostics or AI/pathology announcements without validation, workflow deployment, trial integration, or adoption evidence
- Unsupported claims, vague company positioning, or speculative commercial commentary

Disposition for Low priority: Tracker Only or No Action.

## Conference / Forum / Visibility Rules

Treat conference invitations, presentation slots, late-breaker selections, oral presentations, expert panels, showcase forums, and similar platform-access announcements as a distinct category.

Classify these as one of:

- New evidence milestone
- Visibility/platform-access milestone
- Business-development signaling event
- Promotional conference notice

Do not treat forum participation, presentation acceptance, poster/oral selection, invited talks, or panel access as equivalent to a new clinical milestone, regulatory milestone, transaction, formal diligence, or competitive validation.

If previously disclosed data are being shown, say so.

If the venue appears partner-facing, decision-maker-facing, or benchmark-setting, discuss this as interpretation only. Do not imply a transaction, licensing process, or formal validation unless source-supported.

Prefer official conference programs, agendas, prospectuses, abstract listings, or organizer materials over company framing.

## Clinical Trial Triage Rules

Clinical trial updates should be tracked by default, but not all trial updates deserve alerts.

Use this logic:

- High: material clinical, regulatory, timeline, endpoint, safety, filing, or competitive change.
- Medium: relevant but incremental update suitable for monthly roundup.
- Low: administrative, stale, unsupported, assumed, repetitive, or non-material update.

For clinical trial items, consider:

- Trial phase
- Pivotal vs non-pivotal status
- F2-F3, cF4, ALD, MetALD, or F0-F1 MASLD population
- Histology endpoints
- Outcomes endpoints
- Biopsy vs NIT endpoint strategy
- SSD, PCD, and SCD movement
- Enrollment change
- Trial status change
- Regulatory designation
- NDA, sNDA, MAA, priority review, accelerated approval, or standard pathway relevance
- Whether the update affects filing, approval, launch, or competitive sequencing assumptions

Routine trial updates usually go to Monthly Roundup. Administrative registry updates usually go to Tracker Only or No Action.

## Escalators

Escalate by one level when one or more apply:

- Priority asset/company involved
- F2-F3 MASH or cF4 population involved
- Pivotal, registrational, or outcomes trial involved
- New data not previously disclosed
- Histology or clinical outcomes endpoint involved
- Filing pathway or approval timing affected
- Primary-source confirmation
- Material change in trial status, enrollment, SSD, PCD, or SCD
- New regulatory designation, filing, approval, rejection, or label-relevant milestone
- Update affects competitive sequencing across leading MASH mechanisms
- Update changes assumptions for NDA, sNDA, MAA, approval pathway, launch timing, patient eligibility, payer outlook, or market access

## De-escalators

De-escalate by one level when one or more apply:

- No new data
- No new regulatory, access, transaction, or partnership content
- Previously disclosed data are repeated
- Source is only company-framed, promotional, secondary, or weakly sourced
- Trial is assumed rather than source-confirmed
- Update is administrative
- Update is not specific to MASH, MASLD, cF4, ALD, or MetALD
- Evidence is indirect, preliminary, geographically narrow, or incomplete
- Conference visibility is the main signal
- Source does not disclose endpoints, sample size, statistical significance, patient population, geography, timing, or regulatory status

## Output Format

For the single item given in this call, produce:

Update
[Brief description of the item]

Core Takeaway
[One concise factual sentence stating the crux of the news: what happened, to whom/what, where, and what changed]

Priority
[High / Medium / Low]

Disposition
[Standalone Alert / Monthly Roundup / Tracker Only / No Action]

Milestone Type
[Clinical / Regulatory / Diagnostic / Commercial / Portfolio / Business-Development Visibility / Conference Visibility / Market Access / Mixed / Other]

Rationale
[One concise sentence explaining why the item received that priority and disposition]

Source Check Needed
[Yes / No: what needs to be verified, if anything]

Notes for Alert Agent
[Only if High: what the alert-writing agent should focus on]

## Single-Item Output Rules

Reminder: this call always concerns exactly one update (see Critical Grounding Rule above). Applied to that one item:

- Include both Core Takeaway and Rationale.
- Keep Core Takeaway factual and Rationale interpretive.
- Do not draft a final alert.
- Do not expand background unless needed for categorization.
- Do not describe, list, or reference any other item, company, or asset besides the one in `update_title`/`summary_payload`.
"""

INPUT_SCHEMA = {
    "type": "json",
    "json_schema": {
        "type": "object",
        "properties": {
            "update_title": {"type": "string", "description": "The RSS feed's article title."},
            "summary_payload": {"type": "string", "description": "The RSS feed's article summary/excerpt."},
            "source_url": {"type": "string", "description": "The real article URL. May be fetched for verification."},
            "triage_guidelines": {"type": "string", "description": "The full MASH CI Triage Rules to apply."},
            "run_date": {
                "type": "string",
                "description": "The date/time/timezone this triage batch is being run, for reference.",
            },
        },
        "required": ["update_title", "summary_payload", "source_url", "triage_guidelines", "run_date"],
    },
}

OUTPUT_SCHEMA = {
    "type": "json",
    "json_schema": {
        "type": "object",
        "properties": {
            "update": {
                "type": "string",
                "description": "Brief description of the item, per the 'Update' field in the Output Format section of triage_guidelines.",
            },
            "core_takeaway": {
                "type": "string",
                "description": "Factual one-sentence crux of the news per the Core Takeaway Rules in triage_guidelines. Use 'not reported' for a missing critical detail.",
            },
            "priority": {
                "type": "string",
                "enum": ["High", "Medium", "Low"],
                "description": "Per the High/Medium/Low Priority trigger lists and Escalators/De-escalators sections of triage_guidelines.",
            },
            "disposition": {
                "type": "string",
                "enum": ["Standalone Alert", "Monthly Roundup", "Tracker Only", "No Action"],
                "description": "Mapped from priority per triage_guidelines (High->Standalone Alert, Medium->Monthly Roundup, Low->Tracker Only or No Action).",
            },
            "milestone_type": {
                "type": "string",
                "enum": [
                    "Clinical",
                    "Regulatory",
                    "Diagnostic",
                    "Commercial",
                    "Portfolio",
                    "Business-Development Visibility",
                    "Conference Visibility",
                    "Market Access",
                    "Mixed",
                    "Other",
                ],
                "description": "Category of the milestone per triage_guidelines.",
            },
            "rationale": {
                "type": "string",
                "description": "One concise interpretive sentence explaining why this priority/disposition was assigned.",
            },
            "source_check_needed": {
                "type": "string",
                "description": "'No', or 'Yes: <what must be verified>' if source verification is needed before acting on this item.",
            },
        },
        "required": [
            "update",
            "core_takeaway",
            "priority",
            "disposition",
            "milestone_type",
            "rationale",
            "source_check_needed",
        ],
    },
}

OUTPUT_COLUMNS = [
    "#",
    "Update",
    "Core Takeaway",
    "Priority",
    "Disposition",
    "Type",
    "Rationale",
    "Source Check",
    "Source",
    # Basis (Parallel AI's own citations/confidence for this row's fields) --
    # lets a human catch a hallucinated/off-topic result without re-reading the
    # source themselves: if Grounding Check says MISMATCH, no citation the model
    # gave points back at the URL we actually submitted for this row.
    "Citations",
    "Field Confidence",
    "Grounding Check",
    # Audit trail: the exact values sent to Parallel AI for this row, so every
    # request can be traced back to what was actually submitted.
    "Input_URL",
    "Update_Title",
    "Summary_Payload",
]
COLUMN_WIDTHS = [9.14, 16.43, 31.43, 13, 20.57, 31.43, 13, 13, 16.43, 50, 26, 24, 50, 40, 60]

# 1-based index of "Grounding Check" within OUTPUT_COLUMNS -- kept as a named
# constant (not a magic number) so this stays correct if columns are ever
# reordered. Colors match this project's existing Priority-cell convention
# (see Agent_Triage/triage_agent.py's PRIORITY_FILL).
GROUNDING_CHECK_COLUMN_INDEX = OUTPUT_COLUMNS.index("Grounding Check") + 1
GROUNDING_FILL_GREEN = PatternFill(start_color="63BE7B", end_color="63BE7B", fill_type="solid")
GROUNDING_FILL_RED = PatternFill(start_color="F8696B", end_color="F8696B", fill_type="solid")


def _host(url):
    try:
        host = urlsplit(str(url or "")).netloc.lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _normalize_url(url):
    """(host, path) with protocol/www/trailing-slash/case differences ignored,
    so http://Example.com/A/ and https://www.example.com/a compare equal --
    but two different articles on the same domain do NOT. Domain-only
    matching (the old approach here) could not tell those apart: a citation
    landing on any page of the submitted site was marked "OK" even when it
    was a completely different article. Confirmed on real output: 15 of 72
    "OK"-graded rows in one run were actually citing the wrong specific page
    on the right domain -- including one case where the model fabricated an
    entire FDA-approval story for an item that was really an unrelated
    gas-line-safety article, hidden as "OK" by domain-only matching."""
    try:
        parts = urlsplit(str(url or ""))
    except ValueError:
        return "", ""
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = parts.path.rstrip("/").lower()
    return host, path


def load_source_denylist():
    """source_denylist from mail_settings.json, lowercased. Returns [] (with a
    warning, not a crash) if the file is missing or malformed, so this script
    still runs standalone if the settings file is ever moved."""
    if not os.path.exists(MAIL_SETTINGS_FILE):
        print(f"  [denylist] {MAIL_SETTINGS_FILE} not found; no hosts denylisted this run.")
        return []
    try:
        with open(MAIL_SETTINGS_FILE, "r", encoding="utf-8") as f:
            settings = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"  [denylist] could not read {MAIL_SETTINGS_FILE} ({e}); no hosts denylisted this run.")
        return []
    return [d.strip().lower() for d in settings.get("source_denylist", []) if d.strip()]


def host_is_denied(url, denylist):
    """True if url's host is, or is a subdomain of, a denylisted domain."""
    host = _host(url)
    if not host:
        return False
    return any(host == bad or host.endswith("." + bad) for bad in denylist)


def summarize_basis(basis, source_url):
    """Reduce a row's raw Basis (list of {field, reasoning, citations, confidence})
    into three display strings for the output workbook:

        citations_str  -- every unique citation URL across all fields
        confidence_str -- "field:confidence" pairs, e.g. "priority:high, core_takeaway:medium"
        grounding_str  -- "OK" only if at least one citation is the EXACT page we
                          submitted (host + path match, ignoring protocol/www/
                          trailing-slash/case -- see _normalize_url).
                          "MISMATCH" if the model cited something else: either a
                          different page on the same site, or a different site
                          entirely. Both are collapsed into one "MISMATCH" value
                          here (colored Red in the output sheet) because a
                          same-domain-wrong-article citation is just as capable
                          of hiding a hallucinated result as a different-domain
                          one is -- confirmed on real output, including a case
                          where the model fabricated an entire FDA-approval
                          story sourced from an unrelated article on the same
                          domain as the real (irrelevant) input.
                          "" if the model returned no citations at all to check
                          -- also treated as unverified (Red) in the output
                          sheet, since there is nothing to confirm grounding.
    """
    if not basis:
        return "", "", ""
    src_page = _normalize_url(source_url)
    urls = []
    confidences = []
    matched = False
    any_cited = False
    for field_basis in basis:
        field = field_basis.get("field") or ""
        confidence = field_basis.get("confidence")
        if confidence:
            confidences.append(f"{field}:{confidence}")
        for citation in (field_basis.get("citations") or []):
            url = citation.get("url") if isinstance(citation, dict) else None
            if not url:
                continue
            any_cited = True
            if url not in urls:
                urls.append(url)
            if src_page != ("", "") and _normalize_url(url) == src_page:
                matched = True
    grounding_str = "" if not any_cited else ("OK" if matched else "MISMATCH")
    return "; ".join(urls), ", ".join(confidences), grounding_str


def load_api_key():
    if not os.path.exists(API_KEY_FILE):
        sys.exit(f"API key file not found: {API_KEY_FILE}")
    with open(API_KEY_FILE, "r", encoding="utf-8") as f:
        key = f.read().strip()
    if not key:
        sys.exit(f"API key file is empty: {API_KEY_FILE}")
    return key


def row_key(row):
    raw = f"{row.get('Update_Title', '')}|{row.get('Resolved_URL', '')}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def read_deduped_sheet(path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)
    header = next(rows_iter)
    rows = []
    for values in rows_iter:
        if all(v is None for v in values):
            continue
        rows.append(dict(zip(header, values)))
    return rows


def build_run_input(processor, row):
    source_url = row.get("Resolved_URL") or ""
    kwargs = {
        "input": {
            "update_title": row.get("Update_Title") or "",
            "summary_payload": row.get("Summary_Payload") or "",
            "source_url": source_url,
            "triage_guidelines": TRIAGE_GUIDELINES,
            "run_date": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z"),
        },
        "processor": processor,
        "task_spec": {"input_schema": INPUT_SCHEMA, "output_schema": OUTPUT_SCHEMA},
    }
    # Confine any web research this run does to the submitted article's own
    # domain. Without this, the model is free to search the open web and can
    # drift onto an unrelated but topically-similar MASH story instead of the
    # one actually given. Restricting to the source's own host doesn't stop a
    # direct fetch of source_url; it stops the model from wandering to other
    # domains while "verifying" it.
    host = _host(source_url)
    if host:
        kwargs["source_policy"] = SourcePolicy(include_domains=[host])
    return RunInputParam(**kwargs)


def enqueue_group(client, processor, keyed_rows):
    """Create one task group and enqueue all (key, row) pairs into it in a
    single add_runs call. Returns (task_group_id, [(key, run_id), ...])."""
    inputs = [build_run_input(processor, row) for _key, row in keyed_rows]
    tgroup = client.task_group.create()
    resp = client.task_group.add_runs(tgroup.task_group_id, inputs=inputs)
    pairs = list(zip((key for key, _row in keyed_rows), resp.run_ids))
    return tgroup.task_group_id, pairs


def wait_for_group(client, task_group_id, max_wait_seconds=MAX_WAIT_SECONDS):
    waited = 0
    since_log = 0
    consecutive_poll_errors = 0
    while waited <= max_wait_seconds:
        try:
            group = client.task_group.retrieve(task_group_id)
            consecutive_poll_errors = 0
        except Exception as e:
            # Transient network/API hiccup while polling -- don't let one bad
            # status check nuke an otherwise-healthy batch. Only give up if
            # the status check itself keeps failing.
            consecutive_poll_errors += 1
            if consecutive_poll_errors >= 5:
                raise RuntimeError(
                    f"Task group {task_group_id}: 5 consecutive status-check failures, last error: {e}"
                ) from e
            time.sleep(POLL_INTERVAL_SECONDS)
            waited += POLL_INTERVAL_SECONDS
            continue
        if not group.status.is_active:
            return
        since_log += POLL_INTERVAL_SECONDS
        if since_log >= STATUS_LOG_INTERVAL_SECONDS:
            print(f"    ... still processing after {waited}s: {group.status.task_run_status_counts}")
            since_log = 0
        time.sleep(POLL_INTERVAL_SECONDS)
        waited += POLL_INTERVAL_SECONDS
    raise TimeoutError(f"Task group {task_group_id} did not complete within {max_wait_seconds}s")


def wait_and_fetch(client, task_group_id, label):
    """Poll a task group until it finishes, retrying the wait itself (not
    abandoning the group) if it simply takes longer than one attempt's cap.
    Parallel keeps running the tasks server-side the whole time regardless
    of whether we're still polling, so a client-side timeout on its own is
    not evidence the rows failed."""
    for attempt in range(1, WAIT_RETRY_ATTEMPTS + 1):
        try:
            wait_for_group(client, task_group_id)
            return fetch_group_results(client, task_group_id)
        except TimeoutError as e:
            if attempt == WAIT_RETRY_ATTEMPTS:
                raise
            print(f"  {label}: {e} -- still active server-side, continuing to wait "
                  f"(attempt {attempt + 1}/{WAIT_RETRY_ATTEMPTS}) ...")
    raise AssertionError("unreachable")


def fetch_group_results(client, task_group_id):
    """Returns {run_id: {"content": dict_or_None, "basis": list_or_None, "status": str,
    "error": str_or_None}}. content is None when the run did not complete
    successfully; status/error record the real reason so a genuine failure can be
    diagnosed from the output workbook instead of showing up as an opaque "ERROR".
    basis is Parallel's own per-field citations/reasoning/confidence for this run
    (always returned by the API for JSON outputs), captured here so the output
    workbook can show which sources the model actually cited -- if none of them
    are the URL we submitted, that's a directly checkable hallucination signal.
    """
    results = {}
    for event in client.task_group.get_runs(task_group_id, include_output=True):
        run = event.run
        if run.status == "completed" and event.output is not None:
            basis = [fb.model_dump() for fb in (event.output.basis or [])]
            results[run.run_id] = {"content": event.output.content, "basis": basis, "status": run.status, "error": None}
        else:
            results[run.run_id] = {
                "content": None,
                "basis": None,
                "status": run.status,
                "error": run.error.message if run.error else None,
            }
    return results


def find_default_input():
    """Return the most recently modified *_Deduped.xlsx under Deduped_Links,
    so double-clicking this script with no arguments still works."""
    if not os.path.isdir(DEDUPED_LINKS_DIR):
        return None
    candidates = [
        os.path.join(DEDUPED_LINKS_DIR, name)
        for name in os.listdir(DEDUPED_LINKS_DIR)
        if name.endswith("_Deduped.xlsx")
    ]
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
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError:
        typed = input("Enter the full path to the *_Deduped.xlsx file: ").strip().strip('"')
        return typed or None

    initial_dir = os.path.dirname(find_default_input() or DEDUPED_LINKS_DIR)
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    path = filedialog.askopenfilename(
        title="Select a Deduped xlsx file",
        initialdir=initial_dir,
        filetypes=[("Excel files", "*.xlsx")],
    )
    root.destroy()
    return path or None


def prompt_for_input():
    print("No --input provided. What would you like to do?")
    print("  1) Choose a file")
    print("  2) Use the latest file in the Deduped_Links folder")
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
            print("No *_Deduped.xlsx found automatically under Deduped_Links.")
        else:
            print("Please enter 1 or 2.")


def format_duration(seconds):
    seconds = int(round(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def write_insights_sheet(wb, entries_total, counts, new_requests_sent, processor, elapsed_seconds, grounding_counts):
    ws = wb.create_sheet("Insights")
    price_per_1000 = PROCESSOR_PRICE_PER_1000.get(processor, 0)
    cost = (new_requests_sent / 1000) * price_per_1000

    ws.append(["Metric", "Value"])
    ws.append(["Total entries in output", entries_total])
    ws.append(["New Parallel AI requests sent this run", new_requests_sent])
    ws.append(["Processor used this run", processor])
    ws.append(["Price per 1,000 requests", f"${price_per_1000}"])
    ws.append(["Total cost incurred this run", f"${cost:.4f}"])
    ws.append(["Total processing time this run", format_duration(elapsed_seconds)])
    ws.append([])
    ws.append(["Priority breakdown", ""])
    for label in ("High", "Medium", "Low", "ERROR", "NOT PROCESSED"):
        ws.append([label, counts.get(label, 0)])
    ws.append([])
    ws.append(["Grounding Check breakdown (Basis citations vs. submitted URL)", ""])
    ws.append(["OK -- a citation matches the submitted source", grounding_counts.get("OK", 0)])
    ws.append(["MISMATCH -- cited sources, but none is the submitted URL (review these)", grounding_counts.get("MISMATCH", 0)])
    ws.append(["No citations returned", grounding_counts.get("", 0)])

    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws["A9"].font = Font(bold=True)
    ws["A13"].font = Font(bold=True)
    ws.column_dimensions["A"].width = 60
    ws.column_dimensions["B"].width = 20


def write_output(path, rows, results_by_key, basis_by_key, failed_keys, stats, errors_by_key):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Triage"

    ws.append(OUTPUT_COLUMNS)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws.freeze_panes = "A2"

    counts = {"High": 0, "Medium": 0, "Low": 0, "ERROR": 0, "NOT PROCESSED": 0}
    grounding_counts = {"OK": 0, "MISMATCH": 0, "": 0}

    for idx, row in enumerate(rows, start=1):
        key = row_key(row)
        result = results_by_key.get(key)
        source = row.get("Resolved_URL") or ""
        audit_cols = [source, row.get("Update_Title") or "", row.get("Summary_Payload") or ""]
        if result is None:
            label = "ERROR" if key in failed_keys else "NOT PROCESSED"
            counts[label] += 1
            entry = errors_by_key.get(key) or {}
            reason = entry.get("error") or entry.get("status") or ""
            ws.append([_xlsx_safe(v) for v in [idx, "", "", label, "", "", reason, "", source, "", "", "", *audit_cols]])
            continue
        priority = result.get("priority", "ERROR")
        counts[priority] = counts.get(priority, 0) + 1
        citations_str, confidence_str, grounding_str = summarize_basis(basis_by_key.get(key), source)
        grounding_counts[grounding_str] = grounding_counts.get(grounding_str, 0) + 1
        ws.append([_xlsx_safe(v) for v in [
            idx,
            result.get("update", ""),
            result.get("core_takeaway", ""),
            priority,
            result.get("disposition", ""),
            result.get("milestone_type", ""),
            result.get("rationale", ""),
            result.get("source_check_needed", ""),
            source,
            citations_str,
            confidence_str,
            grounding_str,
            *audit_cols,
        ]])
        # Page-level grounding, colored so a mismatch is visible at a glance
        # without reading the Citations column: Green only for an exact-page
        # match; Red for a wrong-page/wrong-domain citation AND for "no
        # citation returned" (nothing to verify against is treated as
        # unverified, not as a pass).
        grounding_cell = ws.cell(row=ws.max_row, column=GROUNDING_CHECK_COLUMN_INDEX)
        grounding_cell.fill = GROUNDING_FILL_GREEN if grounding_str == "OK" else GROUNDING_FILL_RED

    ws.auto_filter.ref = ws.dimensions
    for col_idx, width in enumerate(COLUMN_WIDTHS, start=1):
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    write_insights_sheet(
        wb, len(rows), counts, stats["new_requests_sent"], stats["processor"], stats["elapsed_seconds"],
        grounding_counts,
    )

    wb.save(path)
    return counts


def main():
    parser = argparse.ArgumentParser(description="Triage deduped RSS updates via the Parallel AI Task Group API")
    parser.add_argument("--input", default=None, help="Path to a *_Deduped.xlsx file (prompted if omitted)")
    parser.add_argument("--output", default=None, help="Output xlsx path")
    parser.add_argument("--processor", choices=PROCESSOR_CHOICES, default=DEFAULT_PROCESSOR, help="Parallel processor tier")
    parser.add_argument("--limit", type=int, default=None, help="Only enqueue first N rows (cheap test run)")
    args = parser.parse_args()

    if not args.input:
        if sys.stdin.isatty():
            args.input = prompt_for_input()
        else:
            args.input = find_default_input()
            if not args.input:
                sys.exit(
                    "No --input given and no *_Deduped.xlsx found under Deduped_Links.\n"
                    'Run again with: python parallel_triage_agent.py --input "path\\to\\report_Deduped.xlsx"'
                )
            print(f"No --input given; auto-detected newest report: {args.input}")

    if not os.path.exists(args.input):
        sys.exit(f"Input file not found: {args.input}")

    if args.output:
        output_path = args.output
    else:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        base, ext = os.path.splitext(os.path.basename(args.input))
        run_timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        output_path = os.path.join(OUTPUT_DIR, f"{base}_Triaged_{run_timestamp}{ext}")

    api_key = load_api_key()
    client = Parallel(api_key=api_key)

    print(f"Reading {args.input} ...")
    try:
        rows = read_deduped_sheet(args.input)
    except PermissionError:
        sys.exit(
            f"Could not read {args.input} -- it looks like the file is "
            "currently open in Excel. Close it and run this again."
        )
    print(f"Loaded {len(rows)} rows.")

    # Denylisted hosts (source_denylist in mail_settings.json) are dropped HERE,
    # post-dedup and before any triage API spend -- not just at the mail-drafting
    # stage, so junk hosts (social/video platforms, share-price sites, content
    # farms) never cost a triage call in the first place.
    denylist = load_source_denylist()
    if denylist:
        before = len(rows)
        kept, denied = [], []
        for row in rows:
            (denied if host_is_denied(row.get("Resolved_URL"), denylist) else kept).append(row)
        rows = kept
        if denied:
            print(f"Denylist dropped {len(denied)} of {before} row(s) "
                  f"(hosts: {sorted({_host(r.get('Resolved_URL')) for r in denied})}).")

    # No checkpoint/cache: every row in the input is (re)submitted every run.
    keyed_rows = [(row_key(row), row) for row in rows]
    if args.limit is not None:
        keyed_rows = keyed_rows[: args.limit]

    print(f"Submitting {len(keyed_rows)} row(s) fresh (no cache).")
    print(f"Using processor '{args.processor}'.")

    failed_keys = set()
    new_requests_sent = 0
    process_start = time.time()

    results_by_key = {}
    basis_by_key = {}
    errors_by_key = {}

    for chunk_start in range(0, len(keyed_rows), GROUP_CHUNK_SIZE):
        chunk = keyed_rows[chunk_start : chunk_start + GROUP_CHUNK_SIZE]
        row_by_key = {key: row for key, row in chunk}
        print(f"Enqueuing {len(chunk)} rows as one task group ...")
        try:
            task_group_id, pairs = enqueue_group(client, args.processor, chunk)
        except Exception as e:
            print(f"  Failed to enqueue group: {e}")
            failed_keys.update(key for key, _row in chunk)
            continue

        new_requests_sent += len(pairs)

        print(f"  Waiting for task group {task_group_id} to complete ...")
        try:
            results = wait_and_fetch(client, task_group_id, label=f"group {task_group_id}")
        except Exception as e:
            print(f"  Failed to fetch group {task_group_id}: {e}")
            failed_keys.update(key for key, _run_id in pairs)
            continue

        for key, run_id in pairs:
            result = results.get(run_id) or {"content": None, "basis": None, "status": "missing", "error": "run_id not present in group results"}
            content = result["content"]
            if content is None:
                failed_keys.add(key)
            results_by_key[key] = content
            basis_by_key[key] = result.get("basis")
            errors_by_key[key] = {"status": result["status"], "error": result["error"]}
            title_label = str(row_by_key[key].get("Update_Title", ""))[:60]
            status_note = content.get("priority") if content else f"ERROR ({result['status']})"
            print(f"  {title_label} -> {status_note}")

    elapsed_seconds = time.time() - process_start
    results_by_key = {k: v for k, v in results_by_key.items() if v is not None}
    stats = {
        "new_requests_sent": new_requests_sent,
        "processor": args.processor,
        "elapsed_seconds": elapsed_seconds,
    }

    print(f"Writing output workbook to {output_path} ...")
    try:
        counts = write_output(output_path, rows, results_by_key, basis_by_key, failed_keys, stats, errors_by_key)
    except PermissionError:
        sys.exit(
            f"Could not write to {output_path} -- it looks like the file is "
            "currently open in Excel. Close it and run this again."
        )

    print(f"Priority counts: {counts}")
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
