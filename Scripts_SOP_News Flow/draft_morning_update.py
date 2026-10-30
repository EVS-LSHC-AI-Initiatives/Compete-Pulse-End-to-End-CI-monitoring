"""
Morning Update Mail-Drafting Agent
==================================

The final stage of the MASH CI monitoring chain. Reads a "*_Triaged_*.xlsx"
workbook produced by parallel_triage_agent.py, decides which of its several
hundred rows belong in the twice-daily client mail, and uses the Parallel AI Task
Group API -- the same engine and API key as the triage stage -- to write each
survivor up as a decision-grade "Morning Update"/"Evening Update" entry in the
voice of an experienced Senior Consultant. Which of the two labels is used comes
from --slot (default "auto": current IST time vs slot_cutoff_hour_ist).

Selection happens in four steps, and NONE of them filter on triage Priority:

  1. prefilter_rows()   -- deterministic, free. Drops incomplete triage rows,
                           denylisted source hosts, and cross-source duplicates.
                           (No already-mailed ledger any more -- removed per user
                           request; a story can now appear again in a later draft
                           even if an earlier draft already included it.)
  2. provenance pass    -- one cheap call per candidate, run with NO source_policy
                           so it can search the OPEN WEB for an earlier copy of the
                           story. derive_new_old() turns its evidence into the
                           New/Old verdict in Python; gate_on_provenance() then
                           drops republications before any drafting is paid for.
                           This check used to live in the triage stage, which is
                           domain-pinned and so could never actually verify it --
                           it was silently killing 97 of 142 eligible rows.
  3. the drafting agent -- one call per survivor, source_policy-restricted to the
                           row's own host (see build_run_input) so the model cannot
                           wander off to an unrelated story while gathering
                           "broader context". Beyond that domain restriction there is
                           still no on-topic/credibility gate or rebuild step; the
                           drafted headline/bullets are trusted at face value.
  4. assemble_sections()-- folds AUD under the MASH/MASLD header (matching the real
                           mails' combined header; no longer score-penalised -- see
                           AUD_RANK_PENALTY), ranks by the resulting score, collapses same-story drafts
                           (dedupe_drafted), penalises a company's 2nd+ item
                           (SAME_COMPANY_PENALTY), caps at max_items_total /
                           max_items_per_section, then clusters the survivors by
                           company for display and buckets them by section.

Why not Priority: measured against the mail actually sent on 2026-07-23, all
three of that mail's items present in the triage output were rated Low /
"Tracker Only", while every row triage rated High / "Standalone Alert" was junk
(share-option quote pages, a video, a content farm, a stock listicle, a
translated duplicate). Priority and Disposition are also perfectly collinear in
that data, so they are one signal, not two. Relevance and freshness separate the
mail from the noise; importance does not -- a minor academic paper still belongs
in the mail, carrying an "exclude from the newsletter" recommendation.

Every candidate's fate is written to a *_Combined_Review.xlsx next to the draft, as
extra columns appended onto that row's own triage data (single sheet
"Triage_And_Selection") so nothing is dropped silently and a reviewer can cross-check
the draft against one file instead of juggling a triage sheet and a separate log.

Output: an UNSENT Outlook draft (via win32com) that opens for review, plus .htm
and .txt copies in Output_Mail_Draft/. The agent NEVER sends the mail.

The rendered HTML matches the house format verified against the real thread:
per-section numbering restarting at 1, italic source label inside the bold
headline's parentheses, lime circulation tags, 36pt-indented Evalueserve
Suggestion, and no Restricted/confidentiality footer (Outlook's sensitivity
label and the mail gateway add those on send).

Settings (greeting, recipients, signature, section order, disclaimers, denylist,
caps) live in "mail_settings.json" next to this script. The Parallel API key is
read from "Parallel API Key.txt" next to this script.

Usage:
    python draft_morning_update.py
        (no args: uses the newest *_Triaged_*.xlsx under Output_Triage_Agent)
    python draft_morning_update.py --input "path/to/report_Triaged.xlsx"
    python draft_morning_update.py --dry-run          # pre-filter only, no API spend
    python draft_morning_update.py --limit 5          # cheap smoke test
    python draft_morning_update.py --no-outlook       # write .htm/.txt only
    python draft_morning_update.py --processor core
"""

import argparse
import html
import json
import os
import re
import sys
import time
import traceback
from datetime import date, datetime, timedelta
from urllib.parse import urlsplit

import openpyxl
from parallel import Parallel
from parallel.types import RunInputParam, SourcePolicy

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

# Reuse the pipeline's own keyword classifier to seed a section hint, and the
# shared IST-clock helpers to pick "Morning Update" vs "Evening Update".
from rss_opml_common import (keyword_match_info, obesity_veto, now_ist, resolve_update_slot,
                             long_path, IST_OFFSET)
# Same URL/title normalisation the dedup stage uses, so "the same story" means the
# same thing in both places -- that keeps the cross-source collapse below
# consistent with Deduped_Links output.
from dedupe_source_urls import canonical_url_key, jaccard, title_tokens
# Single source of truth for Parallel AI processor pricing, so the cost shown here
# always matches the triage stage's own cost accounting.
from parallel_triage_agent import PROCESSOR_PRICE_PER_1000

ROOT_DIR = os.path.dirname(SCRIPT_DIR)
API_KEY_FILE = os.path.join(SCRIPT_DIR, "Parallel API Key.txt")
SETTINGS_FILE = os.path.join(SCRIPT_DIR, "mail_settings.json")
# Inputs and outputs live at the workflow root, alongside Output_Combined and
# Deduped_Links -- not inside Scripts_SOP.
TRIAGE_DIR = os.path.join(ROOT_DIR, "Output_Triage_Agent")
OUTPUT_DIR = os.path.join(ROOT_DIR, "Output_Mail_Draft")

PROCESSOR_CHOICES = ["lite", "base", "core"]
GROUP_CHUNK_SIZE = 500
POLL_INTERVAL_SECONDS = 3
MAX_WAIT_SECONDS = 900

# The corporate network occasionally drops DNS/connections to api.parallel.ai.
# Retry transient errors rather than throwing away a task group that was already
# submitted (and billed) server-side.
CONNECT_RETRIES = 6
RETRY_BACKOFF_SECONDS = 5


def _retry(fn, what):
    """Call fn(), retrying a few times on any transient network error."""
    last = None
    for attempt in range(1, CONNECT_RETRIES + 1):
        try:
            return fn()
        except Exception as e:  # APIConnectionError, DNS failures, timeouts, etc.
            last = e
            print(f"  [retry {attempt}/{CONNECT_RETRIES}] {what} failed: {e}")
            time.sleep(RETRY_BACKOFF_SECONDS)
    raise last

# Maps rss_opml_common keyword categories -> mail section names.
# Section names match the real sent mails: the consultant switched to "MASH/MASLD"
# (that name order) on 24 Jul 2026. Keep these in step with section_order in
# mail_settings.json. "Obesity / T2D" was removed on 03 Aug 2026 -- obesity, T2D
# and weight-management updates are out of scope and are vetoed at extraction
# (see obesity_veto in rss_opml_common.py); the drops below are the safety net.
CATEGORY_TO_SECTION = {
    "MASH/NASH": "MASH/MASLD",
    "ALD/MetALD": "ALD/MetALD",
    "AUD": "AUD",
}
# Preference when an item matches more than one category.
SECTION_PREFERENCE = ["MASH/MASLD", "ALD/MetALD", "AUD"]

# The section name the drafting model must never route an item to. Anything the
# model still labels this way is DROPPED, not rerouted -- without this, removing
# the name from section_order would silently republish obesity items under "Other".
EXCLUDED_SECTION = "Obesity / T2D"

PRIORITY_RANK = {"High": 0, "Medium": 1, "Low": 2}

# Longest source URL we will send. source_url cannot be trimmed (source_policy and
# fetching need it byte-exact), so an enormous one is the only input that can push a
# request past Parallel's 25k task_spec+input limit. See the prefilter.
MAX_URL_CHARS = 200

# ── Provenance (New/Old) ─────────────────────────────────────────────────────
#
# This check lives here, not in the triage stage, for one reason: deciding whether
# a story is a republication of news that broke elsewhere requires searching the
# OPEN WEB for an earlier copy. The triage stage pins each request to the row's own
# domain (which cut its hallucination rate from 42% to 2%), so it structurally
# cannot answer this. The provenance pass below runs UNRESTRICTED so it can.
#
# The verdict itself is computed in Python (derive_new_old) from the evidence the
# model returns, not asked of the model directly. When triage owned this and
# applied the rule itself, it
# produced 13 New vs 66 Old on identical "dated yesterday" evidence, and tagged
# opko.com's own press release Old while ts2.tech's rehash of it came back New.

ORIGINATING_DOMAINS = (
    "globenewswire.com", "businesswire.com", "prnewswire.com", "prnewswire.co.uk",
    "newswire.ca", "sec.gov",
)
REGULATOR_REGISTRY_DOMAINS = (
    "clinicaltrials.gov", "fda.gov", "ema.europa.eu", "pmda.go.jp", "mhlw.go.jp",
    "gov.uk", "tga.gov.au", "nmpa.gov.cn", "canada.ca", "who.int",
    "clinicaltrialsregister.eu", "isrctn.com", "anzctr.org.au", "chictr.org.cn",
)
JOURNAL_DOMAINS = (
    "pubmed.ncbi.nlm.nih.gov", "ncbi.nlm.nih.gov", "thelancet.com", "nature.com",
    "nejm.org", "jamanetwork.com", "bmj.com", "sciencedirect.com", "springer.com",
    "wiley.com", "journals.plos.org", "frontiersin.org", "mdpi.com", "oup.com",
    "academic.oup.com", "cell.com", "doi.org", "medrxiv.org", "biorxiv.org",
)
AGGREGATOR_DOMAINS = (
    "ts2.tech", "tradingview.com", "stocktitan.net", "investing.com", "moomoo.com",
    "barchart.com", "marketscreener.com", "marketbeat.com", "gurufocus.com",
    "simplywall.st", "seekingalpha.com", "fool.com", "benzinga.com", "zacks.com",
    "finance.yahoo.com", "yahoo.com", "aol.com", "msn.com", "news.google.com",
    "marketchameleon.com", "tipranks.com", "allsci.com", "tradingkey.com",
    "fullview.com", "briefing.com",
)
SOCIAL_DOMAINS = (
    "linkedin.com", "facebook.com", "twitter.com", "x.com", "reddit.com",
    "youtube.com", "youtu.be", "instagram.com", "tiktok.com", "threads.net",
    "medium.com", "substack.com",
)

ORIGINAL_ROLES = {
    "originating_press_release_or_wire",
    "regulator_or_registry_record",
    "peer_reviewed_journal_of_record",
    "outlet_original_reporting",
}
REPUBLICATION_ROLES = {
    "syndicated_or_translated_copy",
    "aggregator_or_roundup",
    "marketing_or_content_farm",
    "social_or_forum_post",
}

# What to call an item when provenance genuinely cannot be established. "New" is
# conservative: an Old verdict removes the item from the mail, so defaulting to
# Old would silently bin anything unverifiable. Flip to "Old" only if you would
# rather under-report than over-report.
INDETERMINATE_PROVENANCE_VERDICT = "New"

# A company's own newsroom/IR release URL. We cannot enumerate every pharma
# company's domain, but their release paths follow these conventions -- this is
# what recognises opko.com/investors/news-events/press-releases/... as an
# originating publisher instead of "unknown".
IR_PATH_MARKERS = (
    "/press-release", "/press_release", "/pressrelease", "/news-release",
    "/newsroom", "/news-events", "/media-release", "/investors/news",
    "/investor-relations/news", "/media/news",
)

# Wire services a company uses to distribute its OWN release. Crediting one of
# these is still the original publication -- the company is the source and the
# wire is only the pipe.
SELF_DISTRIBUTION_CREDITS = (
    "globe newswire", "globenewswire", "business wire", "businesswire",
    "pr newswire", "prnewswire", "newswire", "sec edgar", "edgar",
    "company press release", "press release", "not applicable", "not reported",
    "none", "n/a",
)


def _domain_matches(host, domains):
    return any(host == d or host.endswith("." + d) for d in domains)


def source_role_hint(source_url):
    """Domain-derived prior for page_source_role, passed to the model as a hint.

    Conservative by design: fires only where the role is unambiguous from the URL
    alone, and returns 'unknown' otherwise so the model decides from content.
    """
    host = host_of(source_url)
    if not host:
        return "unknown"
    if _domain_matches(host, REGULATOR_REGISTRY_DOMAINS):
        return "regulator_or_registry_record"
    if _domain_matches(host, JOURNAL_DOMAINS):
        return "peer_reviewed_journal_of_record"
    if _domain_matches(host, ORIGINATING_DOMAINS):
        return "originating_press_release_or_wire"
    if _domain_matches(host, SOCIAL_DOMAINS):
        return "social_or_forum_post"
    if _domain_matches(host, AGGREGATOR_DOMAINS):
        return "aggregator_or_roundup"
    try:
        path = urlsplit(str(source_url or "")).path.lower()
    except ValueError:
        path = ""
    if any(marker in path for marker in IR_PATH_MARKERS):
        return "originating_press_release_or_wire"
    return "unknown"


def _is_self_distribution(credited):
    c = (credited or "").strip().lower()
    if not c:
        return True
    return any(marker in c for marker in SELF_DISTRIBUTION_CREDITS)


def _parse_news_date(text):
    """Pull a calendar date out of the provenance pass's date string.

    Tolerant on purpose: the field arrives as "2026-07-21", "2026-07-21 09:00 ET",
    "July 21, 2026", or "Not determinable". Returns a datetime.date or None.
    """
    raw = str(text or "").strip()
    if not raw or "not determin" in raw.lower() or "not reported" in raw.lower():
        return None
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.search(r"([A-Z][a-z]+)\s+(\d{1,2}),?\s+(\d{4})", raw)
    if m:
        for fmt in ("%B %d %Y", "%b %d %Y"):
            try:
                return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", fmt).date()
            except ValueError:
                continue
    return None


