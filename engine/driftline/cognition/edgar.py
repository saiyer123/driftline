"""SEC EDGAR client: find and fetch recent earnings 8-Ks for the watchlist.

Free, official data. EDGAR's fair-access policy requires a User-Agent with
contact info (settings.edgar_contact) and modest request rates — we sleep
between requests and only run on a daily cadence.

Parsing is kept in pure functions so it's testable without the network.
"""

from __future__ import annotations

import html as html_lib
import json
import logging
import re
import time
import urllib.request
from dataclasses import dataclass
from datetime import date, timedelta

from ..config import settings

log = logging.getLogger(__name__)

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{doc}"
REQUEST_GAP_S = 0.4  # stay well under EDGAR's 10 req/s ceiling


def _get(url: str) -> bytes:
    if not settings.edgar_contact:
        raise RuntimeError("EDGAR_CONTACT is not set in .env — EDGAR ingestion disabled")
    req = urllib.request.Request(
        url, headers={"User-Agent": f"Driftline personal research {settings.edgar_contact}"}
    )
    time.sleep(REQUEST_GAP_S)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


@dataclass
class EarningsFiling:
    symbol: str
    cik: int
    accession: str          # no dashes
    filed: str              # YYYY-MM-DD
    form: str
    primary_doc: str


def load_cik_map(raw: bytes) -> dict[str, int]:
    """company_tickers.json -> {ticker: cik}."""
    data = json.loads(raw)
    return {row["ticker"].upper(): int(row["cik_str"]) for row in data.values()}


def parse_earnings_8ks(symbol: str, cik: int, submissions_raw: bytes,
                       since: date) -> list[EarningsFiling]:
    """Pick 8-K filings with Item 2.02 (results of operations) since `since`."""
    data = json.loads(submissions_raw)
    recent = data.get("filings", {}).get("recent", {})
    out = []
    for form, filed, items, accession, doc in zip(
        recent.get("form", []), recent.get("filingDate", []),
        recent.get("items", []), recent.get("accessionNumber", []),
        recent.get("primaryDocument", []),
    ):
        if form != "8-K" or "2.02" not in (items or ""):
            continue
        if date.fromisoformat(filed) < since:
            continue
        out.append(EarningsFiling(
            symbol=symbol, cik=cik, accession=accession.replace("-", ""),
            filed=filed, form=form, primary_doc=doc,
        ))
    return out


def strip_html(raw: str, max_chars: int = 18_000) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()[:max_chars]


def fetch_watchlist_ciks(watchlist: list[str]) -> dict[str, int]:
    cik_map = load_cik_map(_get(TICKERS_URL))
    return {s: cik_map[s] for s in watchlist if s in cik_map}


def fetch_recent_earnings_filings(watchlist: list[str], days_back: int = 4) -> list[EarningsFiling]:
    since = date.today() - timedelta(days=days_back)
    ciks = fetch_watchlist_ciks(watchlist)
    filings: list[EarningsFiling] = []
    for symbol, cik in ciks.items():
        try:
            raw = _get(SUBMISSIONS_URL.format(cik=cik))
            filings += parse_earnings_8ks(symbol, cik, raw, since)
        except Exception:
            log.exception("EDGAR submissions fetch failed for %s; skipping", symbol)
    return filings


def fetch_filing_text(filing: EarningsFiling) -> str:
    """Primary doc plus the press-release exhibit (EX-99*) when present."""
    index_raw = _get(ARCHIVE_URL.format(cik=filing.cik, accession=filing.accession, doc="index.json"))
    items = json.loads(index_raw).get("directory", {}).get("item", [])
    docs = [filing.primary_doc]
    for item in items:
        name = item.get("name", "")
        if re.search(r"ex[-_]?99", name, re.I) and name.endswith((".htm", ".html")):
            docs.append(name)
    parts = []
    for doc in dict.fromkeys(docs):  # dedupe, keep order
        try:
            raw = _get(ARCHIVE_URL.format(cik=filing.cik, accession=filing.accession, doc=doc))
            parts.append(strip_html(raw.decode("utf-8", errors="replace")))
        except Exception:
            log.exception("EDGAR doc fetch failed: %s", doc)
    return "\n\n---\n\n".join(parts)[:24_000]
