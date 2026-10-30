"""
rss_opml_common.py  –  Shared utilities for the three OPML-driven RSS scripts.

Improvements over Script v4_Other RSS.py
-----------------------------------------
1. OPML-driven  : feeds come from .opml files instead of a hardcoded list.
2. Concurrent   : all feeds are fetched in parallel via ThreadPoolExecutor,
                  cutting run time from O(N * latency) to O(latency).
3. Auto-type    : source type (Google News / ClinicalTrials / Google Alert /
                  PubMed / Company IR / Trade Publication) is inferred from URL.
4. Auto-target  : primary drug/company name extracted from the OPML text attribute.
5. Deduplication: entries with the same (target, URL) are dropped only once.
6. Feed_Description column: preserves the full OPML query text for auditability.
7. Date-stamped output: filename includes YYYY-MM-DD so runs never overwrite.
"""

import datetime
import html
import os
import re
import socket
import sys
import time

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

import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from types import SimpleNamespace
from urllib.parse import urlparse

try:
    import feedparser
    import pandas as pd
    import requests
except ImportError as _e:
    import sys
    print("\n[ERROR] A required package is not installed.")
    print(f"        Missing: {_e}")
    print("\n  Run this in your terminal to install everything needed:")
    print("      pip install feedparser pandas openpyxl requests\n")
    input("Press Enter to close...")
    sys.exit(1)

# ── Network hardening ────────────────────────────────────────────────────────
socket.setdefaulttimeout(15.0)

# ── DNS fallback via public resolvers ────────────────────────────────────────
# The corporate DNS (192.168.0.1) silently drops lookups for some foreign
# government domains (www.pmda.go.jp, www.tga.gov.au) and intermittently
# fails for others (eutils.ncbi.nlm.nih.gov). Fix: patch socket.getaddrinfo
# so that when the OS resolver returns NXDOMAIN/timeout, we retry the lookup
# against Google (8.8.8.8) and Cloudflare (1.1.1.1) via dnspython, which
# bypasses the corporate resolver entirely.
def _install_dns_fallback():
    try:
        import dns.resolver as _dr
    except ImportError:
        return   # dnspython not installed — skip silently

    _pub = _dr.Resolver()
    _pub.nameservers = ["8.8.8.8", "1.1.1.1"]
    _pub.lifetime = 5.0
    _cache = {}
    _orig = socket.getaddrinfo

    def _patched_getaddrinfo(host, port, *args, **kwargs):
        # Let the OS resolver try first (fast path for normal hosts).
        try:
            return _orig(host, port, *args, **kwargs)
        except OSError:
            pass
        # OS resolver failed — try public DNS.
        if host not in _cache:
            try:
                ans = _pub.resolve(host, "A")
                _cache[host] = str(ans[0])
            except Exception:
                raise OSError(f"DNS resolution failed for {host} (corporate + public)")
        return _orig(_cache[host], port, *args, **kwargs)

    socket.getaddrinfo = _patched_getaddrinfo

_install_dns_fallback()

# ── Corporate proxy detection ────────────────────────────────────────────────
# On corporate networks (e.g. Evalueserve) Python's requests does NOT use the
# browser proxy automatically. trust_env only reads HTTP_PROXY/HTTPS_PROXY env
# vars — it does NOT read the Windows Internet Settings (WinINet) that Chrome
# uses. That mismatch is why some external hosts (rss.nytimes.com,
# www.fiercebiotech.com, investor.merck.com, ...) fail with "host not found"
# here yet load fine in the browser. We fix this by reading the WinINet proxy
# from the registry and applying it to the session.
_SESSION = requests.Session()
_SESSION.trust_env = True    # still honour explicit HTTP_PROXY/HTTPS_PROXY env vars


def _configure_windows_proxy():
    """
    Detect the Windows system (WinINet) proxy — the one the browser uses — and
    apply it to _SESSION so requests can reach the same hosts the browser can.
    Explicit HTTP(S)_PROXY env vars (already honoured via trust_env) win.
    No-op on non-Windows or when no proxy is configured.

    IMPORTANT: this is OPT-IN (set RSS_USE_WININET_PROXY=1). On networks that
    reach the internet directly, blindly forcing every request through the
    registry proxy breaks the hosts that were working (Entrez, ClinicalTrials,
    Google), so it must not run by default.
    """
    if os.environ.get("RSS_USE_WININET_PROXY", "").strip() not in ("1", "true", "yes"):
        return None
    if os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY"):
        return None
    try:
        import winreg
    except ImportError:
        return None
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            if not enabled:
                return None
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
    except OSError:
        return None
    if not server:
        return None

    proxies = {}
    if "=" in server:
        # Per-protocol form: "http=host:port;https=host:port"
        for part in server.split(";"):
            if "=" in part:
                scheme, _, addr = part.partition("=")
                proxies[scheme.strip()] = addr.strip()
        http_p  = proxies.get("http")  or proxies.get("https")
        https_p = proxies.get("https") or proxies.get("http")
    else:
        http_p = https_p = server.strip()   # single proxy for all protocols

    def _norm(p):
        return p if (not p or p.startswith("http")) else f"http://{p}"

    applied = {}
    if http_p:
        applied["http"] = _norm(http_p)
    if https_p:
        applied["https"] = _norm(https_p)
    if applied:
        _SESSION.proxies.update(applied)
        return applied.get("https") or applied.get("http")
    return None


_DETECTED_PROXY = _configure_windows_proxy()
if _DETECTED_PROXY:
    print(f"  [PROXY] Using Windows system proxy: {_DETECTED_PROXY}")