def _parse_window_start(text):
    """Parse Run_Agent's --window-start ('YYYY-MM-DD HH:MM' or bare 'YYYY-MM-DD',
    the latter taken as that day's start) into an IST-naive datetime, or None."""
    raw = str(text or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


# Fixed UTC-offset approximation for the timezone abbreviations a news source or
# the provenance model is likely to write. DST-abbreviation pairs (EST/EDT etc.)
# are listed separately so "EDT" and "EST" resolve to different offsets rather
# than averaging them; everything else is a single fixed offset (no DST table).
# This trades a possible ~1h error for not needing an IANA timezone database --
# acceptable here because the cutoff comparison only needs to place an item on
# the correct side of a lookback window, not to the minute.
_TZ_ABBREV_UTC_OFFSET_HOURS = {
    "UTC": 0, "UT": 0, "GMT": 0, "Z": 0,
    "EST": -5, "EDT": -4, "ET": -5,
    "CST": -6, "CDT": -5, "CT": -6,
    "MST": -7, "MDT": -6, "MT": -7,
    "PST": -8, "PDT": -7, "PT": -8,
    "BST": 1, "CET": 1, "CEST": 2,
    "IST": 5.5,  # India Standard Time -- this pipeline's own client-facing zone
    "KST": 9, "JST": 9, "SGT": 8, "HKT": 8, "AEST": 10, "AEDT": 11,
}


def _parse_news_moment(text):
    """Resolve the provenance pass's date/time/timezone string to an IST-naive
    datetime, when the string is precise enough to support it.

    Accepts the same free-form style the triage stage already asks for
    elsewhere in this pipeline, e.g. "2026-07-27 14:32 ET", "2026-07-27 (time
    not disclosed)", "July 21, 2026", "Not determinable". Returns None when no
    date is present at all, or when a date is present but no time+timezone can
    be resolved -- the caller then falls back to date-only comparison, which is
    the same behaviour this pipeline used before time-of-day precision existed.
    """
    day = _parse_news_date(text)
    if day is None:
        return None
    raw = str(text or "")
    m = re.search(r"(\d{1,2}):(\d{2})\s*([A-Za-z]{1,4})?", raw)
    if not m:
        return None
    hour, minute, tz_abbrev = int(m.group(1)), int(m.group(2)), (m.group(3) or "").upper()
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    offset_hours = _TZ_ABBREV_UTC_OFFSET_HOURS.get(tz_abbrev)
    if offset_hours is None:
        return None  # time given but timezone unresolvable -- not safe to use
    utc_dt = datetime(day.year, day.month, day.day, hour, minute) - timedelta(hours=offset_hours)
    return utc_dt + IST_OFFSET


def derive_new_old(result, cutoff_dt, source_url=""):
    """Turn the provenance evidence into the New/Old verdict.

    THE TEST IS THE MOMENT THE NEWS BROKE, NOT WHO PUBLISHED IT.

      New = the underlying news first broke at or after cutoff_dt (the moment this
            run's extraction window opened). A same-day syndication, translation or
            aggregator copy of fresh news is still New -- it is fresh news, merely
            carried by a secondary outlet.
      Old = the underlying news first broke before cutoff_dt, so this page is a
            rerun of something the client has already had a chance to see.

    Precision: when the provenance pass reports a resolvable time and timezone
    (_parse_news_moment succeeds), the comparison is exact -- against the full
    cutoff moment, not just its calendar day. This matters at the edges of a
    window: a window opening at 19:30 must treat 14:00 same-day news as Old and
    20:00 same-day news as New, which a date-only comparison cannot distinguish.
    When only a date is available, the comparison falls back to calendar days
    against cutoff_dt's own date -- the same behaviour this pipeline used before
    time-of-day precision existed, and still safe: it was what correctly restored
    Clearmind's CMND-100 readout and Hanmi's Q2 result on 2026-07-28, both of
    which reached us only as same-day secondary copies and were wrongly dropped
    by an earlier "is this the first publisher?" test.

    Duplicate copies of one story are NOT this function's problem -- prefilter_rows
    and dedupe_drafted collapse those. Here we only judge staleness.

    Returns (verdict, reason) so every verdict is traceable in the selection log.
    """
    if not result:
        return INDETERMINATE_PROVENANCE_VERDICT, "no provenance result returned"

    date_text = result.get("earliest_publication_date")
    broke_on = _parse_news_date(date_text)
    role = (result.get("page_source_role") or "").strip()

    if broke_on is None:
        return (INDETERMINATE_PROVENANCE_VERDICT,
                "could not establish when the news first broke; treated as fresh")

    precise_moment = _parse_news_moment(date_text)
    if precise_moment is not None:
        if precise_moment < cutoff_dt:
            earlier = (result.get("earlier_publication_url") or "").strip()
            where = f" (earliest copy: {host_of(earlier)})" if host_of(earlier) else ""
            return "Old", (f"news first broke {precise_moment:%Y-%m-%d %H:%M} IST, before window "
                           f"opened {cutoff_dt:%Y-%m-%d %H:%M} IST{where}")
        label = f"news broke {precise_moment:%Y-%m-%d %H:%M} IST"
        if role in REPUBLICATION_ROLES:
            label += f"; carried by a {role.replace('_', ' ')} but still inside the window"
        return "New", label

    # No usable time -- fall back to calendar-day comparison against the cutoff's
    # own date, exactly as before time-of-day precision was added.
    if broke_on < cutoff_dt.date():
        earlier = (result.get("earlier_publication_url") or "").strip()
        where = f" (earliest copy: {host_of(earlier)})" if host_of(earlier) else ""
        return "Old", f"news first broke {broke_on.isoformat()}, before {cutoff_dt.date().isoformat()}{where}"
    label = f"news broke {broke_on.isoformat()} (no time/timezone resolvable; date-only comparison)"
    if role in REPUBLICATION_ROLES:
        label += f"; same-day {role.replace('_', ' ')} is still fresh"
    return "New", label

PROVENANCE_GUIDELINES = r"""# Provenance Check: on what day -- and at what time -- did this news FIRST break?

Establish the date AND, whenever possible, the time and timezone at which the underlying news first became public anywhere, so a downstream step can tell fresh news from a rerun of something already reported earlier.

## What this is and is not

The question is WHEN the news broke, NOT who published it. A wire copy, an aggregator repost, a translation, or a secondary outlet's write-up of news that broke TODAY is still today's news. Do not treat a secondary source as stale merely for being secondary. That mistake has already cost real coverage: on 2026-07-28 it discarded Clearmind's CMND-100 readout and Hanmi's Q2 result, both of which the consultant did include in the client mail, citing exactly the secondary copies that had been thrown away.

Precision matters at the edges of a monitoring window. The downstream check compares against the EXACT moment a window opened, not just its calendar day, so an item published a few hours before the window opened and one published a few hours after it are treated differently even though both happened "today". Report a time and timezone whenever the source gives you one -- do not round it off to just a date.

## How to work

You have open web access for this task. Use it.

1. Read the page at source_url and identify the underlying news event (company, asset, what happened).
2. Search the web for that same event and find the EARLIEST public appearance of it you can, on any site.
3. Put that earliest date, and time+timezone if the source states one, in earliest_publication_date, formatted like "2026-07-27 14:32 ET" or "2026-07-27 (time not disclosed)" if only the date is known. Put the URL of that earliest copy in earlier_publication_url when it sits on a different host than source_url.
4. If source_url is itself the earliest appearance, put its own publication date/time in earliest_publication_date and leave earlier_publication_url empty.

Do NOT count as an earlier appearance: the wire service that distributed the same organisation's own release (GLOBE NEWSWIRE, Business Wire, PR Newswire), the company's own SEC/EDGAR filing of that release, or background literature a journal article cites. Those are the same news at its source.

Never guess a date or a time. If you genuinely cannot establish either, leave earliest_publication_date empty and say so in provenance_notes; it will be treated as fresh, which is the safe default. A date with no time is far better than a guessed time -- only report a time you actually found stated on a source.

## Fields to produce

- earliest_publication_date: the earliest public appearance of this news anywhere. Format "YYYY-MM-DD HH:MM TZ" when you have a time and timezone (e.g. "2026-07-27 14:32 ET", "2026-07-28 09:00 IST"), else plain "YYYY-MM-DD", else "" if not determinable. THIS IS THE FIELD THAT MATTERS MOST -- include the time whenever the source states one.
- earlier_publication_url: URL of that earliest copy when it is on a different host than source_url; "" if source_url is itself the earliest or nothing earlier was found.
- page_source_role: what the page at source_url IS. Recorded for the audit trail; it does not by itself make an item stale. Use source_role_hint as a strong prior.
  - originating_press_release_or_wire: the company's own newsroom/IR page, a wire distribution of its release, or its own regulatory filing.
  - regulator_or_registry_record: an FDA/EMA/PMDA/MHRA/NMPA page or a trial-registry record.
  - peer_reviewed_journal_of_record: the journal or PubMed record where the study was published.
  - outlet_original_reporting: an established news/trade outlet breaking or independently reporting its OWN story.
  - syndicated_or_translated_copy: the same story reproduced or translated from another outlet or wire.
  - aggregator_or_roundup: aggregator, feed-scraper, stock-commentary site, market-movers roundup, or multi-story digest.
  - marketing_or_content_farm: SEO/commerce/telehealth/coupon/"where to buy" pages, or auto-generated filler.
  - social_or_forum_post: LinkedIn/Facebook/Reddit/X/YouTube posts and threads.
  - indeterminate: no usable signal.
- attributes_news_to_other_source: true if the page visibly credits a DIFFERENT publisher for breaking this news. False for wire self-distribution, own filings, and background citations. Audit trail only.
- attributed_source_name: the outlet credited when the above is true; "" otherwise.
- provenance_notes: one short sentence on what you checked and found, stating plainly if the date search was inconclusive.
"""

PROVENANCE_INPUT_SCHEMA = {
    "type": "json",
    "json_schema": {
        "type": "object",
        "properties": {
            "update_title": {"type": "string", "description": "The article title."},
            "summary_payload": {"type": "string", "description": "The article summary/excerpt."},
            "source_url": {"type": "string", "description": "The page whose provenance is being established."},
            "source_role_hint": {"type": "string", "description": "Domain-derived prior for page_source_role; 'unknown' if the domain gave no signal."},
            "run_date": {"type": "string", "description": "The exact moment this run's monitoring window opened, in IST. News that first broke before this moment is stale; news from at or after it is fresh, whoever carried it. Report a time+timezone on earliest_publication_date so this comparison can be precise rather than date-only."},
            "provenance_guidelines": {"type": "string", "description": "The full Provenance Check rules to apply."},
        },
        "required": ["update_title", "source_url", "provenance_guidelines"],
    },
}

PROVENANCE_OUTPUT_SCHEMA = {
    "type": "json",
    "json_schema": {
        "type": "object",
        "properties": {
            "earliest_publication_date": {
                "type": "string",
                "description": "Earliest public appearance of this news anywhere: 'YYYY-MM-DD HH:MM TZ' if a time+timezone is known (preferred), else 'YYYY-MM-DD', else empty if not determinable. Decides fresh vs stale -- never guess it.",
            },
            "earlier_publication_url": {
                "type": "string",
                "description": "URL of the earliest copy of this news found on a DIFFERENT host than source_url; empty if source_url is the original or nothing earlier was found.",
            },
            "page_source_role": {
                "type": "string",
                "enum": [
                    "originating_press_release_or_wire",
                    "regulator_or_registry_record",
                    "peer_reviewed_journal_of_record",
                    "outlet_original_reporting",
                    "syndicated_or_translated_copy",
                    "aggregator_or_roundup",
                    "marketing_or_content_farm",
                    "social_or_forum_post",
                    "indeterminate",
                ],
                "description": "What the page at source_url IS, per the Provenance Check rules. Audit trail only; it does not by itself make an item stale.",
            },
            "attributes_news_to_other_source": {
                "type": "boolean",
                "description": "True only if the page credits a DIFFERENT publisher for breaking this news. False for wire self-distribution, own filings, background citations.",
            },
            "attributed_source_name": {
                "type": "string",
                "description": "Outlet credited when attributes_news_to_other_source is true; empty otherwise.",
            },
            "provenance_notes": {
                "type": "string",
                "description": "One short sentence on what was checked and found.",
            },
        },
        "required": [
            "earliest_publication_date",
            "earlier_publication_url",
            "page_source_role",
            "attributes_news_to_other_source",
        ],
    },
}

DRAFTING_GUIDELINES = r"""Role
You summarize specific shortlisted news from URLs or websites into concise, decision-grade insights for fast decision-making.
How to Work
Treat the user-provided URLs or websites as the source of truth for the requested summary.
For each pasted link, verify whether the news is genuinely new or whether older news is being repackaged, resurfaced, or republished as if it were new.
Read the linked content carefully enough to understand the main development, its significance, and the likely implications.
Recognize common news patterns such as clinical trial registry updates, site additions, recruiting-status changes, regulatory milestones, partnerships, reimbursement progress, launches, patent or IP updates, treatment-guideline changes, market or prescribing updates, channel or distribution-model changes, access-support programs, and early-stage trial initiations.
Focus on extracting signal, not retelling the full article.
Prioritize developments that were actually published within the latest business day or two unless the user asks for a broader lookback.
If a link mainly republishes older news, say so briefly and focus on the genuinely new element, if any.
When a news item involves an asset overview or clinical trial detail, check for the immediately relevant background needed to interpret the update correctly, such as the asset's indication, mechanism, development stage, trial phase, endpoint context, prior readouts, or key program status.
When the item is a patent/IP, policy, guideline, access, distribution, or channel update, include only the immediate context needed to explain why it matters, such as covered use, jurisdiction, treatment positioning, affected patient population, dispensing model, access channel, or supply/authenticity implications.
Treat each link as a separate news item, even when multiple links cover the same event or development. Do not merge, combine, or synthesize separate links into one summary.
When a link is inaccessible, incomplete, or clearly low-confidence, say so briefly and continue with the remaining usable sources.
Summary Rules
Summarize each news item in no more than 2 concise bullets by default.
Use a third bullet only when it is necessary for a decision-relevant nuance that would otherwise be lost.
Keep bullets crisp, cohesive, and information-dense.
Write in direct prose, not label-plus-colon fragments such as "What changed," "Business impact," or "Risk/opportunity," unless the user explicitly asks for that format.
In each item, prioritize these three things:
what changed
why it matters commercially or strategically
the key risk, limitation, or opportunity
Fold those points into natural bullets instead of breaking them into separate sub-headers.
Do not pad with background facts unless they are necessary to understand the decision significance.
Include only the background context that is immediately relevant to interpreting the asset overview or clinical trial update.
Avoid sensational phrasing, speculation, generic commentary, and repetitive restatements of the headline.
Output Format
When the user provides topic buckets or therapeutic-area groupings, preserve those groupings exactly.
Under each grouping, present each link as its own separate numbered item.
Format each item as: number, short identifying label, source in parentheses, then any user-provided circulation note in parentheses when relevant.
Do not add fixed internal headers such as "What changed," "Business impact," or "Risk/opportunity" inside the item.
Put the substance directly into the bullets.
Make the first bullet carry the core development and the immediate significance.
Use the second bullet for the strongest decision-relevant implication, limitation, or commercial consequence.
If useful and well-supported, include one short sub-bullet only when it adds immediate context such as pricing pressure, access implications, endpoint context, program relevance, or partner support details.
When the item is primarily a study or registry update, use concise factual phrasing inside the bullets where helpful, but keep the summary change-focused rather than protocol-heavy.
Prioritize what changed in the update rather than restating the full study record.
Include unchanged study parameters only when they are needed to interpret the significance of the update.
When the key takeaway is that only a narrow change occurred, explicitly say that all other study details remain the same.
For study updates, prefer a compact change-focused summary over long lists of static protocol details.
Only include design, enrollment, interventions, inclusion criteria, or endpoint details when they materially affect why the update matters.
When the item involves an asset overview, clinical mechanism, or program background, you may include a clearly signposted Asset Overview bullet only if that context is immediately relevant.
Keep any such context tight and limited to the mechanism, development stage, prior readout, or strategic context needed to interpret the update.
When management commentary is included in the source, do not reproduce long quotes. Distill it into one concise point only if it adds decision-relevant context about strategy, positioning, timeline, or next steps.
Quality Bar
Make the summary useful to a decision-maker who wants the takeaway quickly.
Prefer clarity and prioritization over completeness.
If the article lacks enough substance for a decision-grade summary, say that plainly and give the strongest available takeaway without inventing detail.
Safety
Do not fabricate facts, numbers, quotes, or implications.
Distinguish clearly between reported facts and reasonable implications.
If uncertainty remains after reviewing the source, mention the uncertainty briefly rather than overstating confidence.

## Morning Update mail fields (produce these for THIS single item)

You are drafting one item of a daily MASH competitive-intelligence "Morning Update" email for a pharma client. This item has already been shortlisted and triaged. Using the provided title, summary, source URL, and triage metadata (priority, disposition, milestone type, rationale, core takeaway), produce the following fields:

CRITICAL GROUNDING RULE (overrides everything above): update_title/summary_payload/core_takeaway ARE the news item, describing ONE article at source_url -- read that page first, never a homepage/index/sibling article on the same host. Headline and bullets must stay about THAT article; never substitute a different, more famous development. Past failure: a Corbus release and an Antag release both came back written up as Altimmune's pemvidutide readout.

BROADER CONTEXT ALLOWED, WITHIN LIMITS: your fetches are confined to source_url's own domain (source_policy), so background reading (prior readouts, mechanism/stage, competitor programs, deal terms, market reaction) is limited to OTHER pages on that same host -- never a different site, however authoritative. Supplementary only -- never lets you change what the story IS; drifting the headline to a different company/asset/event means discard it and write up the provided text.

Before answering: (1) same company/asset/event as update_title/core_takeaway? If not, write up the provided text instead. (2) Is source_url itself among your citations? Citing only other pages means you never confirmed the anchor -- write from the provided text alone. A thin but correct item beats a polished item about the wrong story, which a grounding check discards automatically.

Every rule below is calibrated against the 46 mails actually sent to this client between 26 Jun and 27 Jul 2026. Match that voice; do not invent a new style.

- indication_group: The section this item belongs in. Choose exactly one of: "MASH/MASLD", "ALD/MetALD", "AUD", "Other". Use indication_hint as a strong prior but correct it if the content clearly belongs elsewhere. Put an item in "MASH/MASLD" when MASH/MASLD/fibrosis is the subject of the news. Obesity, type 2 diabetes and weight management are OUT OF SCOPE for this engagement and have no section: if an item is obesity-, T2D-, weight-loss- or GLP-1-weight-led and its MASH/MASLD link is only secondary, return "Other" -- do not force it into a disease section. Classify an alcohol-use-disorder trial or readout as "AUD" whenever the study population or endpoint is alcohol use / heavy drinking, EVEN IF the same asset is also being developed in MASH/MASLD or obesity -- e.g. Altimmune's pemvidutide RECLAIM readout is "AUD", not "MASH/MASLD". Use "Other" only when it fits none of the disease sections.

- story_key: Canonical id of the underlying EVENT as "company|asset|event", lowercase, taken from the event and NOT from this outlet's phrasing -- every article covering the same announcement must produce the same key, because this is how duplicate coverage is collapsed. event is one of: topline-results, trial-initiation, trial-update, regulatory-submission, approval, designation, guidance, publication, earnings, deal, litigation, personnel, market-analysis, other. Use "-" where company or asset does not apply. Example: all 24 outlets covering Altimmune's pemvidutide RECLAIM readout must return "altimmune|pemvidutide|topline-results".
- primary_company: The one company/sponsor this update is principally about ("Novo Nordisk", "GSK", "Viking Therapeutics", "Veru"). Empty string when there is no single owner -- a journal review, a meta-analysis, a payer/guideline or regulator-wide action. Used only to sit sibling items together; it does not affect ranking.

- headline: One line, sentence case, no trailing period, no source name. Two shapes are used in the real mails -- pick whichever fits:
  (a) Company-led, active verb: "Veru announces full enrollment for Phase 2b PLATEAU trial of enobosarm + semaglutide"; "Novo Nordisk submits Wegovy MASH application in South Korea"; "Viking Therapeutics appoints Dorothy Gemmell to board of directors".
  (b) Finding-led, for journal/review/meta-analysis items with no single sponsor: "Metabolic Rewiring in MASLD: From Disease Mechanisms to Precision Medicine"; "LITMUS Imaging Study supports noninvasive biomarker use in MASLD/MASH"; "Review proposes genetics-led precision hepatology framework for MASLD".
  Name the asset and the phase/trial name when they are known. Roughly 8-22 words. Never write a question, a teaser, or a claim the source does not support.

- source_label: Short hyperlink label, under ~5 words, matching how this client's mails cite sources. Observed conventions, in order of preference:
  * Journal article -> "Journal Nature", "Journal Springer", "Journal Lancet", or the exact journal name when it is distinctive ("Nature Medicine", "MDPI Metabolites").
  * Company or wire release -> outlet/company name followed by " PR": "Novo Nordisk PR", "Viking Therapeutics PR", "Business Wire PR", "Biospace PR", "Reuters PR", "News1 PR". The " PR" suffix is the dominant house convention for release-type sources -- use it.
  * News/trade outlet reporting its own story -> plain outlet name: "Reuters", "Business Insider", "Endpoints", "Economic Times", "ChosunBiz", "RACGP", "GOV.UK".
  * Wire aggregator page -> "GlobeNewswire", "PR Newswire".
  Do not put a URL here, and do not repeat the company name if it is already the first word of the headline.

- bullets: Decision-grade sub-bullets. DEFAULT EXACTLY 2 -- 68 of the 141 real items use two. Use 3 only when a distinct decision-relevant nuance would otherwise be lost, 1 only when the source supports nothing more; never exceed 3.
  * Bullet 1: what changed, with the concrete specifics (company, asset, dose, trial name and phase, population, n, endpoint, geography, date) and why it matters, in one flowing sentence. Real example: "Veru completed enrollment above its ~200-patient target in the Phase 2b PLATEAU trial testing oral enobosarm 3 mg vs. placebo in older adults with obesity initiating semaglutide, shifting the program from enrollment execution to data-readout risk."
  * Bullet 2: the strongest implication plus the limitation or risk qualifying it, often joined with a semicolon (expected data timing, competitive read-through, confirmatory-evidence risk).
  * No label prefixes ("What changed:", "Impact:"), no restatement of the headline. Include every number the source gives; a bullet with no specifics is a failed bullet.
  * For a journal/review item: bullet 1 = what the paper found or proposes, bullet 2 = practical relevance plus the caveat (narrative review, preclinical, needs prospective validation).
  * You may add ONE signposted "Asset Overview:" bullet only when mechanism/stage context is needed to read the update.

- circulation_note: Short tag, no surrounding parentheses. Use circulation_note_hint as the default. In the real mails "no broader circulation/alert needed" is overwhelmingly the most common value; "recommended for monthly newsletter" is used when the item should feed the roundup. Use "standalone alert recommended" only for an item that genuinely meets a High-priority trigger -- most runs should have zero.

- evalueserve_suggestion: One to three sentences: what the update is, the recommendation, then the reason. Use these house forms, which are the ones actually sent -- prefer the first two:
  * Exclude: "The update can be excluded from the monthly newsletter and alert, as <reason>." (e.g. "...as it is a conceptual review article and does not report new clinical, regulatory, or commercial developments.")
  * Exclude, equivalent: "We recommend excluding this update from the newsletter; no separate alert is required." Optionally lead with the reason: "As this is a China-based academic trial, we recommend..."
  * Include: "The update to be included in monthly newsletter, no alert required." or "We recommend including this update in the newsletter; no separate alert is required."
  * Beyond the Headlines (uncommon -- pure BD/visibility with no clinical, regulatory or commercial substance: executive appointments, index additions, LOIs/MOUs without terms): "This update is recommended for inclusion in the Beyond the Headlines section of the monthly newsletter; a separate alert is not required."
  * Standalone alert (rare, genuine High-priority trigger only): state the material, time-sensitive reason, then "This is recommended for a standalone alert; also include in the monthly newsletter."
  * Forthcoming company event to be watched: "Today's earnings call will be monitored for any additional commentary on <asset> trial progress or any additional MASH-relevant information."
  Close with one short factual sentence of portfolio context where it helps, as the real mails do (e.g. "Viking has MASH asset - VK2809 in pipeline."). Ground the recommendation in whether the update changes MASH/MASLD treatment, diagnosis, regulatory or competitive dynamics. Factual and measured; no hype.

## Whether this item belongs in the mail at all

Judge inclusion on RELEVANCE AND FRESHNESS, never on importance. A minor academic paper still belongs in the mail; it simply carries an "exclude from the newsletter" recommendation. Do not drop an item for being low-impact.

- on_topic: true if the item concerns MASLD/MASH, ALD/MetALD, AUD or obesity/T2D therapeutics, diagnostics, trials, regulation, market access, pricing/reimbursement or competitive landscape. Preclinical, translational and methodology work on these diseases IS on topic -- animal models, AI/digital-pathology or histology methods, non-invasive biomarker or imaging frameworks, genetic-risk studies all count; low importance is handled by the recommendation, not by exclusion. Set false only for: generic diet/wellness/lifestyle or consumer health tips; stock-price, share-option or "cheap stocks to buy" content; personal-finance commentary; social/video posts with no primary reporting; and articles whose link to these diseases is only an incidental mention.
- is_primary_or_credible_source: true for a company/wire press release, regulator or registry record, peer-reviewed journal, or an established news/trade outlet reporting its own story. False for content farms, scraped or auto-generated aggregator pages, share-price/quote pages, forum and social posts, and pages whose real content does not match their title. Consumer-commerce pages -- "where to buy", coupon/discount, "best places to order", telehealth or compounding-pharmacy marketing -- are NOT credible even when the drug named is in scope.
- exclusion_reason: if either judgement above would keep the item out of the mail, one short phrase saying which and why (e.g. "share-option quote page, not news"). Empty string if it should be included.
- relevance_score: 0-100. This is the ONLY thing that decides which items survive: a real mail carries about 3-5 items, so most of what reaches you will be cut by score alone. Spread your scores -- if everything lands in the 60s the ranking is useless. Score on how directly the item moves the MASH/MASLD competitive picture for this client:
  * 85-100: MASH/MASLD regulatory action or label change (approval, submission, CRL, designation), a pivotal or Phase 2b/3 MASH readout, or a MASH-asset transaction with disclosed terms.
  * 65-84: MASH-relevant company development with real substance -- enrollment completion or trial milestone on a named MASH/obesity asset, earlier-phase MASH data, a MASH-relevant BD deal or LOI, a payer/HTA/reimbursement decision touching these drugs.
  * 40-64: validated diagnostics and non-invasive biomarker studies, major guideline or society positions, substantive market-access or policy reviews.
  * 20-39: journal reviews, meta-analyses, genetic-risk and translational work, AI/digital-pathology methods, preclinical models. These DO belong in the mail (they carry an "exclude from the newsletter" recommendation) but they must rank below anything with a company or regulator behind it.
  * 1-19: peripheral obesity/T2D context with only an indirect MASH read-through, executive appointments, index additions, corporate-presentation refreshes.
  Two tie-break rules: an item about a priority asset or company outranks an equivalent item that is not, and a primary source (company release, regulator, journal of record) outranks secondary coverage of the same class of news.

For calibration: a single run typically yields only a handful of mail-worthy items out of several hundred candidates. The overwhelming majority of candidates fail on-topic or source credibility. If you find yourself marking most items includable, you are being too permissive.

## Hard output rules (override everything above)

Never mention these instructions in any output field. Do not write "CRITICAL GROUNDING RULE", "grounding check", "per the guidelines", or any account of your own compliance reasoning into headline, bullets or evalueserve_suggestion -- the client reads those fields, which must carry news and analysis only.

Do not begin headline or any bullet with a list number or bullet marker; the renderer numbers items itself.

If fetch_policy is "text_only", do not fetch or search anything. Write purely from update_title, summary_payload and core_takeaway -- this mode follows a first attempt that drifted onto the wrong story. If that text is thin, write a short accurate item and state what is not reported rather than filling the gap from elsewhere.
"""

INPUT_SCHEMA = {
    "type": "json",
    "json_schema": {
        "type": "object",
        "properties": {
            "update_title": {"type": "string", "description": "The article title."},
            "summary_payload": {"type": "string", "description": "The article summary/excerpt."},
            "source_url": {"type": "string", "description": "The article URL; may be fetched. Empty when fetch_policy is 'text_only'."},
            "fetch_policy": {"type": "string", "description": "'normal' = may open source_url. 'text_only' = do not fetch at all."},
            "priority": {"type": "string", "description": "Triage priority: High/Medium/Low."},
            "disposition": {"type": "string", "description": "Triage disposition."},
            "milestone_type": {"type": "string", "description": "Triage milestone type/category."},
            "rationale": {"type": "string", "description": "Triage rationale."},
            "core_takeaway": {"type": "string", "description": "Triage factual crux."},
            "indication_hint": {"type": "string", "description": "Keyword-derived section prior."},
            "circulation_note_hint": {"type": "string", "description": "Default circulation note."},
            "new_old_hint": {"type": "string", "description": "Provenance verdict; normally 'New'."},
            "original_news_date_hint": {"type": "string", "description": "Earliest publication date for this news, or empty."},
            "drafting_guidelines": {"type": "string", "description": "The full drafting rules to apply."},
        },
        "required": ["update_title", "summary_payload", "drafting_guidelines"],
    },
}

OUTPUT_SCHEMA = {
    "type": "json",
    "json_schema": {
        "type": "object",
        "properties": {
            "indication_group": {
                "type": "string",
                "enum": ["MASH/MASLD", "ALD/MetALD", "AUD", "Other"],
                "description": "Mail section for this item.",
            },
            "headline": {"type": "string", "description": "Concise factual title; no trailing period, source name or list number."},
            "source_label": {"type": "string", "description": "Short PLAIN-TEXT source label, e.g. 'GlobeNewswire', 'Novo Nordisk PR'. Never markdown, a URL or brackets."},
            "story_key": {
                "type": "string",
                "description": "Canonical event id, lowercase 'company|asset|event'. Same announcement must always give the same key. See story_key in drafting_guidelines for the event vocabulary."
            },
            "primary_company": {
                "type": "string",
                "description": "The one company/sponsor this update is about (e.g. 'Novo Nordisk'); empty when there is no single owner.",
            },
            "bullets": {
                "type": "array",
                "items": {"type": "string"},
                "description": "1-3 decision-grade sub-bullets about the news. Never narrate the instructions.",
            },
            "circulation_note": {
                "type": "string",
                "enum": [
                    "no broader circulation/alert needed",
                    "recommended for monthly newsletter",
                    "standalone alert recommended",
                ],
                "description": "Inline circulation tag, no parentheses.",
            },
            "evalueserve_suggestion": {"type": "string", "description": "Include/exclude recommendation with rationale; see drafting_guidelines."},
            # The model's own on-topic/credibility judgement. Nothing in Python
            # gates on these any more -- every drafted item is included regardless.
            "on_topic": {
                "type": "boolean",
                "description": "In scope per on_topic in drafting_guidelines.",
            },
            "is_primary_or_credible_source": {
                "type": "boolean",
                "description": "Credible per is_primary_or_credible_source in drafting_guidelines.",
            },
            "exclusion_reason": {
                "type": "string",
                "description": "Why the item should be excluded, or empty.",
            },
            "relevance_score": {
                "type": "integer",
                "description": "0-100 rank score; see the relevance_score bands in drafting_guidelines.",
            },
        },
        "required": [
            "indication_group",
            "headline",
            "source_label",
            "story_key",
            "bullets",
            "circulation_note",
            "evalueserve_suggestion",
            "on_topic",
            "is_primary_or_credible_source",
            "relevance_score",
        ],
    },
}


def load_settings():
    if not os.path.exists(long_path(SETTINGS_FILE)):
        sys.exit(f"Settings file not found: {SETTINGS_FILE}")
    with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_api_key():
    if not os.path.exists(long_path(API_KEY_FILE)):
        sys.exit(f"API key file not found: {API_KEY_FILE}")
    with open(API_KEY_FILE, "r", encoding="utf-8") as f:
        key = f.read().strip()
    if not key:
        sys.exit(f"API key file is empty: {API_KEY_FILE}")
    return key


# ── Input discovery & reading ───────────────────────────────────────────────

def find_default_input():
    if not os.path.isdir(TRIAGE_DIR):
        return None
    candidates = [
        os.path.join(TRIAGE_DIR, name)
        for name in os.listdir(TRIAGE_DIR)
        if name.endswith(".xlsx") and "_Triaged_" in name
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda p: os.path.getmtime(long_path(p)), reverse=True)
    return candidates[0]


def read_triage_sheet(path):
    wb = openpyxl.load_workbook(long_path(path), read_only=True, data_only=True)
    ws = wb["Triage"] if "Triage" in wb.sheetnames else wb.active
    rows_iter = ws.iter_rows(values_only=True)
    header = next(rows_iter)
    rows = []
    for values in rows_iter:
        if all(v is None for v in values):
            continue
        rows.append(dict(zip(header, values)))
    return rows


def read_triage_insights_stats(path):
    """Read the request-count and processor used for the triage run from its
    own "Insights" sheet, so the Combined_Review's Run_Cost_Summary can include
    triage's spend alongside the provenance-check and drafting costs, giving a
    true end-to-end (triage -> provenance -> drafting) total."""
    wb = openpyxl.load_workbook(long_path(path), read_only=True, data_only=True)
    if "Insights" not in wb.sheetnames:
        return None
    ws = wb["Insights"]
    requests_sent, processor = None, None
    for row in ws.iter_rows(values_only=True):
        if not row:
            continue
        if row[0] == "New Parallel AI requests sent this run":
            requests_sent = row[1]
        elif row[0] == "Processor used this run":
            processor = row[1]
    if requests_sent is None or processor is None:
        return None
    return {"requests_sent": requests_sent, "processor": processor}


# ── Derivations that seed the LLM ───────────────────────────────────────────

def section_hint(title, summary):
    """Seed the section using the pipeline's own keyword classifier."""
    _matched, categories, _terms = keyword_match_info(title or "", summary or "")
    sections = {CATEGORY_TO_SECTION[c] for c in categories if c in CATEGORY_TO_SECTION}
    for pref in SECTION_PREFERENCE:
        if pref in sections:
            return pref
    return "Other"


def circulation_hint(priority, disposition):
    p = (priority or "").strip().lower()
    d = (disposition or "").strip().lower()
    if d == "standalone alert" or p == "high":
        return "standalone alert recommended"
    if d == "monthly roundup" or p == "medium":
        return "recommended for monthly newsletter"
    # Tracker Only / No Action rows that survive filtering are shown but excluded.
    return "no broader circulation/alert needed"


# ── Stage 1 of selection: deterministic pre-filter (costs nothing) ───────────
#
# Deliberately NOT filtered on Priority/Disposition. Measured against the mail
# Himani actually sent on 2026-07-23, the triage Priority axis is anti-correlated
# with mail inclusion: all three of her items that were present in that run's 320
# rows were rated Low / "Tracker Only", while the nine rows triage rated
# High / "Standalone Alert" were share-option quote pages, a YouTube video, a
# content-farm domain, a "cheap stocks to buy" listicle and a French-translated
# duplicate -- she used none of them. Priority and Disposition are also perfectly
# collinear in that data (Low->No Action/Tracker Only, Medium->Monthly Roundup,
# High->Standalone Alert), so they carry one signal, not two.
#
# What actually separates her 5 items from the other 315 is on-topic-ness, source
# credibility, duplication and freshness -- so that is what we filter on here and
# in the LLM gate.

def row_url(row):
    return (row.get("Source") or row.get("Input_URL") or "").strip()


def row_title(row):
    return str(row.get("Update_Title") or row.get("Update") or "").strip()


def host_of(url):
    """Registrable-ish host for denylist matching: lowercased, no 'www.'."""
    if not url:
        return ""
    try:
        host = urlsplit(str(url)).netloc.lower()
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host.split(":")[0]


def host_is_denied(url, denylist):
    """True if the URL's host is, or is a subdomain of, a denylisted domain."""
    host = host_of(url)
    if not host:
        return False
    return any(host == bad or host.endswith("." + bad) for bad in denylist)


def story_key(row):
    """Stable identity for "this story", shared with the dedup stage -- used here
    only to collapse cross-source duplicates within a single run."""
    return canonical_url_key(row_url(row)) or row_title(row).lower()


def prefilter_rows(rows, settings):
    """Drop what can be ruled out without spending an API call.

    Returns (kept_rows, decisions) where decisions records every input row's fate
    for the selection log -- nothing is dropped silently.
    """
    preferred = [p.strip().lower() for p in settings.get("preferred_sources", []) if p.strip()]
    drop_dispositions = {d.strip().lower() for d in settings.get("drop_dispositions", ["No Action"]) if d.strip()}

    decisions = []
    survivors = []  # (row, tokens)

    def reject(row, reason):
        decisions.append({"row": row, "decision": "dropped (prefilter)", "reason": reason})

    for row in rows:
        title = row_title(row)
        url = row_url(row)
        priority = (row.get("Priority") or "").strip()
        disposition = (row.get("Disposition") or "").strip()

        if priority.upper() in ("ERROR", "NOT PROCESSED"):
            reject(row, f"triage did not complete this row ({priority})")
            continue
        if not title:
            reject(row, "no title")
            continue
        if not url:
            reject(row, "no source URL")
            continue
        if disposition.lower() in drop_dispositions:
            reject(row, f"triage disposition '{disposition}'")
            continue

        # Obesity / T2D / weight management is out of scope (03 Aug 2026). The
        # primary gate is at extraction, so this only fires on rows triaged before
        # that change or supplied via --input from an older corpus -- but it fires
        # BEFORE any API spend, and the reason lands in the selection log.
        vetoed, vetoed_terms = obesity_veto(
            title, f"{row.get('Summary_Payload') or ''} {row.get('Core Takeaway') or ''}")
        if vetoed:
            reject(row, f"obesity/T2D out of scope ({'; '.join(vetoed_terms)})")
            continue

        tokens = title_tokens(title)
        duplicate_of = None
        for kept_row, kept_tokens in survivors:
            if story_key(kept_row) == story_key(row):
                duplicate_of = kept_row
                break
            if tokens and kept_tokens and jaccard(tokens, kept_tokens) >= 0.6:
                duplicate_of = kept_row
                break
        if duplicate_of is not None:
            # Same story from two sources. Keep whichever sits on a preferred host.
            incoming_preferred = host_is_preferred(url, preferred)
            existing_preferred = host_is_preferred(row_url(duplicate_of), preferred)
            if incoming_preferred and not existing_preferred:
                survivors = [(row, tokens) if r is duplicate_of else (r, t) for r, t in survivors]
                reject(duplicate_of, f"duplicate of the same story on a preferred source ({host_of(url)})")
            else:
                reject(row, f"duplicate of the same story already kept from {host_of(row_url(duplicate_of))}")
            continue

        survivors.append((row, tokens))

    kept_rows = [row for row, _tokens in survivors]
    return kept_rows, decisions


def host_is_preferred(url, preferred):
    host = host_of(url)
    if not host:
        return False
    return any(host == good or host.endswith("." + good) for good in preferred)


# Headline-overlap fallback for collapsing duplicate coverage, used only when the
# model gave no story_key. Measured on the 2026-07-29 run: five paraphrased
# headlines about ONE Altimmune readout scored 0.18-0.47 against each other, while
# five genuinely different stories scored 0.00-0.10 -- so the old 0.6 threshold
# merged none of the duplicates. Linking is single-link/transitive, so a chain of
# moderately-similar headlines still forms one cluster.
DRAFT_DUPLICATE_THRESHOLD = 0.20

# ── Ranking policy knobs (calibrated to the manual 29 Jul mail; all tunable) ──
#
# AUD stays folded under the MASH/MASLD header (see AUD_FOLD_SECTION / _is_aud_item)
# because that is what the real mails do -- the 28 Jul Evening mail rendered a
# combined "MASH/MASLD/AUD" header. But AUD items are NOT down-ranked here any
# more: the 29 Jul Morning mail's real omission of the Altimmune readout wasn't
# because AUD is deprioritised -- it's because that story had ALREADY been mailed
# the previous evening. A content-based score penalty was the wrong tool for a
# time-based fact and would also have suppressed genuinely new AUD news. The
# actual fix is derive_new_old()'s datetime-precise provenance cutoff: an item
# that first broke before this run's window opened is now correctly marked Old
# and dropped by gate_on_provenance(), whether or not it happens to be AUD.
AUD_RANK_PENALTY = 0
# Words that mark an item as AUD regardless of the section the model assigned it --
# the model mislabelled the Altimmune RECLAIM readout as MASH/MASLD on 2026-07-29
# even though it is an alcohol-use-disorder trial, so the penalty above has to key
# off the CONTENT, not just the label.
AUD_TEXT_PATTERN = re.compile(
    r"\balcohol use disorder\b|\bheavy[- ]drinking\b|\bAUD\b|\balcohol dependence\b"
    r"|\balcohol[- ]related disorder|\bRECLAIM\b",
    re.IGNORECASE,
)
# The real mails almost never carry two SEPARATE items from one company in a single
# short update (the 27 Jul GSK ZENITH pair was sub-bullets of one item, not two).
# The model over-scored Hanmi's Q2 earnings (84) above the FDA generic-peptide
# guidance (80) the consultant actually included, so the weaker second Hanmi item
# would otherwise take the slot. Penalise the 2nd+ item of a company by its rank
# within that company, so a genuinely strong second item can still get in but a
# routine one yields to a stronger unrelated story.
SAME_COMPANY_PENALTY = 12
# Priority-entity boost. The client's watchlist is specific companies and named
# assets; a story that actually names one outranks generic obesity/T2D chatter that
# the model happens to score similarly. On the 2026-07-29 run the model scored a
# "9.9 Yuan Body Fat Scale" SEO page and a device first-patient at 78 -- the same as
# the FDA generic-peptide guidance and the Novo CagriSema lawsuit the consultant
# actually sent. The boost breaks that tie in favour of the watchlist. Keyed off the
# ROW text (title + summary + takeaway), not the model's output, so it is grounded.
TRACKED_ENTITY_BOOST = 12
# Specific companies and assets in scope. Deliberately NARROW -- disease words
# ("obesity", "GLP-1", "liver") are NOT here, or everything would be "tracked" and
# the boost would be meaningless. Matched case-insensitively on word boundaries.
_TRACKED_ENTITIES = [
    # companies
    "novo nordisk", "eli lilly", "lilly", "boehringer ingelheim", "boehringer",
    "gsk", "glaxosmithkline", "altimmune", "viking therapeutics", "madrigal",
    "akero", "89bio", "hanmi", "zealand pharma", "structure therapeutics",
    "terns pharmaceuticals", "inventiva", "sagimet", "galmed", "gubra", "corcept",
    "astrazeneca", "regeneron", "amgen",
    # assets / brands
    "semaglutide", "wegovy", "ozempic", "rybelsus", "tirzepatide", "zepbound",
    "mounjaro", "resmetirom", "rezdiffra", "lanifibranor", "efruxifermin",
    "pegozafermin", "survodutide", "efimosfermin", "denifanstat", "miricorilant",
    "pemvidutide", "retatrutide", "cagrilintide", "cagrisema", "mazdutide",
    "orforglipron", "efinopegdutide", "aramchol", "saroglitazar", "belapectin",
    "efpeglenatide", "sonefeglutide",
]
_TRACKED_PATTERN = re.compile(
    r"\b(?:" + "|".join(re.escape(e) for e in _TRACKED_ENTITIES) + r")\b", re.IGNORECASE
)


def names_tracked_entity(row):
    """True when the row's own text names a watchlist company or asset."""
    text = f"{row_title(row)} {row.get('Summary_Payload') or ''} {row.get('Core Takeaway') or ''}"
    return bool(_TRACKED_PATTERN.search(text))

_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((?:[^)]*)\)")


