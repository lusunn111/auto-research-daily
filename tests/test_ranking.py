import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import TypeAdapter

from auto_research_daily.config import load_config
from auto_research_daily.models import RawPaper, ZoteroDocument
from auto_research_daily.ranking import deduplicate_papers, rank_papers

ROOT = Path(__file__).parents[1]


def _fixture() -> tuple[list[RawPaper], list[ZoteroDocument]]:
    payload = json.loads((ROOT / "tests/fixtures/offline_daily.json").read_text())
    papers = TypeAdapter(list[RawPaper]).validate_python(payload["papers"])
    documents = TypeAdapter(list[ZoteroDocument]).validate_python(payload["zotero"])
    return papers, documents


def test_deduplicate_keeps_newer_version() -> None:
    papers, _ = _fixture()
    older = papers[0]
    newer = older.model_copy(update={"version": 2})
    result = deduplicate_papers([older, newer])
    assert len(result) == 1
    assert result[0].version == 2


def test_ranking_is_deterministic_and_explainable() -> None:
    config = load_config(ROOT / "config/research.yaml")
    papers, documents = _fixture()
    now = datetime(2026, 8, 29, tzinfo=UTC)
    first = rank_papers(
        papers,
        profile=config.research_profile,
        config=config.ranking,
        documents=documents,
        now=now,
    )
    second = rank_papers(
        papers,
        profile=config.research_profile,
        config=config.ranking,
        documents=documents,
        now=now,
    )
    assert [item.paper.identity for item in first] == [item.paper.identity for item in second]
    assert all(item.score.matched_terms for item in first)
    assert all(0 <= item.score.base_score <= 1 for item in first)
