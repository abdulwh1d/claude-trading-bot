"""Overnight input: recent SEC filings for the watchlist, straight from EDGAR.

8-Ks carry material events, and earnings releases arrive as an 8-K exhibit
(99.1), so those exhibits are included. For 10-Qs and 10-Ks the text starts at
Management's Discussion and Analysis instead of the cover pages.
Everything here is third-party text and gets wrapped as untrusted before any
model sees it.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html.parser import HTMLParser

import config

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
FOLDER_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/"
_EXHIBIT_99 = re.compile(r"ex[-_]?99", re.IGNORECASE)
_MDA = re.compile(r"management[’'`s]{0,2}\s+discussion\s+and\s+analysis", re.IGNORECASE)


@dataclass
class Filing:
    symbol: str
    cik: int
    form: str
    filed: str           # YYYY-MM-DD
    accession: str
    primary_doc: str

    @property
    def folder(self) -> str:
        return FOLDER_URL.format(cik=self.cik, acc=self.accession.replace("-", ""))


class _Text(HTMLParser):
    _BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "table", "section"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style") or (tag.startswith("ix:header")):
            self._skip += 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") or tag.startswith("ix:header"):
            self._skip = max(0, self._skip - 1)

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _Text()
    parser.feed(html)
    text = "".join(parser.parts).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


class Edgar:
    def __init__(self, user_agent: str = config.SEC_USER_AGENT, session=None):
        if not user_agent:
            raise ValueError("SEC_USER_AGENT is not set (the SEC requires your name and email).")
        if session is None:
            import requests
            session = requests.Session()
        self.session = session
        self.session.headers.update({"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"})
        self._last_request = 0.0
        self._ciks: dict[str, int] | None = None

    def _get(self, url: str):
        wait = 0.15 - (time.monotonic() - self._last_request)   # the SEC allows 10 requests a second
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        return response

    def cik(self, symbol: str) -> int | None:
        if self._ciks is None:
            cache = config.DATA_DIR / "sec_tickers.json"
            fresh = cache.exists() and datetime.now().timestamp() - cache.stat().st_mtime < 7 * 86400
            if fresh:
                raw = json.loads(cache.read_text(encoding="utf-8"))
            else:
                raw = self._get(TICKERS_URL).json()
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps(raw), encoding="utf-8")
            self._ciks = {row["ticker"].upper(): int(row["cik_str"]) for row in raw.values()}
        return self._ciks.get(symbol.upper().replace(".", "-"))

    def recent_filings(self, symbol: str, since: date) -> list[Filing]:
        cik = self.cik(symbol)
        if cik is None:
            return []   # ETFs and funds usually aren't in the company list
        recent = self._get(SUBMISSIONS_URL.format(cik=cik)).json()["filings"]["recent"]
        out = []
        for form, filed, acc, doc in zip(recent["form"], recent["filingDate"],
                                         recent["accessionNumber"], recent["primaryDocument"]):
            if form in config.FILING_FORMS and filed >= since.isoformat():
                out.append(Filing(symbol, cik, form, filed, acc, doc))
        return out

    def filing_text(self, filing: Filing, max_chars: int = config.FILING_MAX_CHARS) -> str:
        text = html_to_text(self._get(filing.folder + filing.primary_doc).text)
        if filing.form in ("10-Q", "10-K"):
            hits = [m.start() for m in _MDA.finditer(text)]
            if hits:   # the first hit is usually the table of contents
                text = text[hits[1] if len(hits) > 1 else hits[0]:]
        if filing.form == "8-K":
            try:
                items = self._get(filing.folder + "index.json").json()["directory"]["item"]
                for item in items:
                    name = item["name"]
                    if _EXHIBIT_99.search(name) and name.lower().endswith((".htm", ".html", ".txt")):
                        text += f"\n\n--- Exhibit {name} ---\n" + html_to_text(self._get(filing.folder + name).text)
            except Exception:
                pass   # the exhibit is a bonus; the 8-K itself is already in hand
        return text[:max_chars]


def documents_for(edgar: Edgar | None, symbol: str, today: date) -> list[tuple[str, str]]:
    """(document id, text) pairs for the overnight brief."""
    if edgar is None:
        return []
    docs = []
    for f in edgar.recent_filings(symbol, since=today - timedelta(days=config.FILING_LOOKBACK_DAYS)):
        try:
            docs.append((f"sec-{f.accession}", f"SEC {f.form} filed {f.filed}\n\n{edgar.filing_text(f)}"))
        except Exception as exc:
            docs.append((f"sec-{f.accession}", f"SEC {f.form} filed {f.filed} (text unavailable: {exc})"))
    return docs