def clean_source_label(label):
    """Reduce a source_label to plain text.

    The model has been observed returning a markdown link -- literally
    "[GlobeNewswire](https://...)" -- which the renderer then escapes and shows as
    raw markup in the client's mail. Worse, the URL inside it was a DIFFERENT page
    from the row's own, which is what first exposed the drift bug.
    """
    text = str(label or "").strip()
    text = _MD_LINK_RE.sub(r"\1", text)        # [Label](url) -> Label
    text = re.sub(r"https?://\S+", "", text)      # drop any bare URL
    text = text.strip().strip("[]()<>|,;-").strip()
    return re.sub(r"\s+", " ", text).strip()


# Phrases that mean the model narrated its own instructions instead of the news.
# The 2026-07-28 19:50 mail opened a client-facing bullet with "CRITICAL GROUNDING
# RULE check: ...". The prompt now forbids this; these are the backstop.
_NARRATION_MARKERS = (
    "critical grounding rule", "grounding rule", "grounding check",
    "one page only", "per the guidelines", "per the drafting guidelines",
    "as instructed", "compliance check", "fetch_policy", "source_policy",
    "update_title", "summary_payload", "core_takeaway", "drafting_guidelines",
    "the item matches the source", "checks before you answer",
)
# Leading "1. ", "2) ", "- ", "* ", "• " that the renderer supplies itself.
_LIST_PREFIX_RE = re.compile("^(?:[-*•·]|[(]?[0-9]{1,2}[).:])" + chr(92) + "s+")


