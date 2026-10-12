"""Models for Module 5 (ADS bibliometrics) and Module 6 (LLM synthesis)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Paper(BaseModel):
    bibcode: str
    title: str | None = None
    first_author: str | None = None
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    pubdate: str | None = None
    journal: str | None = None
    doctype: str | None = None
    refereed: bool = False
    citation_count: int = 0
    keywords: list[str] = Field(default_factory=list)
    facilities: list[str] = Field(
        default_factory=list, description="Telescopes / instruments detected"
    )
    abstract: str | None = None
    ads_url: str


class YearCount(BaseModel):
    year: int
    count: int
    refereed: int


class NamedCount(BaseModel):
    name: str
    count: int


class CitationBin(BaseModel):
    label: str
    min: int
    max: int | None
    count: int


class Bibliometrics(BaseModel):
    total: int
    refereed: int
    years: list[YearCount]
    facilities: list[NamedCount]
    keywords: list[NamedCount]
    citation_bins: list[CitationBin]
    total_citations: int
    h_index: int
    top_cited: list[str] = Field(description="bibcodes, most cited first")
    most_recent: list[str] = Field(description="bibcodes, newest first")


class LiteratureResult(BaseModel):
    query: str
    names: list[str]
    ads_query: str
    num_found: int = Field(description="Total hits reported by ADS")
    truncated: bool
    papers: list[Paper]
    metrics: Bibliometrics
    token_source: str = Field(description="header | server")
    cached: bool = False


class KeyPaper(BaseModel):
    bibcode: str
    reason: str


class KeywordCluster(BaseModel):
    theme: str
    keywords: list[str] = Field(default_factory=list)
    bibcodes: list[str] = Field(default_factory=list)


class FacilityNote(BaseModel):
    name: str
    role: str


class Synthesis(BaseModel):
    summary: str
    consensus: list[str] = Field(description="Established properties and agreed results")
    open_questions: list[str] = Field(
        description="Peculiarities, tensions, open observational questions"
    )
    key_papers: list[KeyPaper] = Field(default_factory=list)
    keyword_clusters: list[KeywordCluster] = Field(default_factory=list)
    facilities: list[FacilityNote] = Field(default_factory=list)
    publication_trend: str = ""


class SynthesisResult(BaseModel):
    query: str
    model: str
    provider: str = "gemini"
    n_abstracts: int
    bibcodes_used: list[str]
    synthesis: Synthesis
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    key_source: str = Field(description="header | server")
    cached: bool = False
    warnings: list[str] = Field(default_factory=list)


class KeyStatus(BaseModel):
    ads_server_key: bool
    gemini_server_key: bool
    gemini_model: str
