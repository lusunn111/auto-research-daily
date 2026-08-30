from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ResearchProfileConfig(ConfigModel):
    name: str
    description: str
    core_terms: tuple[str, ...]
    adjacent_terms: tuple[str, ...]
    negative_terms: tuple[str, ...] = ()
    topic_axes: dict[str, tuple[str, ...]] = Field(default_factory=dict)


class ArxivSourceConfig(ConfigModel):
    enabled: bool = True
    categories: tuple[str, ...]
    lookback_days: int = Field(default=3, ge=1, le=31)
    request_delay_seconds: float = Field(default=3.0, ge=0)
    timeout_seconds: float = Field(default=60.0, gt=0)
    retries: int = Field(default=4, ge=1, le=10)
    max_records: int = Field(default=2500, ge=1, le=10000)


class ZoteroSourceConfig(ConfigModel):
    enabled: bool = True
    max_items: int = Field(default=500, ge=1, le=5000)
    include_collections: tuple[str, ...] = ()
    exclude_collections: tuple[str, ...] = ()


class SourcesConfig(ConfigModel):
    arxiv: ArxivSourceConfig
    zotero: ZoteroSourceConfig


class RankingConfig(ConfigModel):
    candidate_pool: int = Field(default=120, ge=1)
    publish_limit: int = Field(default=40, ge=1)
    min_prefilter_score: float = Field(default=0.08, ge=0, le=1)
    min_llm_relevance: int = Field(default=6, ge=1, le=10)
    personal_weight: float = Field(default=0.45, ge=0)
    topic_weight: float = Field(default=0.35, ge=0)
    recency_weight: float = Field(default=0.10, ge=0)
    exploration_weight: float = Field(default=0.10, ge=0)
    diversity_lambda: float = Field(default=0.78, ge=0, le=1)

    @model_validator(mode="after")
    def normalize_weight_contract(self) -> RankingConfig:
        total = (
            self.personal_weight
            + self.topic_weight
            + self.recency_weight
            + self.exploration_weight
        )
        if abs(total - 1.0) > 1e-6:
            raise ValueError("ranking weights must sum to 1.0")
        if self.publish_limit > self.candidate_pool:
            raise ValueError("publish_limit cannot exceed candidate_pool")
        return self


class AnalysisConfig(ConfigModel):
    enabled: bool = True
    max_papers: int = Field(default=60, ge=1)
    max_concurrency: int = Field(default=4, ge=1, le=16)
    max_failure_ratio: float = Field(default=0.30, ge=0, le=1)
    prompt_version: str
    brief_max_output_tokens: int = Field(default=3500, ge=256, le=32000)
    deep_max_output_tokens: int = Field(default=8000, ge=256, le=64000)
    full_text_top_k: int = Field(default=12, ge=0)
    full_text_max_chars: int = Field(default=18000, ge=1000, le=100000)
    brief_model_default: str
    deep_model_default: str
    brief_thinking: bool = False
    deep_thinking: bool = False
    brief_reasoning_effort: Literal["low", "high", "max"] = "low"
    deep_reasoning_effort: Literal["low", "high", "max"] = "low"
    base_url_default: str


class MailConfig(ConfigModel):
    enabled: bool = True
    send_empty: bool = True
    top_detail_limit: int = Field(default=5, ge=0, le=10)
    html_byte_limit: int = Field(default=61440, ge=16384, le=262144)
    template_version: str = "email-v1"


class OutputConfig(ConfigModel):
    data_dir: Path
    reports_dir: Path
    site_dir: Path
    title: str
    timezone: str = "Asia/Shanghai"


class AppConfig(ConfigModel):
    version: int = 1
    research_profile: ResearchProfileConfig
    sources: SourcesConfig
    ranking: RankingConfig
    analysis: AnalysisConfig
    email: MailConfig
    output: OutputConfig


def load_config(path: Path) -> AppConfig:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("configuration root must be a mapping")
    return AppConfig.model_validate(payload)