def strip_list_prefix(text):
    out = str(text or "").strip()
    for _ in range(3):                      # e.g. "1. - Foo"
        new = _LIST_PREFIX_RE.sub("", out).strip()
        if new == out:
            break
        out = new
    return out


def is_narration(text):
    """True if this reads as the model describing its instructions, not the news."""
    low = str(text or "").strip().lower()
    if not low:
        return True
    return any(marker in low for marker in _NARRATION_MARKERS)


def clean_bullets(raw_bullets):
    """Drop instruction-narration bullets and strip stray list markers."""
    cleaned = []
    for bullet in (raw_bullets or []):
        text = strip_list_prefix(bullet)
        if not text or is_narration(text):
            continue
        cleaned.append(text)
    return cleaned


def normalized_story_key(item):
    """The model's story_key, reduced so trivial formatting differences still match."""
    raw = str((item or {}).get("story_key") or "").strip().lower()
    if not raw:
        return ""
    parts = [re.sub(r"[^a-z0-9]+", "-", p).strip("-") for p in raw.split("|")]
    parts = [p for p in parts if p and p != "-"]
    return "|".join(parts)


def dedupe_drafted(items, decisions):
    """Collapse items that cover the same underlying story, keeping the best-ranked.

    Runs on the DRAFTED output rather than the source URL, because one announcement
    routinely reaches us from a dozen outlets under a dozen different headlines --
    and because drift can make distinct URLs converge on one story.

    Two mechanisms, in order:
      1. story_key: an exact canonical event id from the model. This is the reliable
         one; wording never enters into it.
      2. headline overlap, single-link at DRAFT_DUPLICATE_THRESHOLD, used only for
         items with no usable story_key.

    Input must already be ranked, so the copy we keep is the highest-ranked one.
    """
    def _slug(text):
        return re.sub(r"[^a-z0-9]+", "-", str(text or "").strip().lower()).strip("-")

    def _company_asset(item, key):
        # company from primary_company, falling back to the story_key's first
        # segment; asset from its second segment. Both slugified so "Novo Nordisk"
        # and a "novo-nordisk|..." story_key compare equal across items.
        company = _slug(item.get("company"))
        asset = ""
        if key:
            parts = key.split("|")
            if not company and parts and parts[0]:
                company = parts[0]
            if len(parts) >= 2 and parts[1]:
                asset = parts[1]
        return company, asset

    kept = []  # each: {"item", "key", "tokens", "company", "asset"}
    for item in items:
        key = normalized_story_key(item)
        tokens = title_tokens(item.get("headline") or "")
        company, asset = _company_asset(item, key)
        clash, why = None, ""
        for k in kept:
            # 1) exact canonical event id -- the reliable signal.
            if key and k["key"] and k["key"] == key:
                clash, why = k["item"], f"story_key '{key}'"
                break
            # 2) same company AND same asset, even when the model emitted DIFFERENT
            #    story_key events (topline-results vs trial-update) or split the copies
            #    across sections -- this is what let three Altimmune|pemvidutide
            #    write-ups survive on 2026-07-29. Both parts must be non-empty so an
            #    earnings item (no asset) never merges with a pipeline item.
            if company and asset and company == k["company"] and asset == k["asset"]:
                clash, why = k["item"], f"same company+asset ({company}|{asset})"
                break
            # 3) near-identical headline, now checked even when a story_key exists,
            #    since the keys the model produces for one story are not reliably equal.
            if tokens and k["tokens"] and jaccard(tokens, k["tokens"]) >= DRAFT_DUPLICATE_THRESHOLD:
                clash, why = k["item"], "near-identical headline"
                break
        if clash is None:
            kept.append({"item": item, "key": key, "tokens": tokens,
                         "company": company, "asset": asset})
        else:
            decisions.append({
                "row": item["row"],
                "decision": "dropped (duplicate draft)",
                "reason": f"same story as an already-included item ({why}): {clash.get('headline','')[:64]!r}",
            })
    return [k["item"] for k in kept]


