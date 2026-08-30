from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from auto_research_daily.analysis import (
    HeuristicAnalyzer,
    OpenAICompatibleAnalyzer,
    analyze_ranked_papers,
    make_cache_key,
)
from auto_research_daily.config import load_config
from auto_research_daily.models import (
    Evidence,
    Provenance,
    RankedPaper,
    RawPaper,
    ReadingScope,
    ScoreBreakdown,
)

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


def test_deepseek_cost_controls_and_usage_accounting() -> None:
    config = load_config(ROOT / "config/research.yaml")
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    analyzer = OpenAICompatibleAnalyzer(
        config.analysis,
        config.research_profile,
        api_key="test-only",
        brief_model="deepseek-v4-flash",
        deep_model="deepseek-v4-flash",
        brief_thinking=False,
        deep_thinking=False,
        brief_reasoning_effort="low",
        deep_reasoning_effort="low",
        base_url="https://api.deepseek.com",
        prompt="return json",
        client=client,
    )
    assert analyzer.model_for(None) == "deepseek-v4-flash"
    assert analyzer.model_for("full text") == "deepseek-v4-flash"
    assert analyzer.thinking_for(None) is False
    assert analyzer.thinking_for("full text") is False
    assert analyzer.max_output_tokens_for(None) == 2500
    assert analyzer.max_output_tokens_for("full text") == 5000
    analyzer._record_usage({"usage": {"prompt_tokens": 100, "completion_tokens": 25}})
    assert analyzer.usage() == (100, 25)
    client.close()


def test_failure_ratio_counts_valid_cache_hits_in_the_publishable_batch() -> None:
    config = load_config(ROOT / "config/research.yaml")
    analysis_config = config.analysis.model_copy(
        update={"max_concurrency": 1, "max_failure_ratio": 0.30}
    )
    ranked = [_ranked(version) for version in range(1, 5)]
    cache = {}
    for item in ranked[:3]:
        analysis = HeuristicAnalyzer.analyze(item, None)
        key = make_cache_key(
            item,
            full_text=None,
            model=HeuristicAnalyzer.model,
            prompt_version=analysis_config.prompt_version,
        )
        cache[key] = {
            "analysis": analysis.model_dump(mode="json"),
            "provenance": Provenance(
                reading_scope=ReadingScope.ABSTRACT,
                model=HeuristicAnalyzer.model,
                prompt_version=analysis_config.prompt_version,
                analyzed_at=datetime(2026, 8, 29, tzinfo=UTC),
                input_hash="test-cache-entry",
            ).model_dump(mode="json"),
        }

    class FailingAnalyzer:
        model = HeuristicAnalyzer.model

        @classmethod
        def model_for(cls, full_text: str | None) -> str:
            return cls.model

        @staticmethod
        def analyze(ranked: RankedPaper, full_text: str | None) -> None:
            raise RuntimeError("deliberate model failure")

    results, _, errors = analyze_ranked_papers(
        ranked,
        full_texts={},
        analyzer=FailingAnalyzer(),  # type: ignore[arg-type]
        config=analysis_config,
        cache=cache,
    )

    assert len(results) == 3
    assert len(errors) == 1
