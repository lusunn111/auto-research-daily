from datetime import UTC, datetime
from pathlib import Path

from auto_research_daily.analysis import HeuristicAnalyzer
from auto_research_daily.config import load_config
from auto_research_daily.emailing import (
    MailSettings,
    build_report_message,
    edition_fingerprint,
    select_new_papers,
)
from auto_research_daily.models import (
    AnalyzedPaper,
    Provenance,
    RankedPaper,
    RawPaper,
    ReadingScope,
    RunReport,
    RunStats,
    ScoreBreakdown,
)

ROOT = Path(__file__).parents[1]


def _report(title: str = "Robot <World> Model") -> RunReport:
    paper = RawPaper(
        canonical_id="2608.10000",
        version=1,
        published_at=datetime(2026, 8, 28, tzinfo=UTC),
        updated_at=datetime(2026, 8, 28, tzinfo=UTC),
        title=title,
        authors=("A",),
        categories=("cs.RO",),
        abstract="An action-conditioned world model for robot control.",
        url="https://arxiv.org/abs/2608.10000",
    )
    ranked = RankedPaper(
        paper=paper,
        score=ScoreBreakdown(
            topic=0.8,
            personal=0.7,
            recency=0.9,
            exploration=0.2,
            base_score=0.72,
        ),
        rank=1,
    )
    analysis = HeuristicAnalyzer.analyze(ranked, None)
    analyzed = AnalyzedPaper(
        ranked=ranked,
        analysis=analysis,
        provenance=Provenance(
            reading_scope=ReadingScope.ABSTRACT,
            model="deepseek-v4-flash",
            prompt_version="paper-analysis-v1",
            analyzed_at=datetime(2026, 8, 30, tzinfo=UTC),
            input_hash="a" * 64,
        ),
        final_score=0.8,
        tier="browse",
    )
    return RunReport(
        generated_at=datetime(2026, 8, 30, 4, 30, tzinfo=UTC),
        dry_run=False,
        stats=RunStats(
            fetched=1,
            deduplicated=1,
            preselected=1,
            cache_hits=0,
            model_calls=1,
            full_text_reads=0,
            published=1,
            failed=0,
        ),
        papers=(analyzed,),
    )


def _settings() -> MailSettings:
    return MailSettings(
        host="smtp.qq.com",
        port=465,
        username="sender@example.com",
        auth_code="test-only",
        sender="sender@example.com",
        recipient="recipient@example.com",
        site_url="https://example.com/auto-research-daily/",
    )


def test_fingerprint_is_deterministic_and_state_filters_previous_version() -> None:
    report = _report()
    first = edition_fingerprint(report, report.papers, "email-v1")
    assert first == edition_fingerprint(report, report.papers, "email-v1")
    state = {
        "editions": {
            "2026-08-29": {
                "paper_identities": [report.papers[0].ranked.paper.identity],
            }
        }
    }
    assert select_new_papers(report, state) == ()


def test_email_is_escaped_and_links_stable_archive() -> None:
    config = load_config(ROOT / "config/research.yaml")
    report = _report()
    fingerprint = edition_fingerprint(report, report.papers, config.email.template_version)
    message = build_report_message(
        report,
        report.papers,
        settings=_settings(),
        config=config.email,
        title=config.output.title,
        template_dir=ROOT / "src" / "auto_research_daily" / "templates",
        fingerprint=fingerprint,
    )
    html = message.get_body(preferencelist=("html",)).get_content()
    assert "Robot &lt;World&gt; Model" in html
    assert "archive/2026-08-30.html" in html
    assert len(html.encode()) <= config.email.html_byte_limit
    assert str(message["Message-ID"]).startswith("<auto-research-daily-20260830-")