# ── Parallel AI Task Group plumbing (mirrors parallel_triage_agent.py) ───────

# Parallel caps task_spec + input at 25,000 characters, and DRAFTING_GUIDELINES
# plus this call's task_spec already account for ~21,800 of that. The per-row text
# therefore has to be bounded, or a single pathological RSS excerpt fails the whole
# request. These first-pass ceilings are far above a normal feed summary, so in
# practice nothing is cut; _fit_input() below is the guarantee.
MAX_SUMMARY_CHARS = 1200
MAX_TITLE_CHARS = 160
MAX_TRIAGE_FIELD_CHARS = 400

# Hard ceiling we hold ourselves to, 1k under Parallel's documented 25k limit.
INPUT_CHAR_BUDGET = 24600
# Fields we are willing to shorten, in the order we sacrifice them, each with a
# floor below which trimming stops being useful. source_url is deliberately NOT
# in this list: it must stay byte-exact for source_policy and for fetching.
_TRIMMABLE = (("summary_payload", 120), ("core_takeaway", 90), ("rationale", 90))


def _clip(value, limit):
    text = str(value or "").strip()
    return text if len(text) <= limit else text[:limit].rstrip() + " ..."


def _fit_input(payload, task_spec):
    """Shrink the trimmable fields until task_spec + input clears the budget.

    Guarantees the request is never rejected for length no matter how long an
    upstream title, summary or URL turns out to be.
    """
    overhead = len(json.dumps(task_spec, default=str))

    def total():
        return len(json.dumps(payload, default=str)) + overhead

    for field, floor in _TRIMMABLE:
        while total() > INPUT_CHAR_BUDGET:
            current = payload.get(field) or ""
            if len(current) <= floor:
                break
            target = max(floor, int(len(current) * 0.8))
            # Trim to `target` INCLUDING the ellipsis so the value is strictly
            # shorter on every pass. Appending " ..." on top of `target` re-grows
            # the field and spins here forever once it reaches the floor.
            trimmed = current[: max(1, target - 4)].rstrip() + " ..."
            if len(trimmed) >= len(current):
                break  # this field cannot shrink any further
            payload[field] = trimmed
    return payload


def build_run_input(processor, row, fetch_policy="normal"):
    """One drafting request.

    source_policy confines any web research this run does to the submitted
    article's own domain (mirrors parallel_triage_agent.py's build_run_input).
    Without this, the model is free to search the open web while gathering
    "broader context" and can drift onto an unrelated but topically-similar
    story instead of the one actually given -- e.g. writing up a Biospace M&A
    piece or an Inventiva/lanifibranor article while the row's real source_url
    pointed at something else entirely, then still stamping the row's real URL
    onto the drifted text. Restricting fetches to the source's own host doesn't
    stop a direct fetch of source_url; it stops the model from wandering to
    other domains while "verifying" it.

    fetch_policy="text_only" withholds source_url entirely; kept for completeness,
    unused by the current pipeline.
    """
    title = _clip(row_title(row), MAX_TITLE_CHARS)
    summary = _clip(row.get("Summary_Payload") or row.get("Core Takeaway") or "", MAX_SUMMARY_CHARS)
    url = row_url(row)
    text_only = fetch_policy == "text_only"
    task_spec = {"input_schema": INPUT_SCHEMA, "output_schema": OUTPUT_SCHEMA}
    payload = {
            "update_title": title,
            "summary_payload": summary,
            "source_url": "" if text_only else url,
            "fetch_policy": fetch_policy,
            "priority": row.get("Priority") or "",
            "disposition": row.get("Disposition") or "",
            "milestone_type": row.get("Type") or "",
            "rationale": _clip(row.get("Rationale"), MAX_TRIAGE_FIELD_CHARS),
            "core_takeaway": _clip(row.get("Core Takeaway"), MAX_TRIAGE_FIELD_CHARS),
            "indication_hint": section_hint(title, summary),
            "circulation_note_hint": circulation_hint(row.get("Priority"), row.get("Disposition")),
            # Provenance was already established by the unrestricted provenance pass
            # and attached to the row; pass it through as context only.
            "new_old_hint": row.get("_new_old") or "",
            "original_news_date_hint": row.get("_earliest_publication_date") or "",
            "drafting_guidelines": DRAFTING_GUIDELINES,
    }
    kwargs = {
        "input": _fit_input(payload, task_spec),
        "processor": processor,
        "task_spec": task_spec,
    }
    if not text_only:
        host = host_of(url)
        if host:
            kwargs["source_policy"] = SourcePolicy(include_domains=[host])
    return RunInputParam(**kwargs)


def wait_for_group(client, task_group_id):
    waited = 0
    consecutive_errors = 0
    while waited <= MAX_WAIT_SECONDS:
        try:
            group = client.task_group.retrieve(task_group_id)
            consecutive_errors = 0
        except Exception as e:
            # Transient network blip -- the group is still processing server-side;
            # keep polling rather than abandoning it.
            consecutive_errors += 1
            print(f"  [poll] transient error contacting API ({e}); retrying...")
            if consecutive_errors > CONNECT_RETRIES:
                raise
            time.sleep(RETRY_BACKOFF_SECONDS)
            waited += RETRY_BACKOFF_SECONDS
            continue
        if not group.status.is_active:
            return
        time.sleep(POLL_INTERVAL_SECONDS)
        waited += POLL_INTERVAL_SECONDS
    raise TimeoutError(f"Task group {task_group_id} did not complete within {MAX_WAIT_SECONDS}s")


def fetch_group_results(client, task_group_id):
    """{run_id: content_dict_or_None}. Basis is collected separately by
    fetch_group_basis() so existing callers keep their simple shape."""
    def _collect():
        results = {}
        for event in client.task_group.get_runs(task_group_id, include_output=True):
            run_id = event.run.run_id
            if event.run.status == "completed" and event.output is not None:
                results[run_id] = event.output.content
            else:
                results[run_id] = None
        return results

    return _retry(_collect, "fetch group results")


def fetch_group_results_with_basis(client, task_group_id):
    """{run_id: (content_or_None, basis_list)}. Basis carries the URLs the model
    actually cited, which is how we catch it drafting from a different page on the
    same host -- the failure that put a Corbus oncology release into the mail as an
    Altimmune AUD story on 2026-07-28."""
    def _collect():
        results = {}
        for event in client.task_group.get_runs(task_group_id, include_output=True):
            run_id = event.run.run_id
            if event.run.status == "completed" and event.output is not None:
                basis = [fb.model_dump() for fb in (event.output.basis or [])]
                results[run_id] = (event.output.content, basis)
            else:
                results[run_id] = (None, [])
        return results

    return _retry(_collect, "fetch group results with basis")


def build_provenance_input(processor, row, cutoff_dt):
    """One provenance request. Deliberately NOT domain-restricted: establishing
    that a story broke earlier somewhere else requires searching other sites.

    cutoff_dt (the exact moment this run's window opened, IST) is passed through
    as run_date so the model understands precision matters -- see
    PROVENANCE_INPUT_SCHEMA's run_date description and derive_new_old().
    """
    url = row_url(row)
    task_spec = {"input_schema": PROVENANCE_INPUT_SCHEMA, "output_schema": PROVENANCE_OUTPUT_SCHEMA}
    payload = {
            "update_title": _clip(row_title(row), MAX_TITLE_CHARS),
            "summary_payload": _clip(row.get("Summary_Payload") or row.get("Core Takeaway") or "", MAX_SUMMARY_CHARS),
            "source_url": url,
            "source_role_hint": source_role_hint(url),
            "run_date": cutoff_dt.strftime("%Y-%m-%d %H:%M IST"),
            "provenance_guidelines": PROVENANCE_GUIDELINES,
    }
    return RunInputParam(
        input=_fit_input(payload, task_spec),
        processor=processor,
        task_spec=task_spec,
        # No source_policy on purpose -- open web access is the whole point here.
    )


def run_provenance_pass(client, processor, rows, cutoff_dt):
    """Establish New/Old for each row against the open web, then attach the verdict
    to the row under private '_'-prefixed keys.

    Runs on a cheap processor tier: the output is a date/time, a role enum and a
    URL, not prose we publish, so it does not need the drafting tier's depth.

    cutoff_dt is the exact moment (IST datetime) this run's extraction window
    opened. News that broke before it has already had its chance to be mailed;
    news that broke at or after it is fresh. It is NOT simply "today", and NOT
    just a date: the Morning slot looks back 20h into the previous evening, so
    anchoring on today's date would discard the very overnight items that run
    exists to catch, and a date-only cutoff cannot tell same-day-but-earlier news
    apart from same-day-and-later news at the boundary the window actually opened.
    derive_new_old() compares against this full moment when the provenance pass
    resolves a precise time, and falls back to cutoff_dt.date() otherwise.
    """
    for chunk_start in range(0, len(rows), GROUP_CHUNK_SIZE):
        chunk = rows[chunk_start : chunk_start + GROUP_CHUNK_SIZE]
        inputs = [build_provenance_input(processor, row, cutoff_dt) for row in chunk]
        print(f"Provenance-checking {len(chunk)} item(s) against the open web ...")
        tgroup = _retry(lambda: client.task_group.create(), "create provenance task group")
        resp = _retry(
            lambda: client.task_group.add_runs(tgroup.task_group_id, inputs=inputs),
            "enqueue provenance runs",
        )
        run_ids = list(resp.run_ids)
        print(f"  Waiting for task group {tgroup.task_group_id} to complete ...")
        wait_for_group(client, tgroup.task_group_id)
        results = fetch_group_results(client, tgroup.task_group_id)

        for row, run_id in zip(chunk, run_ids):
            content = results.get(run_id)
            verdict, reason = derive_new_old(content, cutoff_dt, row_url(row))
            row["_new_old"] = verdict
            row["_new_old_reason"] = reason
            row["_page_source_role"] = (content or {}).get("page_source_role", "")
            row["_earlier_publication_url"] = (content or {}).get("earlier_publication_url", "")
            row["_earliest_publication_date"] = (content or {}).get("earliest_publication_date", "")
            row["_provenance_notes"] = (content or {}).get("provenance_notes", "")
            print(f"  {row_title(row)[:58]} -> {verdict} ({reason[:48]})")
    return rows


def gate_on_provenance(rows, decisions):
    """Keep fresh news, drop what broke before this run's window. Every drop logged.

    Note what this deliberately does NOT do any more: it no longer drops an item for
    arriving via a secondary outlet. A same-day aggregator copy, translation or wire
    pickup is fresh news and stays -- the consultant's own mails cite exactly such
    sources ("Yahoo Finance PR", "(Media Report)"). Duplicate copies of one story
    are handled by prefilter_rows and dedupe_drafted instead.
    """
    kept = []
    for row in rows:
        if (row.get("_new_old") or "") == "Old":
            earlier = row.get("_earlier_publication_url") or ""
            suffix = f" -- earliest copy: {earlier}" if earlier else ""
            decisions.append({
                "row": row,
                "decision": "dropped (provenance)",
                "reason": f"already-seen news: {row.get('_new_old_reason','')}{suffix}",
            })
        else:
            kept.append(row)
    return kept