# Full browser headers — feedparser's built-in urllib fetcher gets blocked by
# sites like ClinicalTrials.gov. We use requests for the HTTP layer instead,
# then pass raw content to feedparser for parsing only.
BROWSER_HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept":          "application/rss+xml, application/xml, text/xml, */*",
    "Accept-Language": "en-US,en;q=0.9",
    # NOTE: 'br' (brotli) is deliberately NOT advertised. requests only auto-
    # decodes gzip/deflate; without the optional `brotli` package a brotli
    # response reaches feedparser as raw compressed bytes and fails with
    # "not well-formed (invalid token)" (the BOZO errors seen for GSK,
    # FiercePharma, J&J, PharmaTimes, etc.). Restricting to gzip/deflate makes
    # servers fall back to encodings requests can decode.
    "Accept-Encoding": "gzip, deflate",
    "Cache-Control":   "no-cache",
}

# PubMed needs a Referer header and rate-limiting — firing too many at once
# triggers HTTP 403. Use a semaphore to allow max 2 concurrent PubMed requests.
import threading
_PUBMED_LOCK = threading.Semaphore(2)

# ── NCBI Entrez E-utilities (durable fix for PubMed HTTP 403) ────────────────
# The public PubMed RSS endpoint (/rss/search/<id>) is blocked for automated
# clients. The officially-supported path is the Entrez E-utilities API. With an
# API key NCBI allows 10 requests/sec and does not 403 the way the RSS endpoint
# does. Provide credentials via environment variables (never hard-code the key):
#     set NCBI_API_KEY=<your key>
#     set NCBI_EMAIL=<your contact email>      (NCBI requests a contact address)
def _load_ncbi_credentials():
    """
    Resolve NCBI credentials. Environment variables win; otherwise fall back to
    a local 'ncbi_credentials.txt' next to this file containing lines like:
        NCBI_API_KEY=...
        NCBI_EMAIL=...
    """
    key   = os.environ.get("NCBI_API_KEY", "").strip()
    email = os.environ.get("NCBI_EMAIL", "").strip()
    cred_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "ncbi_credentials.txt")
    if (not key or not email) and os.path.exists(cred_path):
        try:
            with open(cred_path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    name, _, val = line.partition("=")
                    name, val = name.strip().upper(), val.strip()
                    if name == "NCBI_API_KEY" and not key:
                        key = val
                    elif name == "NCBI_EMAIL" and not email:
                        email = val
        except OSError:
            pass
    return key, email


NCBI_API_KEY, NCBI_EMAIL = _load_ncbi_credentials()
NCBI_TOOL    = "eval_rss_alerts"
_EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

# ── Entrez rate limiting ─────────────────────────────────────────────────────
# NCBI caps requests at 10/sec with an API key, 3/sec without. The semaphore
# above bounds *concurrency* (2 in flight) but not the *rate*: with fast
# responses two slots can momentarily burst over the cap. A global gate that
# spaces request STARTS by a minimum interval hard-bounds the aggregate rate no
# matter how many worker threads call in, keeping us safely under NCBI's limit.
#   with key: 0.12 s  -> ≤ ~8.3 req/s   (cap 10)
#   no  key:  0.34 s  -> ≤ ~2.9 req/s   (cap 3)
_ENTREZ_MIN_INTERVAL = 0.12 if NCBI_API_KEY else 0.34
_ENTREZ_RATE_LOCK = threading.Lock()
_ENTREZ_LAST_START = [0.0]   # monotonic timestamp of the last request start


def _entrez_rate_gate():
    """Block until at least _ENTREZ_MIN_INTERVAL has elapsed since the previous
    request start, then record this start. Serialises only the scheduling, not
    the HTTP call itself, so requests still overlap up to the semaphore limit."""
    with _ENTREZ_RATE_LOCK:
        now = time.monotonic()
        wait = _ENTREZ_MIN_INTERVAL - (now - _ENTREZ_LAST_START[0])
        if wait > 0:
            time.sleep(wait)
        _ENTREZ_LAST_START[0] = time.monotonic()

# ── Known trade publication domains ─────────────────────────────────────────
TRADE_PUB_DOMAINS = {
    "fiercebiotech.com":     "Trade Publication (FierceBiotech)",
    "fiercepharma.com":      "Trade Publication (FiercePharma)",
    "biopharmadive.com":     "Trade Publication (BioPharma Dive)",
    "statnews.com":          "Trade Publication (STAT News)",
    "biospace.com":          "Trade Publication (BioSpace)",
    "newsloth.com":          "Trade Publication (BioSpace)",     # BioSpace proxy
    "globenewswire.com":     "Trade Publication (GlobeNewswire)",
    "endpoints.news":        "Trade Publication (Endpoints News)",
    "endpts.com":            "Trade Publication (Endpoints News)",
    "businesskorea.co.kr":   "Trade Publication (BusinessKorea)",
    "pharmaxis.com.au":      "Company News (Pharmaxis)",
}

# ── Keyword triage (2nd-level filter, applied after the date filter) ────────
# Checked against Update_Title + Summary_Payload. Hyphenated/non-hyphenated and
# spaced/non-spaced variants are kept as distinct literal strings because
# word-boundary matching (see _compile_keyword_patterns) treats them literally.
KEYWORD_CATEGORIES = {
    "MASH/NASH": [
        "MASH", "NASH", "MASLD", "NAFLD", "MAFLD", "SLD", "Steatotic Liver Disease",
        "Metabolic dysfunction-associated steatohepatitis",
        "Metabolic dysfunction associated steatohepatitis",
        "Metabolic dysfunction-associated Steatotic liver disease",
        "Metabolic dysfunction-associated fatty liver disease",
        "Nonalcoholic Steatohepatitis", "Non-alcoholic Steatohepatitis",
        "Nonalcoholic Fatty Liver Disease", "Non-alcoholic Fatty Liver Disease",
        "Nonalcoholic Fatty Liver", "Non-alcoholic Fatty Liver", "Fatty Liver Disease",
        "Hepatic impairment", "Liver Fibrosis", "advanced fibrosis", "F3", "F4", "cF4", "Fc4",
        "compensated cirrhosis", "MASH cirrhosis", "MASLD cirrhosis", "cirrhosis",
        "portal hypertension", "CSPH", "clinically significant portal hypertension",
        "liver-related events", "hepatic decompensation",
    ],
    "ALD/MetALD": [
        "ALD", "MetALD", "AALD", "Alcoholic Liver Disease",
        "Alcohol-Associated Liver Disease", "Alcohol-associated Liver Disease",
        "Alcohol-Related Liver Disease", "alcohol-related liver disease",
        "Alcohol Induced Liver Injury", "Alcoholic Steatohepatitis",
        "Alcoholic Fatty Liver", "Metabolic dysfunction and alcohol-related liver disease",
    ],
    "AUD": [
        "AUD", "Alcohol Use Disorder", "Alcohol-Related Disorders", "Alcohol Abuse",
        "Alcohol Dependence", "Alcohol Addiction", "Alcohol Misuse", "alcoholism",
    ],
}

# ── Obesity / T2D / weight-management exclusion (3rd-level filter) ──────────
# Removed from scope on 03 Aug 2026: the client no longer wants obesity, type 2
# diabetes or weight-management updates in any form -- not as a de-prioritised
# "Obesity / T2D" mail section, not folded into "Other". These terms used to be
# the "Obesity/Metabolic" KEYWORD_CATEGORIES entry above, i.e. they QUALIFIED an
# item for the Priority sheet; they now VETO it instead.
#
# The veto is deliberately blunt: an item is dropped if it mentions any of these
# terms, even alongside MASH. That is the agreed rule, but it has real collateral
# cost -- most MASH releases describe the population as "adults with MASH and
# obesity", and every priority incretin asset (survodutide, semaglutide,
# tirzepatide, retatrutide, pemvidutide, efinopegdutide) routinely mentions
# weight loss. Such items ARE dropped under the default setting.
#
# If a run's Priority sheet collapses relative to the previous day, flip the
# constant below to True: the rule then becomes "drop only when there is no
# MASH/ALD/AUD signal at all", which keeps a survodutide MASH readout while still
# dropping a Wegovy weight-loss label update. That is the only change needed.
OBESITY_VETO_OVERRIDDEN_BY_MASH = False

EXCLUSION_KEYWORDS = {
    "Obesity/Metabolic": [
        # original "Obesity/Metabolic" category terms
        "Obesity", "Weight loss", "Weight management", "Diabetes", "T2D",
        # variants the original five missed
        "obese", "weight-loss", "weight reduction", "body weight", "BMI",
        "type 2 diabetes", "type II diabetes", "T2DM", "anti-obesity",
        "bariatric", "GLP-1 for weight",
    ],
}

# ── Obesity in-scope exception: Novo Nordisk / Eli Lilly / Roche only ───────
# Obesity is otherwise out of scope (EXCLUSION_KEYWORDS above). As of 13 Aug
# 2026, the client wants obesity news back in scope ONLY when it concerns
# Novo Nordisk, Eli Lilly or Roche and their products -- obesity news about
# any other company (Pfizer, Amgen, Zealand, Viking, etc.) stays vetoed
# exactly as before. See obesity_allowed_match() and its use in obesity_veto().
OBESITY_ALLOWED_ENTITIES = {
    "Novo Nordisk": [
        "Novo Nordisk", "Wegovy", "Ozempic", "Rybelsus", "semaglutide",
        "CagriSema", "cagrilintide", "amycretin",
        # Obesity pipeline assets added 14 Aug 2026
        "Amylin 355", "Amylin 1213", "SLC25A5", "Next-gen GLP-1", "ACSL5i",
        "Zenagamtide", "UBT251", "Monlunabant",
    ],
    "Eli Lilly": [
        "Eli Lilly", "Lilly", "Mounjaro", "Zepbound", "tirzepatide",
        "retatrutide", "orforglipron", "Foundayo",
        # Obesity pipeline assets added 14 Aug 2026
        "Eloralintide", "Bimagrumab", "Macupatide", "Naperiglipron",
    ],
    "Roche": [
        "Roche", "Genentech", "petrelintide", "CT-388", "CT-996",
        # Obesity pipeline assets added 14 Aug 2026
        "Enicepatide", "RG6640", "RG6849", "RG6652", "Emugrobart", "GYM 329",
        "RG6237",
    ],
}
# NOTE: "Triple" (as a bare standalone term for Novo's UBT251 tri-agonist) was
# deliberately left out -- it is far too generic a word and would false-positive
# on ordinary text ("triple-digit growth", "triple therapy", etc.). UBT251 itself
# is listed above and already covers this asset; flag to the user if a more
# specific qualified phrase is wanted instead.


def _compile_keyword_patterns(categories: dict) -> dict:
    """One compiled case-insensitive, word-boundary alternation regex per category."""
    return {
        cat: re.compile(r"\b(?:" + "|".join(re.escape(k) for k in kws) + r")\b", re.IGNORECASE)
        for cat, kws in categories.items()
    }


_KEYWORD_PATTERNS = _compile_keyword_patterns(KEYWORD_CATEGORIES)
# Same word-boundary semantics as the inclusion patterns, so "BMI" does not fire
# on "BMIQ" and "obese" does not fire inside "obesely".
_EXCLUSION_PATTERNS = _compile_keyword_patterns(EXCLUSION_KEYWORDS)
# Flattened into one category so the existing compile helper can be reused as-is.
_OBESITY_ALLOWED_PATTERN = _compile_keyword_patterns(
    {"Obesity (Novo/Lilly/Roche)": [term for terms in OBESITY_ALLOWED_ENTITIES.values() for term in terms]}
)["Obesity (Novo/Lilly/Roche)"]


def obesity_allowed_match(title: str, summary: str):
    """
    True if the item names Novo Nordisk, Eli Lilly, Roche, or one of their
    obesity products (OBESITY_ALLOWED_ENTITIES) -- the narrow exception that
    lets an otherwise-vetoed obesity/T2D item back into scope.

    Returns (matched, matched_terms), same case/de-dup contract as
    keyword_match_info's matched_terms.
    """
    text = f"{title or ''} {summary or ''}"
    terms, seen = [], set()
    for term in _OBESITY_ALLOWED_PATTERN.findall(text):
        key = term.lower()
        if key not in seen:
            seen.add(key)
            terms.append(term)
    return bool(terms), terms


def keyword_match_info(title: str, summary: str):
    """
    Check Update_Title + Summary_Payload against every keyword category.
    Returns (is_match, matched_categories, matched_terms). matched_terms preserves
    the original casing found in the source text, de-duplicated case-insensitively,
    in first-seen order.
    """
    text = f"{title or ''} {summary or ''}"
    matched_categories, matched_terms, seen = [], [], set()
    for cat, pattern in _KEYWORD_PATTERNS.items():
        found = pattern.findall(text)
        if not found:
            continue
        matched_categories.append(cat)
        for term in found:
            key = term.lower()
            if key not in seen:
                seen.add(key)
                matched_terms.append(term)
    return bool(matched_categories), matched_categories, matched_terms


def obesity_veto(title: str, summary: str, matched_categories=None):
    """
    Decide whether an item is an out-of-scope obesity / T2D / weight-management
    update. See EXCLUSION_KEYWORDS and OBESITY_VETO_OVERRIDDEN_BY_MASH above.

    Returns (vetoed, matched_exclusion_terms). matched_exclusion_terms preserves
    the original casing found in the source text, de-duplicated case-insensitively,
    in first-seen order -- same contract as keyword_match_info's third element, so
    it can be written straight into a report column. Note matched_exclusion_terms
    is still populated even when vetoed=False via the Novo/Lilly/Roche exception
    below, so a caller can tell "no obesity signal" apart from "obesity signal,
    waived".
    """
    text = f"{title or ''} {summary or ''}"
    matched_terms, seen = [], set()
    for pattern in _EXCLUSION_PATTERNS.values():
        for term in pattern.findall(text):
            key = term.lower()
            if key not in seen:
                seen.add(key)
                matched_terms.append(term)

    if not matched_terms:
        return False, []

    # Relaxed mode: an in-scope liver signal outranks the obesity mention.
    if OBESITY_VETO_OVERRIDDEN_BY_MASH:
        if matched_categories is None:
            _m, matched_categories, _t = keyword_match_info(title, summary)
        if any(cat in ("MASH/NASH", "ALD/MetALD", "AUD") for cat in matched_categories):
            return False, matched_terms

    # Narrow exception: obesity news naming Novo Nordisk, Eli Lilly, Roche, or
    # one of their products stays in scope; every other company's obesity/T2D
    # news is still vetoed below.
    allowed, _allowed_terms = obesity_allowed_match(title, summary)
    if allowed:
        return False, matched_terms

    return True, matched_terms


COLUMNS = [
    "Extraction_Timestamp",
    "Asset_Target",
    "Indication",       # MASH | AUD | ALD/MetALD | MOA | NIT | Company | Industry Media | Literature
    "Stream_Source",
    "Feed_Description",
    "Entry_ID",         # NCT number (ClinicalTrials) | PMID (PubMed) | article guid
    "Update_Title",
    "Summary_Payload",
    "Keyword_Match",        # Yes | No — did title/summary match any tracked keyword?
    "Matched_Categories",   # MASH/NASH; ALD/MetALD; AUD (semicolon-joined)
    "Matched_Keywords",     # exact matched terms, original casing (semicolon-joined)
    "Excluded_Reason",      # why an Included row was kept out of the Priority sheet, e.g. the obesity/T2D veto
    "Author",           # author name(s) — PubMed, some news articles
    "Publisher",        # source publication — Google News entries
    "Tags",             # categories / MeSH terms — PubMed, some trade pubs
    "Published_Date",           # raw string exactly as returned by the feed — format AND timezone vary by source
    "Published_Date_Uniform",   # "DD-MM-YYYY HH:MM:SS IST" — source timezone converted to IST for sorting/filtering
    "Updated_Date",             # dc:date / last updated — ClinicalTrials first-posted date (raw, source timezone)
    "Updated_Date_Uniform",     # "DD-MM-YYYY HH:MM:SS IST" — source timezone converted to IST for sorting/filtering
    "Source_URL",
]


def extract_indication(feed_description: str, group: str) -> str:
    """
    Derive the therapeutic indication/category for a feed from its group name
    (the parent OPML outline) and feed description text.

    Group names in the OPML files are structured as:
        "<Indication> — <Feed type>"   e.g. "AUD — Competitor CT Feeds"
    This prefix is the primary signal; the feed text is a fallback.
    """
    g = group.lower()
    fd = feed_description.lower()

    # ── 1. Group name prefix (primary — set by the OPML generator) ───────────
    if g.startswith('aud'):
        return 'AUD'
    if g.startswith('ald/metald') or g.startswith('ald / metald'):
        return 'ALD/MetALD'
    if g.startswith('obesity'):
        return 'Obesity'
    if g.startswith('moa'):
        return 'MOA'
    if g.startswith('nit'):
        return 'NIT'
    if g.startswith('literature'):
        return 'Literature'
    if 'industry media' in g or 'trade publication' in g or 'wire service' in g or 'regional' in g:
        return 'Industry Media'
    if 'company' in g:
        return 'Company'

    # ── 2. Explicit tags embedded in the feed text ────────────────────────────
    if '[aud]' in fd:
        return 'AUD'
    if 'ald/metald' in fd or 'ald / metald' in fd or 'metald' in fd:
        return 'ALD/MetALD'
    if '[mash]' in fd:
        return 'MASH'
    if 'obesity' in fd and 'indication' in g:
        return 'Obesity'

    # ── 3. Default for anything under a MASH-prefixed or unknown group ────────
    return 'MASH'

# ── Text utilities ────────────────────────────────────────────────────────────

def clean_text(raw):
    """Strip HTML tags, collapse whitespace, unescape XML entities."""
    if not raw:
        return "N/A"
    text = re.sub(r"<[^>]+>", " ", raw)
    text = re.sub(r"\s+", " ", text)
    return html.unescape(text).strip()


def _get_entry_id(entry) -> str:
    """
    Return the best available unique identifier for an entry.
      ClinicalTrials → NCT number  (e.g. NCT07335601)
      PubMed         → PMID        (e.g. 38451234)
      Others         → guid / id   (may be a URL or opaque string)
    """
    raw_id = getattr(entry, "id", None) or getattr(entry, "guid", None)
    if not raw_id:
        return "N/A"
    # If the id is just the article URL, skip it (Source_URL already has it)
    if raw_id.startswith("http"):
        return "N/A"
    return raw_id.strip()


def _get_author(entry) -> str:
    """Return a semicolon-separated list of author names if available."""
    # Try the authors list first (feedparser 6.x)
    authors = getattr(entry, "authors", [])
    if authors:
        names = [a.get("name", "").strip() for a in authors if a.get("name")]
        if names:
            return "; ".join(names)
    # Fall back to single author string
    author = getattr(entry, "author", None)
    if author:
        return clean_text(author)
    return "N/A"


def _get_publisher(entry) -> str:
    """
    Return the source publication name.
    Google News puts the outlet name (e.g. 'Reuters') in entry.source.title.
    """
    src = getattr(entry, "source", None)
    if src:
        if isinstance(src, dict):
            title = src.get("title", "")
        else:
            title = getattr(src, "title", "")
        if title:
            return clean_text(title)
    return "N/A"


def _get_tags(entry) -> str:
    """Return a semicolon-separated list of category / MeSH tags if available."""
    tags = getattr(entry, "tags", [])
    if not tags:
        return "N/A"
    terms = [t.get("term", "").strip() for t in tags if t.get("term")]
    return "; ".join(terms) if terms else "N/A"


def _get_updated(entry) -> str:
    """
    Return the 'updated' date string.
    For ClinicalTrials this is dc:date = the first-posted date.
    """
    return getattr(entry, "updated", None) or getattr(entry, "dc_date", None) or "N/A"


def detect_source_type(url: str) -> str:
    """Classify a feed URL into a human-readable source category."""
    u = url.lower()
    if "news.google.com" in u:
        return "Media (Google News)"
    if "clinicaltrials.gov" in u:
        return "Registry (ClinicalTrials)"
    if "google.co.in/alerts" in u or "google.com/alerts" in u:
        return "Media (Google Alert)"
    if "pubmed.ncbi.nlm.nih.gov" in u:
        return "Literature (PubMed)"

    domain = urlparse(url).netloc.lower()
    # Strip common sub-domain prefixes to match the dict keys
    for prefix in ("www.", "ir.", "investor.", "investors.", "app."):
        domain = domain.replace(prefix, "", 1)

    for known_domain, label in TRADE_PUB_DOMAINS.items():
        if known_domain in domain:
            return label

    return f"Company IR ({domain})"


def extract_target(text: str) -> str:
    """
    Pull the primary drug name / company name from an OPML outline text.
    Priority order:
      1. First double-quoted token that looks like a drug ID (no OR/AND inside).
      2. First unquoted token from a parenthesised OR-list.
      3. Company name before known suffixes (News / SEC / Events / Inc.).
      4. First five words as a fallback.
    """
    # 1. First quoted term with no boolean operators inside
    for m in re.finditer(r'"([^"]{2,60})"', text):
        term = m.group(1).strip()
        if not re.search(r"\bOR\b|\bAND\b", term, re.I):
            return term

    # 2. First token in an OR-list (quoted or unquoted)
    m = re.search(
        r'(?:Pubmed\s*\(|Alert\s*-\s*\(|keyword:\s*\()\s*["\s]*'
        r'([A-Za-z0-9][A-Za-z0-9\-\s\.]{0,40}?)'
        r'(?:\s+OR\b|"\s+OR|"\s*\)|\))',
        text, re.I,
    )
    if m:
        return m.group(1).strip().strip('"')

    # 3. Company/publication name before a known trailing word
    m = re.match(
        r"^([A-Z][A-Za-z0-9\s,\.\-]+?)\s+"
        r"(?:News|SEC|Events|Inc\.|Ltd\.|All\s|Investor|Press|Presentations|Releases)",
        text,
    )
    if m:
        return m.group(1).strip()

    # 4. Fallback
    return " ".join(text.split()[:5])


# ── OPML parser ───────────────────────────────────────────────────────────────

def parse_opml(opml_path: str) -> list:
    """
    Parse an OPML file and return a list of feed dicts:
      {"Target", "Type", "URL", "Feed_Description", "Group"}
    Handles nested <outline> grouping. The "Group" field holds the immediate
    parent outline text (e.g. "AUD — Competitor CT Feeds") so the pipeline
    can derive the Indication column from it.
    """
    tree = ET.parse(opml_path)
    root = tree.getroot()
    feeds = []

    def walk(element, parent_group=""):
        for outline in element.findall("outline"):
            url  = outline.get("xmlUrl", "").strip()
            text = (outline.get("text") or outline.get("title") or "").strip()
            if url:
                feeds.append(
                    {
                        "Target":           extract_target(text),
                        "Type":             detect_source_type(url),
                        "URL":              url,
                        "Feed_Description": text,
                        "Group":            parent_group,
                    }
                )
                walk(outline, parent_group=parent_group)
            else:
                # This is a group outline — its text becomes the group for children
                walk(outline, parent_group=text)

    body = root.find("body")
    if body is not None:
        walk(body)
    return feeds


# ── Date filter helpers ───────────────────────────────────────────────────────

def get_date_filters():
    """
    Interactively prompt for start / end date. Returns (start_dt, end_dt).
    Dates entered here are treated as IST calendar dates — entries are
    converted from their source timezone to IST before comparison (see
    entry_in_date_range), so this filter is correct regardless of which
    timezone the underlying feed reports.
    """
    print("\n" + "=" * 85)
    print("DATE FILTER CONFIGURATION")
    print("=" * 85)
    print("Filter results to a specific date range (based on article publication date).")
    print("Dates are interpreted in IST (source feeds in other timezones are converted).")
    print("Optionally add a 24-hour time for finer control, e.g. '2026-07-02 14:30'.")
    print("Press ENTER to skip and retrieve all available results.\n")

    # Tried in order; only the last (date-only) format omits a time component.
    _FMTS = ["%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]

    def ask(label):
        """Return (datetime, had_time) or (None, False) if skipped."""
        while True:
            raw = input(
                f"  Enter {label} date (YYYY-MM-DD or YYYY-MM-DD HH:MM) or ENTER to skip: "
            ).strip()
            if not raw:
                return None, False
            for fmt in _FMTS:
                try:
                    dt = datetime.datetime.strptime(raw, fmt)
                    return dt, fmt != "%Y-%m-%d"
                except ValueError:
                    continue
            print("    [ERROR] Invalid format — use YYYY-MM-DD or YYYY-MM-DD HH:MM "
                  "(e.g. 2025-01-15 or 2025-01-15 14:30).")

    start_dt, _          = ask("START")
    end_dt, end_had_time = ask("END")

    # Only default END to end-of-day when the user didn't type an explicit time.
    if end_dt and not end_had_time:
        end_dt = end_dt.replace(hour=23, minute=59, second=59)

    if start_dt and end_dt and start_dt > end_dt:
        print("    [WARNING] Start is after end — filter ignored.")
        return None, None

    if start_dt or end_dt:
        s = start_dt.strftime("%Y-%m-%d %H:%M") if start_dt else "beginning"
        e = end_dt.strftime("%Y-%m-%d %H:%M")   if end_dt   else "today"
        print(f"\n  [FILTER ACTIVE] {s} -> {e}")
    else:
        print("\n  [NO FILTER] Fetching all available results.")

    print("=" * 85 + "\n")
    return start_dt, end_dt


# Common date string formats seen across feed sources (tried in order).
# The last 4 entries were added after a live audit of all 569 OPML feeds
# (2026-07-01) found these formats slipping past every prior fallback and
# silently bypassing the date-range filter (entries always included):
#   - FierceBiotech / FiercePharma : "Jun 30, 2026 10:38am"  (12-hour, no weekday)
#   - Akero Therapeutics SEC Filings: "Feb 17, 2026"          (abbrev. month, no weekday/time)
#   - MediciNova SEC Filings        : "06/25/26"              (MM/DD/YY)
#   - Terns/Aligos/89bio (occasional): "Wed, 03 Dec 2025"     (RFC822 weekday, no time —
#     email.utils.parsedate requires a time component and returns None for these)
_DATE_FMTS = [
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%d %b %Y",
    "%B %d, %Y",
    "%d %B %Y",
    "%b %d, %Y %I:%M%p",
    "%b %d, %Y",
    "%m/%d/%y",
    "%a, %d %b %Y",
]


# The CI team works in IST; source feeds span US Eastern (EDT/EST), GMT/UTC,
# Japan/Australia/Europe (numeric offsets), and several formats that carry no
# timezone at all. All display + filtering logic below normalizes to IST so
# entries from different source timezones are directly comparable.
IST_OFFSET = datetime.timedelta(hours=5, minutes=30)
IST_LABEL  = "IST"


def now_ist() -> datetime.datetime:
    """Current naive IST wall-clock time, computed from UTC rather than trusting
    the local machine's timezone (the same safe convention run_extraction.py
    uses for _now_ist_naive)."""
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) + IST_OFFSET


def resolve_update_slot(dt: datetime.datetime, cutoff_hour: int = 16) -> str:
    """Label a monitoring run "Morning" or "Evening" based on IST wall-clock
    time. The team's actual cadence is a ~12:40 PM-2:15 PM IST run ("Morning
    Update") and a ~8:30 PM-10:05 PM IST run ("Evening Update") -- never a
    literal early-morning or true-afternoon send -- so the cutoff just needs to
    fall cleanly between those two windows. Default cutoff (16:00 IST / 4 PM)
    does that with margin on both sides."""
    return "Morning" if dt.hour < cutoff_hour else "Evening"


def long_path(path) -> str:
    """Windows extended-length path ("\\\\?\\...") so os.path/openpyxl calls
    can still resolve a file under this project's deeply nested folder
    structure -- a full path here (long project/OneDrive folder names plus
    dated/suffixed generated filenames) routinely lands at or past Windows'
    260-char MAX_PATH limit, where plain path calls fail as if the file were
    missing even though it's right there. No-op on non-Windows."""
    s = os.path.abspath(str(path))
    if sys.platform == "win32" and not s.startswith("\\\\?\\"):
        return "\\\\?\\" + s
    return s


def _parse_date_str(raw: str):
    """
    Parse a raw date string in any of the common formats used by feed sources
    and return a UTC-normalized time.struct_time 9-tuple, or None.

    Timezone handling:
      - RFC 822 dates (named zones like EDT/EST/GMT, or numeric +hhmm/-hhmm)
        are resolved via email.utils.parsedate_tz and shifted to UTC. This
        mirrors what feedparser itself does internally for published_parsed /
        updated_parsed (verified empirically: EDT/GMT/+0900/-0500 test feeds
        for the same instant all converge on the same UTC value) -- so raw
        strings parsed here line up with feedparser's own parsed fields.
      - ISO 8601 strings with an explicit offset (%z, e.g. "+01:00") are
        likewise converted to UTC.
      - Formats with NO timezone information at all (bare "YYYY-MM-DD", the
        FierceBiotech/FiercePharma/Akero-SEC/MediciNova-SEC formats added to
        _DATE_FMTS, ISO 8601 literal "Z" suffix) are assumed to already be
        UTC -- the safest default when the source gives no indication.
    """
    if not raw or raw == "N/A":
        return None
    raw = raw.strip()

    import email.utils
    t = email.utils.parsedate_tz(raw)
    if t:
        offset = t[9] if t[9] is not None else 0
        try:
            utc_dt = datetime.datetime(*t[:6]) - datetime.timedelta(seconds=offset)
            return utc_dt.timetuple()
        except (ValueError, OverflowError):
            pass

    for fmt in _DATE_FMTS:
        try:
            dt = datetime.datetime.strptime(raw, fmt)
        except ValueError:
            continue
        if dt.tzinfo is not None:
            dt = dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        return dt.timetuple()
    return None


def format_uniform_date(raw: str) -> str:
    """
    Normalize any raw date string (RFC 822, ISO 8601, bare YYYY-MM-DD, the
    feed-specific formats added to _DATE_FMTS, ...) to a single IST display
    format: "DD-MM-YYYY HH:MM:SS IST". The source's stated timezone is
    converted to IST first (see _parse_date_str), so entries from different
    source timezones are directly comparable in this column. Returns "N/A"
    when the raw string is empty or unparseable by every known format.
    """
    parsed = _parse_date_str(raw)
    if not parsed:
        return "N/A"
    try:
        ist_dt = datetime.datetime(*parsed[:6]) + IST_OFFSET
        return f"{ist_dt.strftime('%d-%m-%Y %H:%M:%S')} {IST_LABEL}"
    except (ValueError, TypeError):
        return "N/A"


def entry_in_date_range(entry, start_dt, end_dt) -> bool:
    """
    Return True if the entry's most-recent activity date falls within [start_dt, end_dt].
    start_dt / end_dt (from get_date_filters) are IST calendar dates -- the CI
    team's working timezone -- so the entry's date is converted to IST before
    comparing, regardless of which timezone the source feed used.

    For ClinicalTrials feeds the filter runs on the RSS <pubDate> (when the trial
    update appeared in the feed), NOT the dc:date "First Posted" date — so the
    filter matches the Updated_Date column, which is what users expect.

    Date field priority:
      1. published_parsed  – RSS <pubDate>: most recent update date for all sources
                             including ClinicalTrials (where it = the feed update date)
      2. updated_parsed    – Atom <updated>: used for Atom feeds (SEC EDGAR, some IR)
      3. dc_date_parsed    – <dc:date>: last resort (ClinicalTrials "First Posted")
      4. Raw string parse  – fallback for non-standard or missing parsed fields
    Entries with no parseable date are conservatively included.

    Timezone note: published_parsed / updated_parsed / dc_date_parsed are all
    normalized to UTC by feedparser itself, regardless of the source's stated
    timezone (verified: EDT/GMT/+0900/-0500 test feeds for the same instant all
    converge on the same UTC value). _parse_date_str (step 4) performs the same
    UTC normalization manually for raw-string fallbacks. So every path here
    lands on a UTC value before the single +IST_OFFSET conversion below.
    """
    if not start_dt and not end_dt:
        return True

    # ── Step 1: feedparser parsed fields ─────────────────────────────────────
    # published_parsed → RSS <pubDate> (Google News, GlobeNewswire, Company IR…)
    # updated_parsed   → Atom <updated> (ClinicalTrials, SEC EDGAR, some IR feeds)
    # Both are time.struct_time set by feedparser; either is valid for filtering.
    pp = (getattr(entry, "published_parsed", None)
          or getattr(entry, "updated_parsed", None))

    # ── Step 2: dc_date parsed field ─────────────────────────────────────────
    # RSS 1.0 / RDF feeds use <dc:date> which feedparser maps to entry.dc_date_parsed
    # (not to published_parsed). Catch it here before falling to raw-string parsing.
    if not pp:
        pp = getattr(entry, "dc_date_parsed", None)

    # ── Step 3: raw string fallback ──────────────────────────────────────────
    # Last resort for any feed where feedparser couldn't produce a parsed tuple.
    # Tries every date-bearing attribute in priority order.
    if not pp:
        for attr in ("published", "updated", "dc_date"):
            raw = getattr(entry, attr, None)
            if raw and raw != "N/A":
                pp = _parse_date_str(raw)
                if pp:
                    break

    if not pp:
        return True   # no parseable date found — include conservatively
    try:
        pub_dt = datetime.datetime(*pp[:6]) + IST_OFFSET
    except Exception:
        return True
    if start_dt and pub_dt < start_dt:
        return False
    if end_dt   and pub_dt > end_dt:
        return False
    return True


# ── Feed fetcher (runs in a thread) ──────────────────────────────────────────

_DNS_FAIL_MARKERS = ("NameResolutionError", "getaddrinfo failed", "Name or service not known")


def _fail(target, url, status, detail="") -> dict:
    """Build a failure record dict."""
    return {"target": target, "url": url, "status": status, "detail": detail}


# Control characters that are illegal in XML 1.0 (excluding tab/newline/CR).
_BAD_XML_CHARS = re.compile(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _parse_feed_content(content: bytes):
    """
    Parse raw feed bytes with feedparser, with a sanitising fallback.

    Some otherwise-valid feeds carry stray control characters (illegal in XML)
    that make feedparser bail with "not well-formed (invalid token)" partway
    through the document (e.g. the Akero 'line 161:90' BOZO). If the first parse
    is bozo with no entries, retry once after stripping those control chars and
    a leading UTF-8 BOM.
    """
    parsed = feedparser.parse(content)
    if parsed.bozo and not parsed.entries and content:
        cleaned = _BAD_XML_CHARS.sub(b"", content.lstrip(b"\xef\xbb\xbf").lstrip())
        if cleaned != content:
            retry = feedparser.parse(cleaned)
            if retry.entries or not retry.bozo:
                return retry
    return parsed


# ── Headless-browser fallback (defeats bot-blockers / Cloudflare) ────────────
# Some endpoints reject plain `requests` (403, JS/consent challenge → BOZO,
# connection reset) but serve the feed fine to a real browser. When enabled,
# fetch through headless Chromium via Playwright. Optional dependency:
#     pip install playwright && playwright install chromium
# In the main pipeline this is gated behind RSS_BROWSER_FALLBACK=1 to keep the
# default run fast; the repair tool (fix_mash_feeds.py) calls it directly.
_BROWSER_FALLBACK = os.environ.get("RSS_BROWSER_FALLBACK", "").strip() in ("1", "true", "yes")

# ── curl_cffi fallback (Chrome TLS impersonation) ────────────────────────────
# Some servers (e.g. TGA) accept TCP but drop HTTP reads from Python's TLS
# stack while serving real Chrome fine. curl_cffi replaces the TLS handshake
# with Chrome's exact fingerprint — no browser process needed, as fast as
# requests. Automatically used when requests times out or gets a connection
# error, before falling back to the heavier headless Playwright browser.
try:
    from curl_cffi import requests as _curl_requests
    _CURL_CFFI_AVAILABLE = True
except ImportError:
    _CURL_CFFI_AVAILABLE = False

_CURL_CFFI_HEADERS = {
    "User-Agent":      BROWSER_HEADERS["User-Agent"],
    "Accept":          BROWSER_HEADERS["Accept"],
    "Accept-Language": BROWSER_HEADERS["Accept-Language"],
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control":   "no-cache",
}


def _curl_cffi_get(url: str, timeout: int = 20):
    """GET via curl_cffi with Chrome TLS impersonation. Returns response or None."""
    if not _CURL_CFFI_AVAILABLE:
        return None
    try:
        return _curl_requests.get(url, headers=_CURL_CFFI_HEADERS,
                                  timeout=timeout, impersonate="chrome")
    except Exception:
        return None


def _curl_cffi_rescue(stream: dict):
    """
    Try fetching a failed feed via Chrome TLS impersonation (curl_cffi).
    Lighter than Playwright — no browser process. Returns entry list (or [] for empty feed) or None.
    """
    if not _CURL_CFFI_AVAILABLE:
        return None
    resp = _curl_cffi_get(stream["URL"])
    if resp is None or resp.status_code != 200:
        return None
    entries = _entries_from_content(resp.content, stream)
    if entries is not None:
        print(f"    [CURL]    {stream['Target']} — {len(entries)} via Chrome TLS impersonation")
        return entries
    return None


def _looks_like_feed(text) -> bool:
    return bool(text) and ("<rss" in text or "<feed" in text or "<?xml" in text or "<rdf" in text.lower())


def _fetch_via_selenium(url: str, timeout_s: int = 30):
    """
    Fetch `url` through the locally-installed Chrome via Selenium and return the
    raw feed bytes, or None. Preferred over Playwright on corporate machines:
    system Chrome already carries the company proxy config and CA certificates,
    so MITM-proxied HTTPS works and anti-bot filters that block Python's HTTP
    client are bypassed. Selenium 4.6+ auto-resolves the driver (no manual setup).
    Requires: pip install selenium  (and Google Chrome installed).
    """
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
    except ImportError:
        return None
    driver = None
    try:
        opts = Options()
        opts.add_argument("--headless=new")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--log-level=3")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_argument("--disable-infobars")
        opts.add_argument("--window-size=1920,1080")
        opts.add_argument("--start-maximized")
        opts.add_argument(f"--user-agent={BROWSER_HEADERS['User-Agent']}")
        opts.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
        opts.add_experimental_option("useAutomationExtension", False)
        driver = webdriver.Chrome(options=opts)
        # Hide webdriver fingerprint that Akamai/Cloudflare detect
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": """
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3]});
                Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
                window.chrome = {runtime: {}};
            """
        })
        driver.set_page_load_timeout(timeout_s)
        try:
            driver.get(url)
        except Exception:
            pass  # navigation may stall on a challenge; still try the in-page fetch
        text = None
        try:
            driver.set_script_timeout(timeout_s)
            text = driver.execute_async_script(
                "var cb = arguments[arguments.length - 1];"
                "fetch(arguments[0], {credentials:'include'})"
                ".then(r => r.text()).then(t => cb(t)).catch(e => cb(''));",
                url)
        except Exception:
            text = None
        if not _looks_like_feed(text):
            try:
                body = driver.find_element("tag name", "body").text
            except Exception:
                body = ""
            if _looks_like_feed(body):
                text = body
            elif _looks_like_feed(driver.page_source):
                text = driver.page_source
        return text.encode("utf-8", "replace") if _looks_like_feed(text) else None
    except Exception:
        return None
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass


def _fetch_via_browser(url: str, timeout_ms: int = 30000):
    """
    Fetch `url` through a real browser and return the raw feed bytes, or None.
    Tries Selenium + system Chrome first (best behind a corporate proxy), then
    falls back to Playwright's bundled Chromium.
    """
    content = _fetch_via_selenium(url, timeout_s=timeout_ms // 1000)
    if content:
        return content
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(user_agent=BROWSER_HEADERS["User-Agent"])
            page = ctx.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            except Exception:
                pass  # challenge pages may stall navigation; still try fetch below
            text = None
            try:
                text = page.evaluate(
                    "async (u) => { const r = await fetch(u, {credentials:'include'}); "
                    "return await r.text(); }", url)
            except Exception:
                text = None
            if not text or ("<rss" not in text and "<feed" not in text and "<?xml" not in text):
                try:
                    body = page.evaluate("document.body ? document.body.innerText : ''")
                except Exception:
                    body = ""
                if body and ("<rss" in body or "<feed" in body or "<?xml" in body):
                    text = body
            browser.close()
            return text.encode("utf-8", "replace") if isinstance(text, str) and text else None
    except Exception:
        return None


def _pubmed_query_from_text(text: str) -> str:
    """
    Recover the PubMed search query from the OPML feed title/description.
    e.g. 'Pubmed (Efruxifermin OR AKR-001 OR ...)' -> '(Efruxifermin OR AKR-001 OR ...)'
    The leading 'Pubmed'/'PubMed' label is stripped; the boolean expression
    (parentheses, OR/AND) is passed straight to esearch, which understands it.
    """
    q = re.sub(r"^\s*pub\s*med\s*[:\-]?\s*", "", text, flags=re.I).strip()
    return q or text.strip()


def _entrez_get(endpoint: str, params: dict):
    """
    GET an Entrez E-utilities endpoint with auth params, global rate limiting,
    and retry.

    Rate: the global gate (_entrez_rate_gate) spaces request starts so the
    aggregate rate across all worker threads stays under NCBI's cap (10/s with
    key, 3/s without) — no request burst can collapse the API.

    Retry:
      - DNS/connection/timeout failures (eutils.ncbi.nlm.nih.gov resolves
        intermittently on this network) back off and retry rather than failing
        the whole feed.
      - HTTP 429 (too many requests) / 503 (busy) also back off and retry.
    """
    p = dict(params)
    p["tool"] = NCBI_TOOL
    if NCBI_EMAIL:
        p["email"] = NCBI_EMAIL
    if NCBI_API_KEY:
        p["api_key"] = NCBI_API_KEY

    MAX_RETRY = 3
    last_exc = None
    for attempt in range(MAX_RETRY + 1):
        try:
            _entrez_rate_gate()                 # hard rate cap, all threads
            with _PUBMED_LOCK:                  # concurrency cap (2 in flight)
                resp = _SESSION.get(f"{_EUTILS}/{endpoint}", params=p,
                                    headers=BROWSER_HEADERS, timeout=20)
            if resp.status_code in (400, 429, 503) and attempt < MAX_RETRY:
                time.sleep(2 ** (attempt + 1))  # 2 s, 4 s, 8 s
                continue
            return resp
        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as exc:
            last_exc = exc
            if attempt < MAX_RETRY:
                # DNS flakiness on eutils clears within seconds; back off and
                # retry (3 s, 6 s, 9 s) rather than failing the whole feed.
                time.sleep(3 * (attempt + 1))
                continue
            raise
    raise last_exc


def _pubmed_history_date(article_el):
    """
    Return (display_str, struct_time_9tuple) for a PubMed article, using the
    PubMed *history* dates (when the record entered PubMed) rather than the
    journal issue date. History dates are never in the future, unlike the
    Journal/PubDate or esummary sortpubdate (which carry scheduled issue dates
    and placeholder days like /01, /15, /31).

    Preference: 'pubmed' (live on PubMed) -> 'entrez' (received) -> 'medline'
    -> the Article's electronic ArticleDate -> Journal PubDate year.
    """
    def _from_pubdate_el(el):
        if el is None:
            return None
        y = el.findtext("Year")
        if not y or not y.isdigit():
            return None
        m = el.findtext("Month") or "1"
        d = el.findtext("Day") or "1"
        month = int(m) if m.isdigit() else _MONTHS.get(m[:3], 1)
        day   = int(d) if d.isdigit() else 1
        return (int(y), month, day)

    ymd = None
    for status in ("pubmed", "entrez", "medline"):
        el = article_el.find(f".//PubMedPubDate[@PubStatus='{status}']")
        ymd = _from_pubdate_el(el)
        if ymd:
            break
    if not ymd:
        ymd = _from_pubdate_el(article_el.find(".//Article/ArticleDate"))
    if not ymd:
        ymd = _from_pubdate_el(article_el.find(".//Article/Journal/JournalIssue/PubDate"))
    if not ymd:
        return "N/A", None

    y, m, d = ymd
    return f"{y:04d}-{m:02d}-{d:02d}", (y, m, d, 0, 0, 0, 0, 1, -1)


_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}


def _parse_pubmed_article(art):
    """Build a feedparser-style entry SimpleNamespace from an efetch <PubmedArticle>."""
    medline = art.find("MedlineCitation")
    pmid    = (medline.findtext("PMID") if medline is not None else None) or "N/A"
    article = medline.find("Article") if medline is not None else None

    title = "".join(article.find("ArticleTitle").itertext()).strip() \
        if article is not None and article.find("ArticleTitle") is not None else ""

    abstract = ""
    if article is not None:
        parts = ["".join(a.itertext()).strip() for a in article.findall("Abstract/AbstractText")]
        abstract = " ".join(p for p in parts if p)

    authors = []
    if article is not None:
        for au in article.findall("AuthorList/Author"):
            ln = au.findtext("LastName") or ""
            fn = au.findtext("ForeName") or au.findtext("Initials") or ""
            name = " ".join(x for x in (fn, ln) if x).strip()
            if name:
                authors.append({"name": name})
            elif au.findtext("CollectiveName"):
                authors.append({"name": au.findtext("CollectiveName")})

    journal = ""
    if article is not None:
        journal = (article.findtext("Journal/Title")
                   or article.findtext("Journal/ISOAbbreviation") or "")

    pub_str, pp = _pubmed_history_date(art)

    return SimpleNamespace(
        id        = pmid,
        title     = title,
        summary   = abstract or journal or "No abstract provided.",
        link      = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        published = pub_str,
        published_parsed = pp,
        updated   = pub_str,
        authors   = authors,
        source    = {"title": journal},
        tags      = [],
    )


def _fetch_pubmed_entrez(stream: dict) -> tuple:
    """
    Fetch a PubMed feed via Entrez E-utilities (esearch + efetch) instead of the
    blocked RSS endpoint. Returns (entries, failure) like _fetch_one.

    Uses efetch (full XML) rather than esummary so we can read the PubMed
    *history* date (never in the future) and the real abstract. Builds
    feedparser-style entry objects so run_pipeline's accessors work unchanged.
    """
    target = stream["Target"]
    url    = stream["URL"]
    query  = _pubmed_query_from_text(stream.get("Feed_Description", "") or target)

    try:
        r = _entrez_get("esearch.fcgi", {
            "db": "pubmed", "term": query, "retmax": 15,
            "sort": "pub_date", "retmode": "json",
        })
        if r.status_code != 200:
            return [], _fail(target, url, f"HTTP {r.status_code}", "esearch")
        idlist = r.json().get("esearchresult", {}).get("idlist", [])
        if not idlist:
            return [], None    # valid query, no current results (EMPTY)

        f = _entrez_get("efetch.fcgi", {
            "db": "pubmed", "id": ",".join(idlist),
            "retmode": "xml", "rettype": "abstract",
        })
        if f.status_code != 200:
            return [], _fail(target, url, f"HTTP {f.status_code}", "efetch")

        root = ET.fromstring(f.content)
        entries = []
        for art in root.findall(".//PubmedArticle"):
            entry = _parse_pubmed_article(art)
            entries.append({
                "_entry":           entry,
                "Target":           target,
                "Type":             stream["Type"],
                "Feed_Description": stream["Feed_Description"],
                "Group":            stream.get("Group", ""),
            })
        print(f"    [ENTREZ]  {target} — {len(entries)} via E-utilities (efetch)")
        return entries, None

    except Exception as exc:
        return [], _fail(target, url, "ERROR", f"entrez: {str(exc)[:100]}")


def _entries_from_content(content: bytes, stream: dict):
    """Parse raw feed bytes into the run_pipeline entry-dict shape, or None if unusable."""
    parsed = _parse_feed_content(content)
    if parsed.bozo and not parsed.entries:
        return None
    return [
        {
            "_entry":           entry,
            "Target":           stream["Target"],
            "Type":             stream["Type"],
            "Feed_Description": stream["Feed_Description"],
            "Group":            stream.get("Group", ""),
        }
        for entry in parsed.entries
    ]


def _browser_rescue(stream: dict):
    """
    Last-resort fetch of a bot-blocked feed via headless Chromium.
    Returns an entry list (or [] for empty feed) on success, or None. Safe to call even when
    Playwright isn't installed (_fetch_via_browser returns None).
    """
    content = _fetch_via_browser(stream["URL"])
    if not content:
        return None
    entries = _entries_from_content(content, stream)
    if entries is not None:
        print(f"    [BROWSER] {stream['Target']} — {len(entries)} via headless Chromium")
        return entries
    return None


def _fetch_one(stream: dict) -> tuple:
    """
    Fetch a single RSS/Atom feed using requests (not feedparser's built-in
    urllib fetcher, which gets silently blocked by sites like ClinicalTrials.gov).
    Raw bytes are passed to feedparser for XML parsing only.

    Returns: (entries: list, failure: dict | None)
      - On success  → (non-empty list, None)
      - On failure  → ([],            failure_dict)

    Retry logic:
      - DNS failures     → permanent, no retry  → [DEAD URL]
      - Timeout          → transient, retry 2x  → [TIMEOUT]
      - Connection error → transient, retry 2x  → [CONN ERR]
      - HTTP non-200     → no retry             → [HTTP xxx]
    """
    url       = stream["URL"]
    target    = stream["Target"]
    MAX_RETRY = 2

    # PubMed: prefer the Entrez E-utilities API (durable fix for HTTP 403).
    # Falls back to the throttled RSS endpoint only when no API key is set.
    is_pubmed = "pubmed.ncbi.nlm.nih.gov" in url
    if is_pubmed and NCBI_API_KEY:
        return _fetch_pubmed_entrez(stream)

    headers   = dict(BROWSER_HEADERS)
    if is_pubmed:
        headers["Referer"] = "https://pubmed.ncbi.nlm.nih.gov/"

    for attempt in range(MAX_RETRY + 1):
        try:
            if is_pubmed:
                with _PUBMED_LOCK:
                    resp = _SESSION.get(url, headers=headers, timeout=15)
                    time.sleep(0.5)   # small gap between PubMed requests
            else:
                resp = _SESSION.get(url, headers=headers, timeout=15)

            if resp.status_code != 200:
                status = f"HTTP {resp.status_code}"
                # Transient: 5xx server errors, HTTP 429 (rate limit), and HTTP 403
                # (WAF challenges seen from PMLive/PharmaTimes under concurrency)
                # are worth retrying with backoff before giving up.
                transient = (resp.status_code >= 500 or resp.status_code == 429
                             or resp.status_code == 403)
                if transient and attempt < MAX_RETRY:
                    headers = {"User-Agent": "Mozilla/5.0"}
                    time.sleep(2 ** (attempt + 1))
                    continue
                rescued = _curl_cffi_rescue(stream)
                if rescued is not None:
                    return rescued, None
                if _BROWSER_FALLBACK:
                    rescued = _browser_rescue(stream)
                    if rescued is not None:
                        return rescued, None
                print(f"    [{status}] {target}")
                return [], _fail(target, url, status)

            parsed = _parse_feed_content(resp.content)

            if parsed.bozo and not parsed.entries:
                exc_msg = str(getattr(parsed, "bozo_exception", "unknown"))
                detail  = exc_msg[:120]
                rescued = _curl_cffi_rescue(stream)
                if rescued is not None:
                    return rescued, None
                if _BROWSER_FALLBACK:
                    rescued = _browser_rescue(stream)
                    if rescued is not None:
                        return rescued, None
                print(f"    [BOZO]    {target} — {detail}")
                return [], _fail(target, url, "BOZO", detail)

            entries = [
                {
                    "_entry":           entry,
                    "Target":           target,
                    "Type":             stream["Type"],
                    "Feed_Description": stream["Feed_Description"],
                    "Group":            stream.get("Group", ""),
                }
                for entry in parsed.entries
            ]
            return entries, None       # success

        except requests.exceptions.Timeout:
            if attempt < MAX_RETRY:
                headers = {"User-Agent": "Mozilla/5.0"}
                time.sleep(2 ** (attempt + 1))
                continue
            rescued = _curl_cffi_rescue(stream)
            if rescued is not None:
                return rescued, None
            if _BROWSER_FALLBACK:
                rescued = _browser_rescue(stream)
                if rescued is not None:
                    return rescued, None
            msg = f"gave up after {MAX_RETRY + 1} attempts"
            print(f"    [TIMEOUT]  {target} — {msg}")
            return [], _fail(target, url, "TIMEOUT", msg)

        except requests.exceptions.ConnectionError as exc:
            exc_str = str(exc)
            if any(m in exc_str for m in _DNS_FAIL_MARKERS):
                if attempt < MAX_RETRY:
                    time.sleep(3 * (attempt + 1))   # 3 s, 6 s
                    continue
                rescued = _curl_cffi_rescue(stream)
                if rescued is not None:
                    return rescued, None
                if _BROWSER_FALLBACK:
                    rescued = _browser_rescue(stream)
                    if rescued is not None:
                        return rescued, None
                print(f"    [DEAD URL] {target} — host not found (DNS failure)")
                return [], _fail(target, url, "DEAD URL", "host not found")
            if attempt < MAX_RETRY:
                time.sleep(2 ** (attempt + 1))
                continue
            rescued = _curl_cffi_rescue(stream)
            if rescued is not None:
                return rescued, None
            if _BROWSER_FALLBACK:
                rescued = _browser_rescue(stream)
                if rescued is not None:
                    return rescued, None
            msg = f"connection failed after {MAX_RETRY + 1} attempts"
            print(f"    [CONN ERR] {target} — {msg}")
            return [], _fail(target, url, "CONN ERR", msg)

        except Exception as exc:
            detail = str(exc)[:120]
            print(f"    [ERROR]    {target}: {detail}")
            return [], _fail(target, url, "ERROR", detail)

    return [], _fail(target, url, "ERROR", "unknown — loop exhausted")


# ── Excel helpers ─────────────────────────────────────────────────────────────

def _autofit(worksheet):
    for col in worksheet.columns:
        max_len = max(len(str(cell.value or "")) for cell in col)
        worksheet.column_dimensions[col[0].column_letter].width = min(
            max(max_len + 3, 11), 80
        )


COMBINED_COLUMNS = COLUMNS + ["Alert_OPML File"]


def build_combined_report(pipeline_outputs, output_dir):
    """
    pipeline_outputs: list of {"opml": <opml_path>, "path": <xlsx_path or None>}
    Reads Priority/Included/Excluded/Duplicates from each pipeline's report,
    tags every row with its source OPML filename, and writes one workbook
    with the same 4 sheets (each sheet = union across pipelines).
    Returns the absolute path written, or None if there was nothing to combine.
    """
    sheet_names = ["Priority", "Included", "Excluded", "Duplicates"]
    combined = {name: [] for name in sheet_names}

    for po in pipeline_outputs:
        path = po.get("path")
        if not path or not os.path.exists(path):
            continue
        opml_name = os.path.basename(po["opml"])
        for name in sheet_names:
            try:
                df = pd.read_excel(path, sheet_name=name)
            except ValueError:
                continue
            if df.empty:
                continue
            if "Indication" not in df.columns:
                df["Indication"] = "N/A"
            df["Alert_OPML File"] = opml_name
            df = df.reindex(columns=COMBINED_COLUMNS)
            combined[name].append(df)

    if not any(combined.values()):
        return None

    os.makedirs(output_dir, exist_ok=True)
    date_tag = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out_path = os.path.join(output_dir, f"Combined_Report_{date_tag}.xlsx")

    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        for name in sheet_names:
            frames = combined[name]
            df_out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=COMBINED_COLUMNS)
            df_out.to_excel(writer, index=False, sheet_name=name)
            _autofit(writer.sheets[name])

    return os.path.abspath(out_path)


# ── Main pipeline ─────────────────────────────────────────────────────────────

def run_pipeline(opml_path: str, output_prefix: str, max_workers: int = 12,
                 start_dt=None, end_dt=None, columns=None):
    """
    Full ingestion pipeline:
      1. Parse OPML → list of feeds
      2. Ask for date filter (skipped if start_dt/end_dt already supplied)
      3. Fetch all feeds concurrently
      4. Deduplicate, apply date filter
      5. Write dated Excel file with Included / Excluded sheets

    columns: optional override of which COLUMNS to write to the Excel sheets
             (e.g. drop "Indication" for a pipeline where it isn't meaningful).
             Defaults to the full COLUMNS list.
    """
    cols = columns or COLUMNS
    print(f"\n{'='*85}")
    print(f"  OPML : {opml_path}")
    print(f"{'='*85}")

    feeds = parse_opml(opml_path)
    print(f"  Feeds found in OPML : {len(feeds)}")

    if start_dt is None and end_dt is None:
        start_dt, end_dt = get_date_filters()

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    date_tag  = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")

    print(f"Fetching {len(feeds)} feeds (up to {max_workers} concurrent)...\n")
    print("-" * 85)

    all_raw:  list = []
    failures: list = []

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_fetch_one, f): f for f in feeds}
        done = 0
        for future in as_completed(futures):
            done += 1
            stream           = futures[future]
            results, failure = future.result()
            # Stamp each result with the group so Indication can be derived later
            for r in results:
                r["Group"] = stream.get("Group", "")
            all_raw.extend(results)
            if failure:
                failures.append(failure)
            status = f"{len(results):3d} entries"
            print(
                f"  [{done:03d}/{len(feeds)}] {stream['Type']:<38} "
                f"-> {stream['Target'][:45]:<45}  {status}"
            )

    print("-" * 85)
    print(f"  Total raw entries : {len(all_raw)}")

    # ── Build records, dedup, date-filter, keyword-filter ────────────────────
    seen          = set()
    priority      = []
    included      = []
    excluded      = []
    duplicates    = []

    for raw in all_raw:
        entry    = raw["_entry"]
        link     = getattr(entry, "link", "N/A")
        entry_id = _get_entry_id(entry)

        # ClinicalTrials feeds are keyed on NCT ID rather than (Target, URL):
        # the same trial often matches more than one drug/company feed, and
        # those cross-feed repeats should land in Duplicates too, not just
        # exact Target+URL repeats within a single feed.
        is_ct = "ClinicalTrials" in raw["Type"]
        if is_ct and entry_id != "N/A":
            key = ("NCT_ID", entry_id)
        else:
            key = (raw["Target"], link)

        # ClinicalTrials feeds carry two dates with swapped semantics:
        #   published  (RSS <pubDate>)  = date the trial update appeared in the feed (recent)
        #   dc_date    (<dc:date>)      = "First Posted" date on ClinicalTrials.gov (historic)
        # Swap them so Published_Date always holds the older "first seen" date and
        # Updated_Date holds the more-recent "last changed" date, keeping
        # Published_Date ≤ Updated_Date logically consistent.
        if is_ct:
            pub_date     = _get_updated(entry)                    # dc:date = First Posted
            updated_date = getattr(entry, "published", "N/A")    # pubDate = feed update date
        else:
            pub_date     = getattr(entry, "published", "N/A")
            updated_date = _get_updated(entry)

        title_text   = clean_text(getattr(entry, "title",   ""))
        summary_text = clean_text(getattr(entry, "summary", "No baseline summary provided."))
        is_match, matched_cats, matched_terms = keyword_match_info(title_text, summary_text)
        vetoed, vetoed_terms = obesity_veto(title_text, summary_text, matched_cats)
        _, allowed_terms = obesity_allowed_match(title_text, summary_text)

        # Obesity news let back in via the Novo/Lilly/Roche exception has no
        # MASH/ALD/AUD term of its own (is_match would otherwise be False and
        # it would never reach the Priority sheet below), so surface it as its
        # own matched category -- visibly distinct from a normal MASH hit.
        obesity_allowed = bool(vetoed_terms) and not vetoed and bool(allowed_terms)
        if obesity_allowed:
            is_match = True
            matched_cats = matched_cats + ["Obesity (Novo/Lilly/Roche)"]
            matched_terms = matched_terms + allowed_terms

        record = {
            "Extraction_Timestamp": timestamp,
            "Asset_Target":         raw["Target"],
            "Indication":           extract_indication(
                                        raw["Feed_Description"],
                                        raw.get("Group", "")
                                    ),
            "Stream_Source":        raw["Type"],
            "Feed_Description":     raw["Feed_Description"][:150],
            "Entry_ID":             entry_id,
            "Update_Title":         title_text,
            "Summary_Payload":      summary_text,
            "Keyword_Match":        "Yes" if is_match else "No",
            "Matched_Categories":   "; ".join(matched_cats)  or "N/A",
            "Matched_Keywords":     "; ".join(matched_terms) or "N/A",
            "Excluded_Reason":      (("obesity/T2D out of scope: " + "; ".join(vetoed_terms)) if vetoed
                                     else ("Obesity allowed: Novo/Lilly/Roche match (" + "; ".join(allowed_terms) + ")")
                                     if obesity_allowed else "N/A"),
            "Author":               _get_author(entry),
            "Publisher":            _get_publisher(entry),
            "Tags":                 _get_tags(entry),
            "Published_Date":         pub_date,
            "Published_Date_Uniform": format_uniform_date(pub_date),
            "Updated_Date":           updated_date,
            "Updated_Date_Uniform":   format_uniform_date(updated_date),
            "Source_URL":             link,
        }

        if key in seen:
            duplicates.append(record)
            continue
        seen.add(key)

        if entry_in_date_range(entry, start_dt, end_dt):
            included.append(record)
            # The Priority sheet is the ONLY sheet the downstream pipeline reads
            # (dedupe_source_urls.py --sheet Priority), so the obesity/T2D veto
            # applied here keeps those items out of dedup, triage and the mail at
            # zero API cost. They stay in Included, tagged via Excluded_Reason.
            if is_match and not vetoed:
                priority.append(record)   # subset of Included — keyword-matched highlight
        else:
            excluded.append(record)

    if duplicates:
        print(f"  Duplicates removed : {len(duplicates)} entries "
              f"(same Target + article URL, or for ClinicalTrials the same NCT ID, repeated)")
    if excluded:
        print(f"  Date-filtered out  : {len(excluded)} entries")
    print(f"  Entries in report  : {len(included) + len(excluded)}  "
          f"(Included {len(included)}, of which Priority (keyword-matched) {len(priority)}"
          f"  + Excluded {len(excluded)}  == Raw {len(all_raw)} - Duplicates {len(duplicates)})")

    # ── Write Excel ───────────────────────────────────────────────────────────
    if priority or included or excluded or duplicates:
        out_path = f"{output_prefix}_{date_tag}.xlsx"
        with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
            pd.DataFrame(priority or [], columns=cols).to_excel(
                writer, index=False, sheet_name="Priority"
            )
            _autofit(writer.sheets["Priority"])

            pd.DataFrame(included or [], columns=cols).to_excel(
                writer, index=False, sheet_name="Included"
            )
            _autofit(writer.sheets["Included"])

            pd.DataFrame(excluded or [], columns=cols).to_excel(
                writer, index=False, sheet_name="Excluded"
            )
            _autofit(writer.sheets["Excluded"])

            pd.DataFrame(duplicates or [], columns=cols).to_excel(
                writer, index=False, sheet_name="Duplicates"
            )
            _autofit(writer.sheets["Duplicates"])

        abs_path = os.path.abspath(out_path)
        print(f"\n[SUCCESS] Written -> '{abs_path}'")
        print(f"  Sheet 'Priority'   : {len(priority)} entries")
        print(f"  Sheet 'Included'   : {len(included)} entries")
        print(f"  Sheet 'Excluded'   : {len(excluded)} entries")
        print(f"  Sheet 'Duplicates' : {len(duplicates)} entries")
    else:
        print("\n[WARNING] No entries returned — spreadsheet not created.")
        abs_path = None

    # ── Failure summary ───────────────────────────────────────────────────────
    if failures:
        print(f"\n  {'─'*83}")
        print(f"  FEED FAILURES  ({len(failures)} of {len(feeds)} feeds failed)")
        print(f"  {'─'*83}")
        print(f"  {'#':<4}  {'STATUS':<12}  {'TARGET':<35}  {'DETAIL'}")
        print(f"  {'─'*83}")
        for i, f in enumerate(failures, 1):
            print(
                f"  {i:<4}  {f['status']:<12}  {f['target'][:35]:<35}  {f['detail'][:50]}"
            )
            print(f"        URL: {f['url'][:75]}")
        print(f"  {'─'*83}")
    else:
        print(f"\n  All {len(feeds)} feeds fetched successfully — no failures.")

    return abs_path          # return actual path so callers never have to guess


# ── Guard: this file is a shared library, not a runnable script ──────────────
if __name__ == "__main__":
    print("=" * 65)
    print("  rss_opml_common.py is a shared library.")
    print("  Do NOT run it directly.")
    print()
    print("  Run one of the three main scripts instead:")
    print("    python Script_v1_Clinical_Trials_RSS.py")
    print("    python Script_v1_ALD_AUD_RSS.py")
    print("    python Script_v1_MASH_RSS.py")
    print("=" * 65)
    input("\nPress Enter to close...")
