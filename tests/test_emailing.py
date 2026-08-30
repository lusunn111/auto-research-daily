import json
import smtplib
from datetime import UTC, datetime
from pathlib import Path

import pytest

import auto_research_daily.emailing as emailing
from auto_research_daily.analysis import HeuristicAnalyzer
from auto_research_daily.config import load_config
from auto_research_daily.emailing import (
    MailSettings,
    build_report_message,
    edition_fingerprint,
    notify_report,
    select_new_papers,
    send_message,
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
    first = edition_fingerprint(
        report,
        report.papers,
        "email-v1",
        recipient="recipient@example.com",
    )
    assert first == edition_fingerprint(
        report,
        report.papers,
        "email-v1",
        recipient="recipient@example.com",
    )
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
    fingerprint = edition_fingerprint(
        report,
        report.papers,
        config.email.template_version,
        recipient=_settings().recipient,
    )
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


def _notify(tmp_path: Path, *, force: bool = False) -> dict[str, object]:
    config = load_config(ROOT / "config/research.yaml")
    return notify_report(
        _report(),
        settings=_settings(),
        config=config.email,
        title=config.output.title,
        template_dir=ROOT / "src" / "auto_research_daily" / "templates",
        state_path=tmp_path / "notifications.json",
        force=force,
    )


def test_notify_records_success_and_skips_same_day(monkeypatch, tmp_path: Path) -> None:
    messages = []
    monkeypatch.setattr(
        emailing,
        "send_message",
        lambda message, settings: messages.append(message),
    )
    assert _notify(tmp_path)["status"] == "sent"
    state = json.loads((tmp_path / "notifications.json").read_text())
    assert state["editions"]["2026-08-30"]["paper_identities"] == [
        "arxiv:2608.10000:v1"
    ]
    assert _notify(tmp_path)["status"] == "skipped"
    assert len(messages) == 1


def test_notify_failure_does_not_record_state(monkeypatch, tmp_path: Path) -> None:
    def fail(*args, **kwargs):
        raise RuntimeError("smtp failed")

    monkeypatch.setattr(emailing, "send_message", fail)
    with pytest.raises(RuntimeError, match="smtp failed"):
        _notify(tmp_path)
    assert not (tmp_path / "notifications.json").exists()


def test_force_revision_has_new_message_id(monkeypatch, tmp_path: Path) -> None:
    messages = []
    monkeypatch.setattr(
        emailing,
        "send_message",
        lambda message, settings: messages.append(message),
    )
    _notify(tmp_path)
    _notify(tmp_path, force=True)
    assert messages[0]["Message-ID"] != messages[1]["Message-ID"]
    assert "修订 1" in str(messages[1]["Subject"])


def test_quit_failure_after_data_is_treated_as_delivered(monkeypatch) -> None:
    class Server:
        def __init__(self, *args, **kwargs):
            self.sent = False

        def login(self, username, auth_code):
            return None

        def send_message(self, message):
            self.sent = True

        def quit(self):
            raise smtplib.SMTPServerDisconnected("quit failed")

        def close(self):
            return None

    monkeypatch.setattr(emailing.smtplib, "SMTP_SSL", Server)
    send_message(emailing.build_test_message(_settings()), _settings())


def test_dry_run_settings_do_not_require_auth_code(monkeypatch) -> None:
    monkeypatch.setenv("SMTP_USERNAME", "sender@example.com")
    monkeypatch.setenv("MAIL_TO", "recipient@example.com")
    monkeypatch.setenv("SITE_URL", "https://example.com/")
    monkeypatch.delenv("SMTP_AUTH_CODE", raising=False)
    assert MailSettings.from_env(require_auth=False).auth_code is None