def gate_priority_and_provenance(rows, decisions):
    """Keep only rows with High or Medium triage priority AND New provenance
    verdict. This is the combined gate that replaces the old separate
    gate_on_provenance() call -- it filters on both axes in one pass."""
    kept = []
    for row in rows:
        priority = (row.get("Priority") or "").strip()
        new_old = (row.get("_new_old") or "")

        if new_old == "Old":
            earlier = row.get("_earlier_publication_url") or ""
            suffix = f" -- earliest copy: {earlier}" if earlier else ""
            decisions.append({
                "row": row,
                "decision": "dropped (provenance)",
                "reason": f"already-seen news: {row.get('_new_old_reason', '')}{suffix}",
            })
            continue
        if priority not in ("High", "Medium"):
            decisions.append({
                "row": row,
                "decision": "dropped (priority gate)",
                "reason": f"priority '{priority}' is not High or Medium",
            })
            continue
        kept.append(row)
    return kept


def collapse_by_primary_source(rows, settings, decisions):
    """Collapse copies of one story using the provenance pass's own findings, BEFORE
    any drafting is paid for.

    The provenance pass already tells us, per row, the earliest public copy of that
    news (_earlier_publication_url). Rows that trace back to the same primary URL are
    the same announcement carried by different outlets, so only one needs drafting.
    On 2026-07-29 one Altimmune readout arrived from 24 outlets; drafting all of them
    cost 24 core calls and still filled the mail with five copies of one story.

    Keeps the copy on a preferred_sources host when there is one, else the first seen.
    """
    preferred = [x.strip().lower() for x in settings.get("preferred_sources", []) if x.strip()]
    groups, order = {}, []
    for row in rows:
        # Group by the story's earliest public copy. Key on the row's OWN canonical
        # URL when the provenance pass found nothing earlier -- otherwise the
        # ORIGINATING release (whose _earlier_publication_url is empty because it IS
        # the earliest) lands in a group of its own and never merges with the dozen
        # secondary copies that cite it. That is exactly how the Altimmune IR release
        # survived alongside its own wire pickups on 2026-07-29.
        primary = (canonical_url_key(row.get("_earlier_publication_url") or "")
                   or canonical_url_key(row_url(row)))
        key = primary or f"solo-{id(row)}"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(row)

    kept = []
    for key in order:
        members = groups[key]
        if len(members) == 1:
            kept.append(members[0])
            continue
        winner = next((r for r in members if host_is_preferred(row_url(r), preferred)), members[0])
        kept.append(winner)
        for row in members:
            if row is winner:
                continue
            decisions.append({
                "row": row,
                "decision": "dropped (duplicate source)",
                "reason": (f"same announcement as {host_of(row_url(winner))} copy; both trace back to "
                           f"{host_of(row.get('_earlier_publication_url') or '') or 'the same primary source'}"),
            })
    return kept


def draft_items(client, processor, kept_rows):
    """Run every kept row through Parallel AI. Returns a list of dicts:
    {row, result}  (result is the LLM content dict, or None on failure)."""
    drafted = []
    for chunk_start in range(0, len(kept_rows), GROUP_CHUNK_SIZE):
        chunk = kept_rows[chunk_start : chunk_start + GROUP_CHUNK_SIZE]
        inputs = [build_run_input(processor, row) for row in chunk]
        print(f"Enqueuing {len(chunk)} items as one task group ...")
        tgroup = _retry(lambda: client.task_group.create(), "create task group")
        resp = _retry(
            lambda: client.task_group.add_runs(tgroup.task_group_id, inputs=inputs),
            "enqueue runs",
        )
        run_ids = list(resp.run_ids)

        print(f"  Waiting for task group {tgroup.task_group_id} to complete ...")
        wait_for_group(client, tgroup.task_group_id)
        results = fetch_group_results_with_basis(client, tgroup.task_group_id)

        for row, run_id in zip(chunk, run_ids):
            content, basis = results.get(run_id) or (None, [])
            label = str(row.get("Update_Title") or row.get("Update") or "")[:60]
            grp = content.get("indication_group") if content else "ERROR"
            print(f"  {label} -> {grp}")
            drafted.append({"row": row, "result": content, "basis": basis})
    return drafted


# ── Assemble sections ───────────────────────────────────────────────────────

# AUD is deliberately NOT a standalone section: the client "parked" it, so AUD
# items are folded under the MASH/MASLD header (rendered "MASH/MASLD/AUD" when any
# are present) and downranked. See AUD_RANK_PENALTY and _is_aud_item.
DEFAULT_SECTION_ORDER = ["MASH/MASLD", "ALD/MetALD", "Other"]

# Where a folded AUD item is displayed.
AUD_FOLD_SECTION = "MASH/MASLD"


def _is_aud_item(section, headline, bullets):
    """True when an item is alcohol-use-disorder news, whether the model LABELLED it
    AUD or wrote it up as MASH/MASLD (as it did with the Altimmune RECLAIM readout).
    Keys off the content so the downrank cannot be dodged by a mislabel."""
    if (section or "").strip().upper() == "AUD":
        return True
    text = f"{headline or ''} {' '.join(bullets or [])}"
    return bool(AUD_TEXT_PATTERN.search(text))


def _cluster_by_company(ranked):
    """Group same-company items adjacently while preserving relevance order.

    Each company keeps the position of its best-scoring item, and items within a
    company stay in their existing (already score-sorted) order. Items with no
    company stay standalone in place. Input must already be sorted by rank.
    """
    order, groups = [], {}
    for idx, item in enumerate(ranked):
        company = (item.get("company") or "").strip().lower()
        # No company -> unique key so the item is never merged with anything else.
        key = company or f"solo-{idx}"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(item)
    return [item for key in order for item in groups[key]]


def assemble_sections(drafted, settings, decisions):
    """Apply the mail-worthiness gate, rank what survives, cap the volume, and
    bucket the winners by section in configured order.

    Appends to `decisions` so every item the gate or the cap removed is recorded
    in the selection log -- a silently shortened mail is worse than a long one.
    """
    section_order = settings.get("section_order", DEFAULT_SECTION_ORDER)

    gated = []
    failed = 0
    for entry in drafted:
        result, row = entry["result"], entry["row"]
        if result is None:
            failed += 1
            decisions.append({"row": row, "decision": "dropped (agent error)",
                              "reason": "the drafting agent returned no result for this row"})
            continue
        headline = strip_list_prefix(result.get("headline"))
        bullets = clean_bullets(result.get("bullets"))
        model_section = result.get("indication_group") or "Other"
        is_aud = _is_aud_item(model_section, headline, bullets)
        # Fold AUD under MASH/MASLD; anything unknown -> Other.
        section = AUD_FOLD_SECTION if is_aud else model_section
        # Out of scope since 03 Aug 2026. Must be dropped, not passed to the
        # "unknown -> Other" fallback below, or it would just reappear under Other.
        if section == EXCLUDED_SECTION:
            decisions.append({"row": row, "decision": "dropped (out of scope)",
                              "reason": f"drafting agent labelled this '{EXCLUDED_SECTION}', "
                                        "which is no longer in scope"})
            continue
        if section not in section_order:
            section = "Other"
        raw_score = int(result.get("relevance_score") or 0)
        is_tracked = names_tracked_entity(row)
        # Effective score: downrank parked-AUD, boost watchlist entities. Content-
        # based, so a mislabelled AUD item is still downranked and a watchlist story
        # the model under-scored is still lifted above generic obesity/T2D chatter.
        score = (raw_score
                 - (AUD_RANK_PENALTY if is_aud else 0)
                 + (TRACKED_ENTITY_BOOST if is_tracked else 0))
        gated.append({
            "row": row,
            "section": section,
            "is_aud": is_aud,
            "is_tracked": is_tracked,
            "headline": headline,
            "source_label": clean_source_label(result.get("source_label")) or "Source",
            "url": row_url(row),
            "bullets": bullets,
            "circulation_note": (result.get("circulation_note") or "").strip(),
            "evalueserve_suggestion": ("" if is_narration(result.get("evalueserve_suggestion"))
                                        else str(result.get("evalueserve_suggestion") or "").strip()),
            "priority": (row.get("Priority") or "").strip(),
            "raw_score": raw_score,
            "score": score,
            "company": (result.get("primary_company") or "").strip(),
            "story_key": (result.get("story_key") or "").strip(),
        })

    # Rank by (downranked) relevance, triage priority as the tie-break ...
    gated.sort(key=lambda it: (-it["score"], PRIORITY_RANK.get(it["priority"], 3)))
    # ... collapse items that drafted out to the same story (keeps the best-ranked
    # copy: this is what finally reduces one Altimmune readout to a single item) ...
    gated = dedupe_drafted(gated, decisions)
    # ... then penalise the 2nd+ DISTINCT item from one company by its within-company
    # rank, so a routine second item (Hanmi's Q2 earnings) yields the slot to a
    # stronger unrelated story (the FDA generic-peptide guidance) the way the real
    # mails do, while a genuinely strong second item can still get in.
    seen_company = {}
    for item in gated:
        company = (item.get("company") or "").strip().lower()
        if company:
            rank = seen_company.get(company, 0)
            item["score"] -= SAME_COMPANY_PENALTY * rank
            seen_company[company] = rank + 1
    gated.sort(key=lambda it: (-it["score"], PRIORITY_RANK.get(it["priority"], 3)))

    # Stash the scores on every gated row so the selection log can show the ranking.
    for item in gated:
        row = item["row"]
        row["_relevance_score"] = item["raw_score"]
        row["_effective_score"] = item["score"]
        row["_draft_section"] = ("AUD->MASH/MASLD" if item["is_aud"] else item["section"])

    # Select in score order, capping total and per-section volume. Clustering by
    # company happens AFTER the cap (below) so a company's weaker item can no longer
    # ride into the mail on the coat-tails of its strong one.
    selected = []
    for item in gated:
        selected.append(item)
        decisions.append({"row": item["row"], "decision": "INCLUDED",
                          "reason": (f"section {item['section']}"
                                     f"{' (AUD, folded)' if item['is_aud'] else ''}, "
                                     f"relevance {item['raw_score']}"
                                     f"{f' -> {item['score']} after penalties' if item['score'] != item['raw_score'] else ''}")})

    # Cluster the SELECTED items by company so siblings sit together in the mail,
    # then bucket by section (folded-AUD items already carry section MASH/MASLD).
    selected = _cluster_by_company(selected)
    buckets = {name: [] for name in section_order}
    buckets.setdefault("Other", [])
    for item in selected:
        buckets[item["section"]].append(item)

    # Keep only non-empty sections, in configured order. When the MASH/MASLD bucket
    # carries any folded-AUD item, its header becomes "MASH/MASLD/AUD" -- exactly the
    # combined header the consultant uses in the real mails.
    sections = []
    for name in section_order:
        items = buckets.get(name)
        if not items:
            continue
        display = name
        if name == AUD_FOLD_SECTION and any(it.get("is_aud") for it in items):
            display = "MASH/MASLD/AUD"
        sections.append((display, items))
    return sections, failed


def _style_review_sheet(ws):
    """Bold header, frozen header row, autofilter -- so the sheet is usable for
    review without any manual formatting once it's opened in Excel."""
    from openpyxl.styles import Font

    if ws.max_row < 1 or ws.max_column < 1:
        return
    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    for col_cells in ws.columns:
        length = max((len(str(c.value)) for c in col_cells if c.value is not None), default=0)
        ws.column_dimensions[col_cells[0].column_letter].width = min(max(length + 2, 10), 60)


def write_run_cost_summary_sheet(wb, api_calls):
    """A "Run_Cost_Summary" sheet describing every batch of Parallel AI requests
    this run sent (which stage, which processor tier, how many requests) and what
    it cost -- so the team can see the spend without asking what ran.

    api_calls: list of {"stage": str, "processor": str, "requests_sent": int},
    one entry per pipeline stage that actually called the API (provenance check,
    drafting). Price is looked up per-processor from PROCESSOR_PRICE_PER_1000, the
    same table parallel_triage_agent.py uses, so the two stages' costs are
    comparable and add up to one trustworthy total.
    """
    ws = wb.create_sheet("Run_Cost_Summary")
    ws.append(["Stage (API request description)", "Processor", "Requests sent",
               "Price per 1,000 requests", "Cost"])
    total_requests = 0
    total_cost = 0.0
    for call in api_calls:
        price = PROCESSOR_PRICE_PER_1000.get(call["processor"], 0)
        cost = (call["requests_sent"] / 1000) * price
        total_requests += call["requests_sent"]
        total_cost += cost
        ws.append([call["stage"], call["processor"], call["requests_sent"], f"${price}", f"${cost:.4f}"])
    ws.append([])
    ws.append(["Total API requests sent this run", "", total_requests, "", ""])
    ws.append(["Total cost incurred this run", "", "", "", f"${total_cost:.4f}"])
    _style_review_sheet(ws)


def write_combined_review_workbook(path, triage_rows, decisions, api_calls=None):
    """One sheet: every triage input row, with the selection pipeline's fate for
    that row appended as extra columns -- so a reviewer can cross-check the draft
    against a single file instead of juggling a separate triage sheet and a
    separate selection log. Each triage row is mutated in place as it moves
    through the pipeline (see run_provenance_pass/draft_items/assemble_sections),
    so by the time this is called the row dict already carries its own
    _relevance_score/_draft_section/etc -- no separate join is needed, matching
    on object identity against `decisions` is enough to find its Decision/Reason.

    A second sheet, "Run_Cost_Summary", lists every batch of Parallel AI requests
    this run sent and their cost -- see write_run_cost_summary_sheet().
    """
    order = {"INCLUDED": 0, "candidate (would be drafted)": 1, "dropped (volume cap)": 2,
             "dropped (priority gate)": 3, "dropped (duplicate draft)": 5, "dropped (duplicate source)": 5,
             "dropped (agent error)": 6, "dropped (provenance)": 7,
             "dropped (prefilter)": 8}
    decision_by_row_id = {id(d["row"]): d for d in decisions}

    def decision_for(row):
        return decision_by_row_id.get(id(row))

    ordered_rows = sorted(
        triage_rows,
        key=lambda row: order.get((decision_for(row) or {}).get("decision"), 9),
    )

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Triage_And_Selection"
    triage_header = list(triage_rows[0].keys()) if triage_rows else []
    extra_header = ["Decision", "Reason", "Relevance_Score", "Effective_Score", "Draft_Section",
                     "New_Old_Check", "Page_Source_Role", "Earlier_Publication_URL",
                     "Earliest_Publication_Date", "Provenance_Notes"]
    ws.append(triage_header + extra_header)
    for row in ordered_rows:
        d = decision_for(row)
        ws.append([row.get(col) for col in triage_header] + [
            d["decision"] if d else "", d["reason"] if d else "",
            row.get("_relevance_score", ""), row.get("_effective_score", ""),
            row.get("_draft_section", ""),
            row.get("_new_old") or "", row.get("_page_source_role") or "",
            row.get("_earlier_publication_url") or "", row.get("_earliest_publication_date") or "",
            row.get("_provenance_notes") or "",
        ])
    _style_review_sheet(ws)

    write_run_cost_summary_sheet(wb, api_calls or [])

    wb.save(long_path(path))
    return len(ordered_rows)


