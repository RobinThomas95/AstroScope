"""NASA ADS search and bibliometrics (Module 5)."""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import Counter
from typing import Any

import httpx

from app.schemas.literature import (
    Bibliometrics,
    CitationBin,
    LiteratureResult,
    NamedCount,
    Paper,
    YearCount,
)
from app.services.facilities import detect_facilities

log = logging.getLogger(__name__)

ADS_API = "https://api.adsabs.harvard.edu/v1/search/query"
FIELDS = (
    "bibcode,title,author,year,pubdate,pub,doctype,property,citation_count,keyword,abstract,"
    "facility"
)
FIELDS_MINIMAL = FIELDS.replace(",facility", "")
PAGE = 200
MAX_ROWS = 2000
CITATION_BINS = [(0, 0), (1, 4), (5, 19), (20, 49), (50, 99), (100, 499), (500, None)]


def _escape(value: str) -> str:
    return value.replace('"', '\\"')


class AdsQueryError(ValueError):
    """ADS rejected the query (HTTP 400); the message is ADS's own."""


def build_query(names: list[str], refereed_only: bool = False, use_object: bool = True) -> str:
    """ADS query matching the object field or the abstract/title for any of the names."""
    clean = []
    for n in names:
        n = " ".join(n.split())
        if n and n not in clean:
            clean.append(n)
    if not clean:
        raise ValueError("No object names to search for")
    terms = [f'object:"{_escape(n)}"' for n in clean] if use_object else []
    # Catalogue names often appear without the space ("NGC1365") in titles/abstracts.
    for n in clean:
        compact = n.replace(" ", "")
        terms.append(f'abs:"{_escape(n)}"')
        if compact != n and re.match(r"^[A-Za-z]+\s*\d", n):
            terms.append(f'abs:"{_escape(compact)}"')
    q = "(" + " OR ".join(terms) + ") collection:astronomy"
    if refereed_only:
        q += " property:refereed"
    return q


class AdsClient:
    def __init__(self, token: str, timeout: float = 60.0):
        if not token:
            raise ValueError("An ADS API token is required")
        self.token = token
        self.timeout = timeout

    def _get(self, params: dict[str, Any]) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout) as client:
            res = client.get(
                ADS_API,
                params=params,
                headers={"Authorization": f"Bearer {self.token}", "User-Agent": "AstroScope/0.1"},
            )
        if res.status_code == 401:
            raise PermissionError("ADS rejected the token (401 Unauthorized)")
        if res.status_code == 429:
            raise RuntimeError("ADS rate limit reached (429); try again later")
        if res.status_code == 400:
            try:
                msg = res.json().get("error") or res.text[:300]
            except Exception:  # noqa: BLE001
                msg = res.text[:300]
            raise AdsQueryError(f"ADS rejected the query: {msg}")
        res.raise_for_status()
        return res.json()

    def search(
        self, query: str, max_rows: int = MAX_ROWS, fields: str = FIELDS
    ) -> tuple[list[dict[str, Any]], int]:
        """All ADS documents for a query (paged), and the total number found."""
        docs: list[dict[str, Any]] = []
        start = 0
        num_found = 0
        while start < max_rows:
            rows = min(PAGE, max_rows - start)
            data = self._get(
                {
                    "q": query,
                    "fl": fields,
                    "rows": rows,
                    "start": start,
                    "sort": "citation_count desc",
                }
            )
            resp = data.get("response", {})
            num_found = int(resp.get("numFound", 0))
            batch = resp.get("docs", [])
            docs.extend(batch)
            start += len(batch)
            if not batch or start >= num_found:
                break
        return docs, num_found


def to_paper(doc: dict[str, Any]) -> Paper:
    title = (doc.get("title") or [None])[0]
    authors = list(doc.get("author") or [])
    props = set(doc.get("property") or [])
    keywords = [k for k in (doc.get("keyword") or []) if k]
    facilities = list(doc.get("facility") or [])
    for f in detect_facilities(title, doc.get("abstract"), " ".join(keywords)):
        if f not in facilities:
            facilities.append(f)
    year = doc.get("year")
    try:
        year_i = int(year) if year is not None else None
    except (TypeError, ValueError):
        year_i = None
    bibcode = str(doc.get("bibcode"))
    return Paper(
        bibcode=bibcode,
        title=title,
        first_author=authors[0] if authors else None,
        authors=authors[:10],
        year=year_i,
        pubdate=doc.get("pubdate"),
        journal=doc.get("pub"),
        doctype=doc.get("doctype"),
        refereed="REFEREED" in props,
        citation_count=int(doc.get("citation_count") or 0),
        keywords=keywords[:15],
        facilities=facilities,
        abstract=doc.get("abstract"),
        ads_url=f"https://ui.adsabs.harvard.edu/abs/{bibcode}/abstract",
    )


