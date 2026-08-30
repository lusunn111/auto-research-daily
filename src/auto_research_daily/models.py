from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Literal
from urllib.parse import unquote, urlsplit

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


class FigurePanel(StrictModel):
    original_url: str
    cached_path: str | None = Field(
        default=None,
        pattern=(
            r"^figures/arxiv/\d{4}\.\d{4,5}/v[1-9]\d*/"
            r"fig[12]-panel[1-9]\d*\.(?:png|jpg|webp)$"
        ),
    )

    @field_validator("original_url")
    @classmethod
    def validate_original_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        try:
            port = parsed.port
        except ValueError as error:
            raise ValueError("figure URL has an invalid port") from error
        decoded_path = unquote(parsed.path)
        encoded_path = parsed.path.casefold()
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"arxiv.org", "www.arxiv.org"}
            or parsed.username is not None
            or parsed.password is not None
            or port not in (None, 443)
            or parsed.query
            or parsed.fragment
            or re.search(r"%(?:2e|2f|5c)", encoded_path) is not None
            or "\\" in decoded_path
            or "\x00" in decoded_path
            or any(component in {".", ".."} for component in decoded_path.split("/"))
        ):
            raise ValueError("figure URL must be a safe arXiv HTTPS URL")
        return value


class FigureAsset(StrictModel):
    number: Literal[1, 2]
    label: str = Field(min_length=1)
    caption: str = Field(min_length=1, max_length=4000)
    panels: tuple[FigurePanel, ...] = Field(min_length=1, max_length=8)
    source_url: str
    source: Literal["arxiv_html"] = "arxiv_html"


class FigureGallery(StrictModel):
    status: Literal["available", "html_unavailable", "not_found", "fetch_failed"]
    html_url: str
    checked_at: datetime
    figures: tuple[FigureAsset, ...] = Field(default=(), max_length=2)

    @model_validator(mode="after")
    def validate_gallery(self) -> FigureGallery:
        if self.checked_at.tzinfo is None:
            raise ValueError("figure timestamp must be timezone-aware")
        if self.status == "available" and not self.figures:
            raise ValueError("available figure gallery requires figures")
        if self.status != "available" and self.figures:
            raise ValueError("unavailable figure gallery cannot contain figures")
        numbers = [figure.number for figure in self.figures]
        if len(numbers) != len(set(numbers)):
            raise ValueError("figure numbers must be unique")
        parsed_html = urlsplit(self.html_url)
        identity_match = re.fullmatch(
            r"/html/(?P<arxiv_id>\d{4}\.\d{4,5})v(?P<version>[1-9]\d*)",
            parsed_html.path,
        )
        if (
            parsed_html.scheme != "https"
            or parsed_html.hostname not in {"arxiv.org", "www.arxiv.org"}
            or parsed_html.username is not None
            or parsed_html.password is not None
            or parsed_html.port not in (None, 443)
            or parsed_html.query
            or parsed_html.fragment
            or identity_match is None
        ):
            raise ValueError("figure gallery must identify one versioned arXiv HTML paper")
        arxiv_id = identity_match.group("arxiv_id")
        version = int(identity_match.group("version"))
        expected_prefix = f"{parsed_html.path}/"
        for figure in self.figures:
            source = urlsplit(figure.source_url)
            if figure.source == "arxiv_html" and (
                source.scheme != "https"
                or source.hostname not in {"arxiv.org", "www.arxiv.org"}
                or source.username is not None
                or source.password is not None
                or source.port not in (None, 443)
                or source.path != parsed_html.path
                or source.query
                or not source.fragment
                or re.fullmatch(r"[A-Za-z0-9._~%:-]+", source.fragment) is None
            ):
                raise ValueError("figure source must belong to the gallery paper")
            for panel_number, panel in enumerate(figure.panels, start=1):
                panel_url = urlsplit(panel.original_url)
                if not panel_url.path.startswith(expected_prefix):
                    raise ValueError("figure panel must belong to the gallery paper")
                if panel.cached_path is not None:
                    expected_path = (
                        f"figures/arxiv/{arxiv_id}/v{version}/"
                        f"fig{figure.number}-panel{panel_number}."
                    )
                    if not panel.cached_path.startswith(expected_path):
                        raise ValueError("cached figure path does not match its panel")
        return self


class AnalyzedPaper(StrictModel):
    ranked: RankedPaper
    analysis: PaperAnalysis
    provenance: Provenance
    final_score: float = Field(ge=0.0, le=1.0)
    tier: Literal["deep_read", "browse", "explore"] = "browse"
    figure_gallery: FigureGallery | None = None


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
    figure_cache_hits: int = Field(default=0, ge=0)
    figure_requests: int = Field(default=0, ge=0)
    figure_available: int = Field(default=0, ge=0)
    figure_failed: int = Field(default=0, ge=0)
    figure_panel_failed: int = Field(default=0, ge=0)


class RunReport(StrictModel):
    schema_version: int = 1
    generated_at: datetime
    dry_run: bool
    stats: RunStats
    papers: tuple[AnalyzedPaper, ...]