# ── HTML rendering (Outlook "Morning Update" house style) ───────────────────

# Font names use single quotes so the whole style string can sit inside a
# double-quoted HTML style="..." attribute without breaking it.
BODY_FONT = "font-family:'Aptos Display',sans-serif;font-size:11.0pt;color:black"
SIG_NAME_STYLE = "font-size:9.0pt;font-family:'Arial',sans-serif;color:#47234B"
SIG_MUTED_STYLE = "font-size:9.0pt;font-family:'Arial',sans-serif;color:#595959"
LIME = "background:lime"


def esc(text):
    return html.escape(str(text or ""), quote=True)


def render_item_html(number, item, show_item_notes=True):
    """One numbered item, matching the markup Outlook produces in the reference
    mails: a single-item <ol start="N">, the source label ITALIC inside the bold
    headline's parentheses, then the sub-bullets as a sibling <ul type="disc">
    (Outlook does not nest the <ul> inside the <li>), then the Evalueserve
    Suggestion as a 36pt-indented paragraph.
    """
    src = item["source_label"]
    url = item["url"]
    note = item["circulation_note"]
    source_html = f'<a href="{esc(url)}">{esc(src)}</a>' if url else esc(src)
    note_html = (
        f' <i><span style="{BODY_FONT};{LIME}">({esc(note)})</span></i>'
        if (note and show_item_notes) else ""
    )
    parts = [
        f'<ol style="margin-top:0cm" start="{number}" type="1">',
        f'<li style="color:black;margin-top:6.0pt;margin-bottom:6.0pt">'
        f'<b><span style="{BODY_FONT}">{esc(item["headline"])} '
        f'(<i>{source_html}</i>) </span></b>{note_html}'
        f'</li>',
        "</ol>",
    ]
    if item["bullets"]:
        parts.append('<ul style="margin-top:0cm" type="disc">')
        for bullet in item["bullets"]:
            parts.append(
                f'<li style="color:black;margin-top:6.0pt;margin-bottom:6.0pt;margin-left:18.0pt">'
                f'<span style="{BODY_FONT}">{esc(bullet)}</span></li>'
            )
        parts.append("</ul>")
    if item["evalueserve_suggestion"] and show_item_notes:
        parts.append(
            f'<p style="margin-top:6.0pt;margin-bottom:6.0pt;margin-left:36.0pt">'
            f'<b><span style="{BODY_FONT}">Evalueserve Suggestion:</span></b>'
            f'<span style="{BODY_FONT}"> {esc(item["evalueserve_suggestion"])}</span></p>'
        )
    return "".join(parts)


def render_html(sections, settings, subject):
    greeting = settings.get("greeting_name", "")
    intro = settings.get("intro_line", "")
    disclaimers = settings.get("section_disclaimers", {})
    consultant = settings.get("consultant", {})

    out = []
    out.append(
        "<html><head><meta charset=\"utf-8\">"
        "<style>body{margin:0;padding:0;} li{margin-bottom:4pt;}</style></head><body>"
    )
    out.append(f'<p style="{BODY_FONT}">Hi {esc(greeting)},</p>')
    out.append(f'<p style="{BODY_FONT}">{esc(intro)}</p>')

    # Numbering RESTARTS at 1 in every section. Confirmed against the reference
    # thread's own HTML, where Outlook emits one <ol> per item with an explicit
    # start=: start="1" appears 60 times across the four mails, start="2" 33
    # times, start="3" 25 -- a continuous counter could not produce that shape.
    for name, items in sections:
        header = f'<b><u><span style="{BODY_FONT}">{esc(name)}</span></u></b>'
        disclaimer = disclaimers.get(name)
        if disclaimer:
            header += (
                f' <b><span style="{BODY_FONT}">-</span></b> '
                f'<i><span style="{BODY_FONT};{LIME}">{esc(disclaimer)}</span></i>'
            )
        out.append(f'<p style="margin-bottom:8.0pt;background:white">{header}</p>')
        # A section-level disclaimer (e.g. "Other") supersedes per-item
        # circulation notes and Evalueserve Suggestions -- matches the client format.
        show_item_notes = disclaimer is None
        for number, item in enumerate(items, start=1):
            out.append(render_item_html(number, item, show_item_notes=show_item_notes))

    # Signature block
    closing = consultant.get("closing", "Kind Regards,")
    name = consultant.get("name", "")
    title = consultant.get("title", "")
    org = consultant.get("org", "")
    phone = consultant.get("phone", "")
    out.append(f'<p style="{SIG_NAME_STYLE}"><b>{esc(closing)}</b></p>')
    sig_line = (
        f'<b><span style="{SIG_NAME_STYLE}">{esc(name)}</span></b>'
        f'<span style="{SIG_MUTED_STYLE}"> | {esc(title)} | {esc(org)} | {esc(phone)}</span>'
    )
    out.append(f'<p>{sig_line}</p>')

    # No "Restricted-Limited Distribution" banner and no confidentiality
    # paragraph here on purpose. In the reference thread the banner is stamped by
    # the sensitivity-label add-on and the confidentiality paragraph is appended
    # once by the mail gateway at the very bottom of the whole thread -- neither
    # is typed by the consultant, so emitting them here would duplicate them in
    # the sent mail.
    out.append("</body></html>")
    return "".join(out)


def render_text(sections, settings, subject):
    """Plain-text mirror for the .txt backup / quick review."""
    lines = []
    lines.append(subject)
    lines.append("")
    lines.append(f"Hi {settings.get('greeting_name','')},")
    lines.append("")
    lines.append(settings.get("intro_line", ""))
    lines.append("")
    disclaimers = settings.get("section_disclaimers", {})
    for name, items in sections:
        disclaimer = disclaimers.get(name)
        header = name
        if disclaimer:
            header += f" - {disclaimer}"
        lines.append(header.upper())
        show_item_notes = disclaimer is None
        # Numbering restarts per section, same as the HTML.
        for number, item in enumerate(items, start=1):
            note = f" ({item['circulation_note']})" if (item["circulation_note"] and show_item_notes) else ""
            lines.append(f"  {number}. {item['headline']} ({item['source_label']}){note}")
            if item["url"]:
                lines.append(f"     Link: {item['url']}")
            for b in item["bullets"]:
                lines.append(f"       - {b}")
            if item["evalueserve_suggestion"] and show_item_notes:
                lines.append(f"     Evalueserve Suggestion: {item['evalueserve_suggestion']}")
            lines.append("")
        lines.append("")
    c = settings.get("consultant", {})
    lines.append(c.get("closing", "Kind Regards,"))
    lines.append("")
    lines.append(f"{c.get('name','')} | {c.get('title','')} | {c.get('org','')} | {c.get('phone','')}")
    return "\n".join(lines)


