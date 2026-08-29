from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class ReadingScope(StrEnum):
    ABSTRACT = "abstract"
    FULL_TEXT = "full_text"


class RawPaper(StrictModel):
    source: str = "arxiv"
    canonical_id: str
    version: int = Field(default=1, ge=1)
    published_at: datetime
    updated_at: datetime
    title: str = Field(min_length=1)
    authors: tuple[str, ...] = Field(min_length=1)
    categories: tuple[str, ...] = Field(min_length=1)
    abstract: str = Field(min_length=1)
    comment: str | None = None
    url: str
    pdf_url: str | None = None

    @field_validator("authors", "categories")
    @classmethod
    def deduplicate_tuple(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(dict.fromkeys(value.strip() for value in values if value.strip()))
        if not cleaned:
            raise ValueError("tuple must contain at least one non-empty value")
        return cleaned

    @model_validator(mode="after")
    def validate_times(self) -> RawPaper:
        if self.published_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("paper timestamps must be timezone-aware")
        return self

    @property
    def identity(self) -> str:
        return f"{self.source}:{self.canonical_id}:v{self.version}"


class ZoteroDocument(StrictModel):
    key: str
    title: str
    abstract: str
    date_added: datetime
    collections: tuple[str, ...] = ()


class ScoreBreakdown(StrictModel):
    topic: float = Field(ge=0.0, le=1.0)
    personal: float = Field(ge=0.0, le=1.0)
    recency: float = Field(ge=0.0, le=1.0)
    exploration: float = Field(ge=0.0, le=1.0)
    base_score: float = Field(ge=0.0, le=1.0)
    matched_terms: tuple[str, ...] = ()
    negative_terms: tuple[str, ...] = ()


class RankedPaper(StrictModel):
    paper: RawPaper
    score: ScoreBreakdown
    rank: int = Field(ge=1)


class Evidence(StrictModel):
    claim: str = Field(min_length=1)
    quote: str = Field(min_length=1, max_length=800)
    location: Literal["abstract", "supplied_full_text"]


class PaperAnalysis(StrictModel):
    title_zh: str = Field(min_length=1)
    first_affiliation: str = Field(min_length=1)
    corresponding_authors: tuple[str, ...] = Field(min_length=1, max_length=8)
    relevance_score: int = Field(ge=1, le=10)
    primary_topic: str = Field(min_length=1)
    tags: tuple[str, ...] = Field(min_length=1, max_length=12)
    setting: str = Field(min_length=1)
    motivation: str = Field(min_length=1)
    insight: str = Field(min_length=1)
    challenges: tuple[str, ...] = Field(min_length=1, max_length=8)
    analysis: str = Field(min_length=1)
    method: tuple[str, ...] = Field(min_length=1, max_length=12)
    experiments: tuple[str, ...] = Field(min_length=1, max_length=12)
    limitations: tuple[str, ...] = Field(min_length=1, max_length=8)
    relation_to_research: str = Field(min_length=1)
    why_recommended: str = Field(min_length=1)
    uncertainty: str = Field(min_length=1)
    evidence: tuple[Evidence, ...] = Field(min_length=1, max_length=8)


class Provenance(StrictModel):
    reading_scope: ReadingScope
    model: str
    prompt_version: str
    analyzed_at: datetime
    input_hash: str


class AnalyzedPaper(StrictModel):
    ranked: RankedPaper
    analysis: PaperAnalysis
    provenance: Provenance
    final_score: float = Field(ge=0.0, le=1.0)
    tier: Literal["deep_read", "browse", "explore"] = "browse"


class RunStats(StrictModel):
    fetched: int = Field(ge=0)
    deduplicated: int = Field(ge=0)
    preselected: int = Field(ge=0)
    cache_hits: int = Field(ge=0)
    model_calls: int = Field(ge=0)
    full_text_reads: int = Field(ge=0)
    published: int = Field(ge=0)
    failed: int = Field(ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)


class RunReport(StrictModel):
    schema_version: int = 1
    generated_at: datetime
    dry_run: bool
    stats: RunStats
    papers: tuple[AnalyzedPaper, ...]
