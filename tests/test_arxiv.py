from datetime import UTC, datetime
from pathlib import Path

from auto_research_daily.sources.arxiv import ArxivSource

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_oai_page() -> None:
    papers, token = ArxivSource.parse_page(
        (FIXTURES / "arxiv_oai.xml").read_bytes(),
        categories=frozenset({"cs.RO"}),
        since=datetime(2026, 8, 27, tzinfo=UTC),
        until=datetime(2026, 8, 29, tzinfo=UTC),
    )

    assert token == "next-page"
    assert len(papers) == 1
    assert papers[0].identity == "arxiv:2608.12345:v1"
    assert papers[0].authors == ("Ada Researcher", "Bo Systems")


def test_parse_oai_page_filters_categories() -> None:
    papers, _ = ArxivSource.parse_page(
        (FIXTURES / "arxiv_oai.xml").read_bytes(),
        categories=frozenset({"math.OC"}),
        since=datetime(2026, 8, 27, tzinfo=UTC),
        until=datetime(2026, 8, 29, tzinfo=UTC),
    )
    assert papers == []