def _add_docx_hyperlink(paragraph, url, text, font_name='Aptos Display', font_size_pt=11, bold=True, italic=True):
    """Add a true clickable hyperlink to a python-docx paragraph using OpenXML."""
    from docx.shared import Pt, RGBColor
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    if not url:
        run = paragraph.add_run(text)
        run.bold = bold
        run.italic = italic
        if font_name:
            run.font.name = font_name
        if font_size_pt:
            run.font.size = Pt(font_size_pt)
        return run

    part = paragraph.part
    r_id = part.relate_to(url, 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink', is_external=True)

    hyperlink = OxmlElement('w:hyperlink')
    hyperlink.set(qn('r:id'), r_id)

    new_run = OxmlElement('w:r')
    rPr = OxmlElement('w:rPr')

    if font_name:
        rFonts = OxmlElement('w:rFonts')
        rFonts.set(qn('w:ascii'), font_name)
        rFonts.set(qn('w:hAnsi'), font_name)
        rPr.append(rFonts)

    if font_size_pt:
        sz = OxmlElement('w:sz')
        sz.set(qn('w:val'), str(int(font_size_pt * 2)))
        rPr.append(sz)

    color = OxmlElement('w:color')
    color.set(qn('w:val'), '0563C1')  # standard hyperlink blue
    rPr.append(color)

    u = OxmlElement('w:u')
    u.set(qn('w:val'), 'single')
    rPr.append(u)

    if bold:
        b = OxmlElement('w:b')
        rPr.append(b)

    if italic:
        i = OxmlElement('w:i')
        rPr.append(i)

    new_run.append(rPr)

    text_element = OxmlElement('w:t')
    text_element.text = text
    new_run.append(text_element)

    hyperlink.append(new_run)
    paragraph._p.append(hyperlink)


def render_word_document(sections, settings, subject, output_path):
    """Create a Word document replicating the HTML email's formatting.
    Uses python-docx; Aptos Display 11pt body, bold+underlined section headers,
    numbered items with bold headline + italic source label, disc sub-bullets,
    lime-highlighted circulation notes, indented Evalueserve Suggestion,
    and the consultant signature block."""
    from docx import Document
    from docx.shared import Pt, Inches, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    doc = Document()
    style = doc.styles['Normal']
    style.font.name = 'Aptos Display'
    style.font.size = Pt(11)
    style.font.color.rgb = RGBColor(0, 0, 0)
    style.paragraph_format.space_after = Pt(0)
    style.paragraph_format.space_before = Pt(0)

    greeting = settings.get("greeting_name", "")
    intro = settings.get("intro_line", "")
    disclaimers = settings.get("section_disclaimers", {})
    consultant = settings.get("consultant", {})

    # Subject as title
    title_para = doc.add_paragraph()
    title_run = title_para.add_run(subject)
    title_run.bold = True
    title_run.font.size = Pt(14)
    title_run.font.name = 'Aptos Display'
    title_run.font.color.rgb = RGBColor(0, 0, 0)

    # Greeting
    greeting_para = doc.add_paragraph()
    greeting_run = greeting_para.add_run(f"Hi {greeting},")
    greeting_run.font.name = 'Aptos Display'
    greeting_run.font.size = Pt(11)

    # Intro line
    intro_para = doc.add_paragraph()
    intro_run = intro_para.add_run(intro)
    intro_run.font.name = 'Aptos Display'
    intro_run.font.size = Pt(11)

    doc.add_paragraph()  # blank line

    for section_name, items in sections:
        disclaimer = disclaimers.get(section_name)
        show_item_notes = disclaimer is None

        # Section header: bold + underlined
        header_para = doc.add_paragraph()
        header_run = header_para.add_run(section_name)
        header_run.bold = True
        header_run.underline = True
        header_run.font.name = 'Aptos Display'
        header_run.font.size = Pt(11)
        header_run.font.color.rgb = RGBColor(0, 0, 0)

        if disclaimer:
            header_para.add_run(" - ")
            disc_run = header_para.add_run(disclaimer)
            disc_run.italic = True
            disc_run.font.name = 'Aptos Display'
            disc_run.font.size = Pt(11)
            disc_run.font.highlight_color = 7  # WD_COLOR_INDEX.BRIGHT_GREEN / lime

        # Numbered items (numbering restarts at 1 per section)
        for number, item in enumerate(items, start=1):
            # Item paragraph: "N. Headline (Source Label) (circulation note)"
            item_para = doc.add_paragraph()
            item_para.paragraph_format.left_indent = Inches(0.25)
            item_para.paragraph_format.space_before = Pt(6)
            item_para.paragraph_format.space_after = Pt(6)

            # Number + headline (bold)
            num_run = item_para.add_run(f"{number}. ")
            num_run.bold = True
            num_run.font.name = 'Aptos Display'
            num_run.font.size = Pt(11)

            hl_run = item_para.add_run(f"{item['headline']} (")
            hl_run.bold = True
            hl_run.font.name = 'Aptos Display'
            hl_run.font.size = Pt(11)

            # Source label as a true clickable hyperlink
            url = item.get("url") or ""
            _add_docx_hyperlink(item_para, url, item["source_label"], font_name='Aptos Display', font_size_pt=11, bold=True, italic=True)

            close_run = item_para.add_run(")")
            close_run.bold = True
            close_run.font.name = 'Aptos Display'
            close_run.font.size = Pt(11)

            # Circulation note (lime highlight, italic)
            note = item.get("circulation_note", "")
            if note and show_item_notes:
                item_para.add_run(" ")
                note_run = item_para.add_run(f"({note})")
                note_run.italic = True
                note_run.font.name = 'Aptos Display'
                note_run.font.size = Pt(11)
                note_run.font.highlight_color = 7  # lime/bright green

            # Sub-bullets
            for bullet in (item.get("bullets") or []):
                bullet_para = doc.add_paragraph()
                bullet_para.paragraph_format.left_indent = Inches(0.5)
                bullet_para.paragraph_format.space_before = Pt(3)
                bullet_para.paragraph_format.space_after = Pt(3)
                bullet_run = bullet_para.add_run(f"\u2022 {bullet}")
                bullet_run.font.name = 'Aptos Display'
                bullet_run.font.size = Pt(11)
                bullet_run.font.color.rgb = RGBColor(0, 0, 0)

            # Evalueserve Suggestion (indented, bold label)
            suggestion = item.get("evalueserve_suggestion", "")
            if suggestion and show_item_notes:
                sug_para = doc.add_paragraph()
                sug_para.paragraph_format.left_indent = Inches(0.5)
                sug_para.paragraph_format.space_before = Pt(6)
                sug_para.paragraph_format.space_after = Pt(6)
                sug_label = sug_para.add_run("Evalueserve Suggestion: ")
                sug_label.bold = True
                sug_label.font.name = 'Aptos Display'
                sug_label.font.size = Pt(11)
                sug_text = sug_para.add_run(suggestion)
                sug_text.font.name = 'Aptos Display'
                sug_text.font.size = Pt(11)

        doc.add_paragraph()  # blank line between sections

    # Signature block
    closing = consultant.get("closing", "Kind Regards,")
    name = consultant.get("name", "")
    title_str = consultant.get("title", "")
    org = consultant.get("org", "")
    phone = consultant.get("phone", "")

    sig_para1 = doc.add_paragraph()
    sig_run1 = sig_para1.add_run(closing)
    sig_run1.bold = True
    sig_run1.font.name = 'Arial'
    sig_run1.font.size = Pt(9)
    sig_run1.font.color.rgb = RGBColor(0x47, 0x23, 0x4B)

    sig_para2 = doc.add_paragraph()
    name_run = sig_para2.add_run(name)
    name_run.bold = True
    name_run.font.name = 'Arial'
    name_run.font.size = Pt(9)
    name_run.font.color.rgb = RGBColor(0x47, 0x23, 0x4B)
    details_run = sig_para2.add_run(f" | {title_str} | {org} | {phone}")
    details_run.font.name = 'Arial'
    details_run.font.size = Pt(9)
    details_run.font.color.rgb = RGBColor(0x59, 0x59, 0x59)

    doc.save(output_path)
    return output_path


# ── Delivery: Outlook draft + disk backup (never sends) ─────────────────────

def create_outlook_draft(subject, to_list, cc_list, html_body, display):
    """Create an UNSENT Outlook draft. Returns True on success, False if
    Outlook / pywin32 is unavailable. Never calls .Send()."""
    try:
        import win32com.client
    except ImportError:
        print("  [Outlook] pywin32 not available -- skipping Outlook draft (using .htm backup).")
        return False
    try:
        outlook = win32com.client.Dispatch("Outlook.Application")
        mail = outlook.CreateItem(0)  # 0 = olMailItem
        mail.Subject = subject
        if to_list:
            mail.To = "; ".join(to_list)
        if cc_list:
            mail.CC = "; ".join(cc_list)
        mail.HTMLBody = html_body
        mail.Save()  # saves to Drafts; does NOT send
        if display:
            mail.Display(False)  # opens the draft for review (non-modal). Still not sent.
        print("  [Outlook] Draft created in Outlook (UNSENT). Review it, then send manually.")
        return True
    except Exception as e:  # Outlook not installed / COM error
        print(f"  [Outlook] Could not create Outlook draft ({e}). Using .htm backup instead.")
        return False


def pause_if_interactive():
    if os.environ.get("ENDTOEND_ORCHESTRATED"):
        return
    if sys.stdin.isatty():
        try:
            input("\nPress Enter to close this window...")
        except EOFError:
            pass


def main():
    parser = argparse.ArgumentParser(description="Draft the MASH CI Morning Update mail from a triaged workbook (never sends)")
    parser.add_argument("--input", default=None, help="Path to a *_Triaged_*.xlsx file")
    parser.add_argument("--from-triaged", dest="input", help="Alias of --input")
    parser.add_argument("--processor", choices=PROCESSOR_CHOICES, default=None, help="Parallel processor tier for DRAFTING (overrides settings)")
    parser.add_argument("--provenance-processor", choices=PROCESSOR_CHOICES, default=None,
                         help="Parallel processor tier for the open-web provenance pass "
                              "(default 'base' -- its output is an enum/bool/URL, not prose, so it "
                              "does not need the drafting tier's depth)")
    parser.add_argument("--limit", type=int, default=None, help="Only draft the first N candidates that survive the pre-filter (cheap smoke test)")
    parser.add_argument("--no-outlook", action="store_true", help="Skip the Outlook draft; write .htm/.txt only")
    parser.add_argument("--no-display", action="store_true", help="Create the Outlook draft but don't pop it open (still saved to Drafts, still unsent)")
    parser.add_argument("--date", default=None, help="Date shown in the subject (YYYY-MM-DD); default today")
    parser.add_argument("--slot", choices=["Morning", "Evening", "auto"], default="auto",
                         help="Which twice-daily run this is, for the subject/intro line. "
                              "'auto' (default) picks based on the current IST time and "
                              "slot_cutoff_hour_ist in mail_settings.json.")
    parser.add_argument("--window-start", default=None,
                         help="'YYYY-MM-DD HH:MM' (or bare 'YYYY-MM-DD', taken as 00:00) the moment "
                              "the extraction window opened, IST. News that first broke before this "
                              "moment counts as already-mailed; news from at or after it is fresh. "
                              "Run_Agent passes this automatically. Without it we fall back to "
                              "now-minus-slot-lookback, which is wrong for any run over a backdated "
                              "window.")
    parser.add_argument("--dry-run", action="store_true",
                         help="Run the deterministic pre-filter only, write the combined review workbook, and stop. "
                              "Spends no API credit and produces no draft.")
    parser.add_argument("--output-format", choices=["docx", "outlook", "both", "ask"], default="ask",
                         help="Output format: 'docx' for Word document, 'outlook' for Outlook draft, "
                              "'both' for both, 'ask' (default) to prompt interactively.")
    args = parser.parse_args()

    # Unattended runs (launched from Run_EndToEnd.py, or stdin isn't a terminal)
    # never block on a prompt -- they keep today's defaults: proceed, and draft
    # to docx. Only a real interactive terminal gets asked anything.
    interactive = not os.environ.get("ENDTOEND_ORCHESTRATED") and sys.stdin.isatty()

    settings = load_settings()
    processor = args.processor or settings.get("processor", "base")
    provenance_processor = (args.provenance_processor
                            or settings.get("provenance_processor", "base"))

    if args.slot == "auto":
        slot = resolve_update_slot(now_ist(), settings.get("slot_cutoff_hour_ist", 16))
    else:
        slot = args.slot

    # Resolve input
    if not args.input:
        args.input = find_default_input()
        if not args.input:
            sys.exit(
                "No --input given and no *_Triaged_*.xlsx found under Output_Triage_Agent.\n"
                'Run again with: python draft_morning_update.py --input "path\\to\\report_Triaged.xlsx"'
            )
        print(f"No --input given; using newest triaged report: {args.input}")
    if not os.path.exists(long_path(args.input)):
        sys.exit(f"Input file not found: {args.input}")

    # Subject date
    if args.date:
        try:
            date_obj = datetime.strptime(args.date, "%Y-%m-%d")
        except ValueError:
            sys.exit("--date must be YYYY-MM-DD")
    else:
        date_obj = datetime.now()
    date_str = date_obj.strftime(settings.get("date_format", "%d %b %Y"))

    # ── Ask whether to proceed past the free pre-filter into the paid stages ──
    # An explicit --dry-run always short-circuits without asking; otherwise an
    # interactive run gets a chance to bail before spending any API credit.
    if not args.dry_run and interactive:
        answer = input("\nProceed with the provenance check and drafting step? (y/n, default y): ").strip().lower()
        if answer == "n":
            args.dry_run = True
            print("Skipping the provenance check and drafting for this run (dry run only).")

    # ── Resolve output format (only matters if we're actually going to draft) ─
    if args.dry_run:
        output_format = None
    elif args.output_format == "ask":
        if not interactive:
            output_format = "docx"  # unattended default
        else:
            print("\nOutput:")
            word_answer = input("Create a Word document (.docx)? (y/n, default y): ").strip().lower()
            want_word = word_answer != "n"
            outlook_answer = input("Create an Outlook draft? (y/n, default y): ").strip().lower()
            want_outlook_choice = outlook_answer != "n"
            if want_word and want_outlook_choice:
                output_format = "both"
            elif want_outlook_choice:
                output_format = "outlook"
            elif want_word:
                output_format = "docx"
            else:
                output_format = "docx"
                print("Neither was selected; defaulting to Word document so a draft is still produced.")
    else:
        output_format = args.output_format
    if output_format:
        print(f"Output format: {output_format.upper()}")

    subject = settings.get("subject_template", "{slot} Update: {date}").format(slot=slot, date=date_str)
    settings["intro_line"] = settings.get(
        "intro_line", "{slot} monitoring has been completed, and the identified updates are summarized below."
    ).format(slot=slot)
    print(f"Slot: {slot} Update")
    print(f"Processors -> provenance: '{provenance_processor}', drafting: '{processor}'")

    print(f"Reading {args.input} ...")
    try:
        rows = read_triage_sheet(args.input)
    except PermissionError:
        sys.exit(f"Could not read {args.input} -- it looks like it is open in Excel. Close it and retry.")
    print(f"Loaded {len(rows)} triaged rows.")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    safe_date = date_obj.strftime("%Y-%m-%d")
    log_path = os.path.join(OUTPUT_DIR, f"{slot}_Update_{safe_date}_{ts}_Combined_Review.xlsx")
    # Every batch of Parallel AI requests this run sends gets one entry here, so the
    # Run_Cost_Summary sheet always matches what actually ran, including partial
    # runs that exit early (dry-run, or a stage that leaves no candidates).
    api_calls = []
    triage_stats = read_triage_insights_stats(args.input)
    if triage_stats:
        api_calls.append({"stage": "Triage (Parallel AI priority + summary pass)",
                           "processor": triage_stats["processor"],
                           "requests_sent": triage_stats["requests_sent"]})
    else:
        print(f"Note: could not read triage cost stats from {args.input} "
              f"(no Insights sheet found) -- Run_Cost_Summary will omit triage cost.")

    # ── Selection step 1: deterministic pre-filter (no API cost) ─────────────
    candidates, decisions = prefilter_rows(rows, settings)
    print(f"Pre-filter kept {len(candidates)} candidate(s), dropped {len(rows) - len(candidates)}.")
    if args.limit is not None:
        for row in candidates[args.limit:]:
            decisions.append({"row": row, "decision": "dropped (prefilter)",
                              "reason": f"beyond --limit {args.limit}"})
        candidates = candidates[: args.limit]
        print(f"Limited to first {len(candidates)} candidate(s) for this run.")

    if args.dry_run:
        # Record the survivors too -- a log listing only what was dropped can't be
        # used to check that the right items got through.
        for row in candidates:
            decisions.append({"row": row, "decision": "candidate (would be drafted)",
                              "reason": "survived the pre-filter; the provenance pass would check it next"})
        write_combined_review_workbook(log_path, rows, decisions, api_calls)
        print("\n[--dry-run] No API calls made, no draft produced.")
        print(f"Combined review workbook: {log_path}")
        print(f"  {len(candidates)} candidate(s) would have gone to the provenance pass, then drafting.")
        return

    if not candidates:
        write_combined_review_workbook(log_path, rows, decisions, api_calls)
        sys.exit(f"No candidates survived the pre-filter. Nothing to draft. See {log_path}")

    api_key = load_api_key()
    try:
        client = Parallel(api_key=api_key, max_retries=5)
    except TypeError:
        client = Parallel(api_key=api_key)

    # ── Selection step 2: provenance (New/Old) against the OPEN WEB ───────────
    # Runs before drafting so the expensive drafting call is never spent on news the
    # client has already been sent. Uses a cheap tier: the output is a date + enum.
    t0 = time.time()
    provenance_cutoff = _parse_window_start(args.window_start)
    if provenance_cutoff:
        print(f"Provenance cutoff: news that first broke before {provenance_cutoff:%Y-%m-%d %H:%M} IST "
              f"counts as already-seen (extraction window opened then).")
    else:
        lookback_hours = int(settings.get("lookback_hours_by_slot", {}).get(slot, 20))
        provenance_cutoff = now_ist() - timedelta(hours=lookback_hours)
        print(f"Provenance cutoff: news that first broke before {provenance_cutoff:%Y-%m-%d %H:%M} IST "
              f"counts as already-seen (no --window-start given; derived from {slot} lookback "
              f"{lookback_hours}h -- pass --window-start for a backdated window).")
    api_calls.append({"stage": "Provenance check (New/Old verdict against the open web)",
                       "processor": provenance_processor, "requests_sent": len(candidates)})
    run_provenance_pass(client, provenance_processor, candidates, provenance_cutoff)
    before_prov = len(candidates)
    candidates = gate_priority_and_provenance(candidates, decisions)
    print(f"Priority + Provenance gate complete in {time.time() - t0:.0f}s: "
          f"kept {len(candidates)} High/Medium + New item(s), dropped {before_prov - len(candidates)} "
          f"(not High/Medium priority or broke before {provenance_cutoff:%Y-%m-%d %H:%M} IST).")
    # Collapse duplicate coverage now, while it is still free -- the provenance pass
    # has already identified which rows trace back to the same primary source.
    before_dupe = len(candidates)
    candidates = collapse_by_primary_source(candidates, settings, decisions)
    if before_dupe != len(candidates):
        print(f"Duplicate-source collapse: {before_dupe} -> {len(candidates)} item(s) "
              f"({before_dupe - len(candidates)} copies of stories already covered).")
    if not candidates:
        write_combined_review_workbook(log_path, rows, decisions, api_calls)
        sys.exit(f"Every candidate was a republication. Nothing to draft. See {log_path}")

    # ── Selection step 3: LLM gate + drafting, one call per survivor ──────────
    print(f"Drafting with processor '{processor}' ...")
    t0 = time.time()
    api_calls.append({"stage": "Drafting (headline/bullets for the mail)",
                       "processor": processor, "requests_sent": len(candidates)})
    drafted = draft_items(client, processor, candidates)
    print(f"Drafting complete in {time.time() - t0:.0f}s.")

    # ── Selection step 4: rank, cap, bucket ─────────────────────────────────
    sections, failed = assemble_sections(drafted, settings, decisions)
    total_items = sum(len(items) for _name, items in sections)
    capped_out = sum(1 for d in decisions if d["decision"] == "dropped (volume cap)")
    dupe_out = sum(1 for d in decisions if d["decision"] == "dropped (duplicate draft)")
    print(f"Assembled {total_items} item(s) across {len(sections)} section(s) "
          f"(duplicate drafts {dupe_out}, "
          f"volume cap dropped {capped_out}, agent errors {failed}).")
    for name, items in sections:
        print(f"  {name}: {len(items)}")

    write_combined_review_workbook(log_path, rows, decisions, api_calls)
    print(f"Combined review workbook : {log_path}")
    if total_items == 0:
        sys.exit(f"No items survived the mail-worthiness gate. No draft produced. See {log_path}")

    html_body = render_html(sections, settings, subject)
    text_body = render_text(sections, settings, subject)

    htm_path = os.path.join(OUTPUT_DIR, f"{slot}_Update_{safe_date}_{ts}.htm")
    txt_path = os.path.join(OUTPUT_DIR, f"{slot}_Update_{safe_date}_{ts}.txt")
    with open(long_path(htm_path), "w", encoding="utf-8") as f:
        f.write(html_body)
    with open(long_path(txt_path), "w", encoding="utf-8") as f:
        f.write(text_body)
    print(f"\nWrote HTML backup  : {htm_path}")
    print(f"Wrote text backup  : {txt_path}")

    if output_format in ("docx", "both"):
        docx_path = os.path.join(OUTPUT_DIR, f"{slot}_Update_{safe_date}_{ts}.docx")
        render_word_document(sections, settings, subject, docx_path)
        print(f"Wrote Word document: {docx_path}")
    if output_format in ("outlook", "both"):
        want_outlook = settings.get("create_outlook_draft", True) and not args.no_outlook
        if want_outlook:
            display = settings.get("display_outlook_draft", True) and not args.no_display
            create_outlook_draft(
                subject,
                settings.get("to", []),
                settings.get("cc", []),
                html_body,
                display,
            )
        else:
            print("  [Outlook] Skipped (per --no-outlook or settings).")

    print("\n" + "=" * 70)
    print(f"  {slot.upper()} UPDATE DRAFT READY ({output_format.upper()})")
    print(f"  Subject : {subject}")
    print(f"  Items   : {total_items}  |  Sections : {len(sections)}")
    print("  NOTE: nothing was sent. Review the draft and send manually.")
    print("=" * 70)


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
