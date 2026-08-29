from datetime import UTC, datetime
from pathlib import Path

import pytest

from auto_research_daily.analysis import (
    HeuristicAnalyzer,
    OpenAICompatibleAnalyzer,
    make_cache_key,
)
from auto_research_daily.config import load_config
from auto_research_daily.models import Evidence, RankedPaper, RawPaper, ScoreBreakdown

ROOT = Path(__file__).parents[1]


def _ranked(version: int = 1) -> RankedPaper:
    paper = RawPaper(
        canonical_id="2608.10000",
        version=version,
        published_at=datetime(2026, 8, 28, tzinfo=UTC),
        updated_at=datetime(2026, 8, 28, tzinfo=UTC),
        title="Robot World Model",
        authors=("A",),
        categories=("cs.RO",),
        abstract="An action-conditioned world model for robot control.",
        url="https://arxiv.org/abs/2608.10000",
    )
    score = ScoreBreakdown(
        topic=0.8,
        personal=0.7,
        recency=0.9,
        exploration=0.2,
        base_score=0.72,
    )
    return RankedPaper(paper=paper, score=score, rank=1)


def test_cache_key_changes_with_evidence_model_prompt_and_version() -> None:
    config = load_config(ROOT / "config/research.yaml")
    base = make_cache_key(
        _ranked(), full_text=None, model="model-a", prompt_version=config.analysis.prompt_version
    )
    assert base != make_cache_key(
        _ranked(2), full_text=None, model="model-a", prompt_version=config.analysis.prompt_version
    )
    assert base != make_cache_key(
        _ranked(),
        full_text="full text",
        model="model-a",
        prompt_version=config.analysis.prompt_version,
    )
    assert base != make_cache_key(
        _ranked(), full_text=None, model="model-b", prompt_version=config.analysis.prompt_version
    )
    assert base != make_cache_key(
        _ranked(), full_text=None, model="model-a", prompt_version="paper-analysis-v2"
    )
    assert base != make_cache_key(
        _ranked(),
        full_text=None,
        model="model-a",
        prompt_version=config.analysis.prompt_version,
        profile_fingerprint="changed-profile",
    )


def test_grounding_rejects_quote_not_in_supplied_material() -> None:
    ranked = _ranked()
    analysis = HeuristicAnalyzer.analyze(ranked, None).model_copy(
        update={
            "evidence": (
                Evidence(
                    claim="unsupported",
                    quote="This sentence never appeared in the paper.",
                    location="abstract",
                ),
            )
        }
    )
    with pytest.raises(ValueError, match="not present"):
        OpenAICompatibleAnalyzer._validate_grounding(analysis, ranked, None)