def h_index(citations: list[int]) -> int:
    h = 0
    for i, c in enumerate(sorted(citations, reverse=True), start=1):
        if c >= i:
            h = i
        else:
            break
    return h


def bibliometrics(papers: list[Paper]) -> Bibliometrics:
    by_year: dict[int, list[int]] = {}
    for p in papers:
        if p.year is not None:
            by_year.setdefault(p.year, [0, 0])
            by_year[p.year][0] += 1
            by_year[p.year][1] += int(p.refereed)
    years = [YearCount(year=y, count=c[0], refereed=c[1]) for y, c in sorted(by_year.items())]
    fac = Counter(f for p in papers for f in p.facilities)
    kw = Counter(k.strip().lower() for p in papers for k in p.keywords)
    cites = [p.citation_count for p in papers]
    bins = []
    for lo, hi in CITATION_BINS:
        n = sum(1 for c in cites if c >= lo and (hi is None or c <= hi))
        label = f"{lo}" if lo == hi else (f"{lo}–{hi}" if hi is not None else f"{lo}+")
        bins.append(CitationBin(label=label, min=lo, max=hi, count=n))
    top = sorted(papers, key=lambda p: -p.citation_count)[:25]
    recent = sorted(papers, key=lambda p: (p.pubdate or "", p.year or 0), reverse=True)[:25]
    return Bibliometrics(
        total=len(papers),
        refereed=sum(1 for p in papers if p.refereed),
        years=years,
        facilities=[NamedCount(name=n, count=c) for n, c in fac.most_common(40)],
        keywords=[NamedCount(name=n, count=c) for n, c in kw.most_common(40)],
        citation_bins=bins,
        total_citations=sum(cites),
        h_index=h_index(cites),
        top_cited=[p.bibcode for p in top],
        most_recent=[p.bibcode for p in recent],
    )


class LiteratureService:
    def __init__(self, cache_ttl: int = 3600, client_factory=AdsClient):
        self.cache_ttl = cache_ttl
        self.client_factory = client_factory
        self._cache: dict[tuple, tuple[float, LiteratureResult]] = {}
        self._lock = threading.Lock()

    def search(
        self,
        query: str,
        names: list[str],
        token: str,
        token_source: str,
        refereed_only: bool = False,
        max_rows: int = MAX_ROWS,
    ) -> LiteratureResult:
        key = (tuple(sorted(n.lower() for n in names)), refereed_only, max_rows, token[-6:])
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(key)
            if hit and hit[0] > now:
                return hit[1].model_copy(update={"cached": True})
        client = self.client_factory(token)
        # ADS answers 400 when its object: resolution fails or a field is unknown; fall back
        # progressively rather than failing the whole search.
        attempts = [
            (build_query(names, refereed_only, use_object=True), FIELDS),
            (build_query(names, refereed_only, use_object=False), FIELDS),
            (build_query(names, refereed_only, use_object=False), FIELDS_MINIMAL),
        ]
        last_error: Exception | None = None
        docs: list[dict[str, Any]] = []
        num_found = 0
        ads_query = attempts[0][0]
        for ads_query, fields in attempts:
            try:
                docs, num_found = client.search(ads_query, max_rows=max_rows, fields=fields)
                last_error = None
                break
            except AdsQueryError as exc:
                log.warning("ADS query rejected (%s); trying a simpler form", exc)
                last_error = exc
        if last_error is not None:
            raise last_error
        papers = [to_paper(d) for d in docs if d.get("bibcode")]
        result = LiteratureResult(
            query=query,
            names=names,
            ads_query=ads_query,
            num_found=num_found,
            truncated=num_found > len(papers),
            papers=papers,
            metrics=bibliometrics(papers),
            token_source=token_source,
        )
        with self._lock:
            self._cache[key] = (now + self.cache_ttl, result)
        return result


_service: LiteratureService | None = None
_service_lock = threading.Lock()


def get_literature_service() -> LiteratureService:
    global _service
    with _service_lock:
        if _service is None:
            from app.config import get_settings

            _service = LiteratureService(cache_ttl=get_settings().cache_ttl)
        return _service
