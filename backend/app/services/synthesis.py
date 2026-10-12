"""Scientific synthesis of ADS abstracts with Gemini (Module 6)."""

from __future__ import annotations

import logging
import threading
import time

from app.schemas.literature import (
    Bibliometrics,
    FacilityNote,
    KeyPaper,
    KeywordCluster,
    Paper,
    Synthesis,
    SynthesisResult,
)
from app.services.gemini import GeminiClient

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an expert extragalactic astronomer writing a concise, factual research
briefing about one astronomical target from a set of ADS abstracts. Rules:
- Use only information contained in the abstracts and statistics provided. Do not invent results,
  numbers, or papers. Cite papers by their bibcode in square brackets, e.g. [2019ApJ...876...12A].
- Distinguish established, repeatedly confirmed results (consensus) from single-paper claims,
  tensions between papers, and explicitly stated open questions.
- Be specific and quantitative where the abstracts are (redshifts, masses, rates, resolutions).
- Keep each bullet to one or two sentences. Prefer recent, highly cited work but do not ignore
  older foundational results.
- Respond only with JSON matching the requested schema."""

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "summary": {"type": "STRING"},
        "consensus": {"type": "ARRAY", "items": {"type": "STRING"}},
        "open_questions": {"type": "ARRAY", "items": {"type": "STRING"}},
        "key_papers": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {"bibcode": {"type": "STRING"}, "reason": {"type": "STRING"}},
                "required": ["bibcode", "reason"],
            },
        },
        "keyword_clusters": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "theme": {"type": "STRING"},
                    "keywords": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "bibcodes": {"type": "ARRAY", "items": {"type": "STRING"}},
                },
                "required": ["theme", "keywords"],
            },
        },
        "facilities": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {"name": {"type": "STRING"}, "role": {"type": "STRING"}},
                "required": ["name", "role"],
            },
        },
        "publication_trend": {"type": "STRING"},
    },
    "required": [
        "summary",
        "consensus",
        "open_questions",
        "key_papers",
        "keyword_clusters",
        "facilities",
        "publication_trend",
    ],
}


def select_abstracts(
    papers: list[Paper], metrics: Bibliometrics, max_abstracts: int = 60
) -> list[Paper]:
    """Top-cited plus most-recent refereed papers that have an abstract."""
    by_bib = {p.bibcode: p for p in papers if p.abstract}
    chosen: list[Paper] = []
    seen: set[str] = set()
    half = max(1, max_abstracts // 2)
    for bib in metrics.top_cited:
        if bib in by_bib and bib not in seen and len(chosen) < half:
            chosen.append(by_bib[bib])
            seen.add(bib)
    for bib in metrics.most_recent:
        if bib in by_bib and bib not in seen and len(chosen) < max_abstracts:
            if by_bib[bib].refereed or len(chosen) < max_abstracts // 2:
                chosen.append(by_bib[bib])
                seen.add(bib)
    # fill with remaining highest-cited if still short
    for p in sorted(by_bib.values(), key=lambda p: -p.citation_count):
        if len(chosen) >= max_abstracts:
            break
        if p.bibcode not in seen:
            chosen.append(p)
            seen.add(p.bibcode)
    return chosen


def build_user_prompt(
    target: str, papers: list[Paper], metrics: Bibliometrics, aliases: list[str]
) -> str:
    lines = [f"TARGET: {target}"]
    if aliases:
        lines.append("ALIASES: " + ", ".join(aliases[:12]))
    years = metrics.years
    if years:
        span = f"{years[0].year}–{years[-1].year}"
        peak = max(years, key=lambda y: y.count)
        lines.append(
            f"STATISTICS: {metrics.total} ADS papers ({metrics.refereed} refereed), {span}, "
            f"peak year {peak.year} ({peak.count} papers), "
            f"total citations {metrics.total_citations}, h-index {metrics.h_index}."
        )
        recent = [y for y in years if y.year >= years[-1].year - 4]
        lines.append(
            "PAPERS PER YEAR (last decade): "
            + ", ".join(f"{y.year}:{y.count}" for y in years if y.year >= years[-1].year - 9)
        )
        if recent:
            lines.append(f"Recent 5-year total: {sum(y.count for y in recent)} papers.")
    if metrics.facilities:
        lines.append(
            "FACILITIES MENTIONED (paper counts): "
            + ", ".join(f"{f.name} ({f.count})" for f in metrics.facilities[:20])
        )
    if metrics.keywords:
        lines.append(
            "FREQUENT ADS KEYWORDS: "
            + ", ".join(f"{k.name} ({k.count})" for k in metrics.keywords[:25])
        )
    lines.append("")
    lines.append(f"ABSTRACTS ({len(papers)} papers, most cited and most recent):")
    for p in papers:
        head = (
            f"[{p.bibcode}] {p.year or '????'} | {p.first_author or '?'} et al. | "
            f"{p.citation_count} citations | {p.journal or ''}"
        )
        lines.append(head)
        if p.title:
            lines.append(f"Title: {p.title}")
        lines.append(f"Abstract: {(p.abstract or '').strip()}")
        lines.append("")
    lines.append(
        "TASK: Produce the JSON briefing: a 4–6 sentence summary; consensus bullets; "
        "open-question / peculiarity bullets; 5–10 key papers with one-line reasons; "
        "4–8 keyword clusters (theme, representative keywords, supporting bibcodes); "
        "facilities with the role each played for this target; and a short reading of the "
        "publication trend using the statistics above."
    )
    return "\n".join(lines)


def _coerce(parsed: dict, known_bibcodes: set[str]) -> tuple[Synthesis, list[str]]:
    warnings: list[str] = []

    def strs(v) -> list[str]:
        return [str(x) for x in (v or []) if str(x).strip()]

    key_papers = []
    for kp in parsed.get("key_papers") or []:
        if isinstance(kp, dict) and kp.get("bibcode"):
            bib = str(kp["bibcode"]).strip("[] ")
            if bib not in known_bibcodes:
                warnings.append(f"key paper {bib} is not among the supplied abstracts")
            key_papers.append(KeyPaper(bibcode=bib, reason=str(kp.get("reason", ""))))
    clusters = []
    for c in parsed.get("keyword_clusters") or []:
        if isinstance(c, dict) and c.get("theme"):
            clusters.append(
                KeywordCluster(
                    theme=str(c["theme"]),
                    keywords=strs(c.get("keywords")),
                    bibcodes=[b.strip("[] ") for b in strs(c.get("bibcodes"))],
                )
            )
    facilities = [
        FacilityNote(name=str(f["name"]), role=str(f.get("role", "")))
        for f in (parsed.get("facilities") or [])
        if isinstance(f, dict) and f.get("name")
    ]
    syn = Synthesis(
        summary=str(parsed.get("summary", "")),
        consensus=strs(parsed.get("consensus")),
        open_questions=strs(parsed.get("open_questions")),
        key_papers=key_papers,
        keyword_clusters=clusters,
        facilities=facilities,
        publication_trend=str(parsed.get("publication_trend", "")),
    )
    return syn, warnings


class SynthesisService:
    def __init__(self, cache_ttl: int = 3600, client_factory=GeminiClient):
        self.cache_ttl = cache_ttl
        self.client_factory = client_factory
        self._cache: dict[tuple, tuple[float, SynthesisResult]] = {}
        self._lock = threading.Lock()

    def synthesise(
        self,
        target: str,
        aliases: list[str],
        papers: list[Paper],
        metrics: Bibliometrics,
        api_key: str,
        key_source: str,
        model: str,
        max_abstracts: int = 60,
    ) -> SynthesisResult:
        chosen = select_abstracts(papers, metrics, max_abstracts)
        if not chosen:
            raise ValueError("No abstracts available to synthesise")
        key = (target.lower(), model, tuple(p.bibcode for p in chosen))
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(key)
            if hit and hit[0] > now:
                return hit[1].model_copy(update={"cached": True})
        prompt = build_user_prompt(target, chosen, metrics, aliases)
        client = self.client_factory(api_key, model)
        parsed, usage = client.generate_json(SYSTEM_PROMPT, prompt, RESPONSE_SCHEMA)
        syn, warnings = _coerce(parsed, {p.bibcode for p in chosen})
        result = SynthesisResult(
            query=target,
            model=client.model,
            n_abstracts=len(chosen),
            bibcodes_used=[p.bibcode for p in chosen],
            synthesis=syn,
            prompt_tokens=usage.get("promptTokenCount"),
            output_tokens=usage.get("candidatesTokenCount"),
            key_source=key_source,
            warnings=warnings,
        )
        with self._lock:
            self._cache[key] = (now + self.cache_ttl, result)
        return result


_service: SynthesisService | None = None
_service_lock = threading.Lock()


def get_synthesis_service() -> SynthesisService:
    global _service
    with _service_lock:
        if _service is None:
            from app.config import get_settings

            _service = SynthesisService(cache_ttl=get_settings().cache_ttl)
        return _service
