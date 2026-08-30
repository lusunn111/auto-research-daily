from __future__ import annotations

import hashlib
import json
import os
import smtplib
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from jinja2 import Environment, FileSystemLoader, select_autoescape

from auto_research_daily.config import MailConfig
from auto_research_daily.models import AnalyzedPaper, RunReport
from auto_research_daily.storage import atomic_write_json, load_json

TIER_LABELS = {
    "deep_read": "今日必读",
    "browse": "值得浏览",
    "explore": "探索发现",
}


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"缺少环境变量 {name}")
    return value


def _safe_header(name: str, value: str) -> str:
    if "\r" in value or "\n" in value:
        raise ValueError(f"{name} 不能包含换行符")
    if name in {"MAIL_FROM", "MAIL_TO", "SMTP_USERNAME"} and "@" not in value:
        raise ValueError(f"{name} 不是有效邮箱地址")
    return value


@dataclass(frozen=True)
class MailSettings:
    host: str
    port: int
    username: str
    auth_code: str
    sender: str
    recipient: str
    site_url: str

    @classmethod
    def from_env(cls) -> MailSettings:
        username = _safe_header("SMTP_USERNAME", _required_env("SMTP_USERNAME"))
        sender = _safe_header("MAIL_FROM", os.getenv("MAIL_FROM", username).strip())
        recipient = _safe_header("MAIL_TO", _required_env("MAIL_TO"))
        port_text = os.getenv("SMTP_PORT", "465").strip()
        try:
            port = int(port_text)
        except ValueError as error:
            raise ValueError("SMTP_PORT 必须是整数") from error
        if port != 465:
            raise ValueError("当前实现只允许 SMTP_SSL 465 端口")
        return cls(
            host=os.getenv("SMTP_HOST", "smtp.qq.com").strip(),
            port=port,
            username=username,
            auth_code=_required_env("SMTP_AUTH_CODE"),
            sender=sender,
            recipient=recipient,
            site_url=_required_env("SITE_URL").rstrip("/") + "/",
        )


def _analysis_identity(item: AnalyzedPaper) -> str:
    return f"{item.ranked.paper.identity}:{item.provenance.input_hash}"


def edition_fingerprint(
    report: RunReport,
    papers: tuple[AnalyzedPaper, ...],
    template_version: str,
) -> str:
    payload = {
        "schema_version": 1,
        "date": report.generated_at.date().isoformat(),
        "template_version": template_version,
        "papers": [_analysis_identity(item) for item in papers],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _notified_identities(state: dict[str, Any]) -> set[str]:
    identities: set[str] = set()
    editions = state.get("editions", {})
    if not isinstance(editions, dict):
        return identities
    for edition in editions.values():
        if isinstance(edition, dict):
            values = edition.get("paper_identities", [])
            if isinstance(values, list):
                identities.update(str(value) for value in values)
    return identities


def select_new_papers(report: RunReport, state: dict[str, Any]) -> tuple[AnalyzedPaper, ...]:
    notified = _notified_identities(state)
    return tuple(item for item in report.papers if item.ranked.paper.identity not in notified)


def _archive_url(settings: MailSettings, report: RunReport) -> str:
    date_name = report.generated_at.strftime("%Y-%m-%d")
    return urljoin(settings.site_url, f"archive/{date_name}.html")


def _render_plain_text(
    report: RunReport,
    papers: tuple[AnalyzedPaper, ...],
    *,
    title: str,
    archive_url: str,
    detail_limit: int,
) -> str:
    lines = [
        f"{title}：{report.generated_at:%Y-%m-%d}",
        "",
        f"今日新增推荐 {len(papers)} 篇，全文级解读 "
        f"{sum(item.tier == 'deep_read' for item in papers)} 篇。",
        f"完整日报：{archive_url}",
        "",
    ]
    for index, item in enumerate(papers, start=1):
        analysis = item.analysis
        paper = item.ranked.paper
        lines.extend(
            [
                f"{index}. [{TIER_LABELS[item.tier]}] {analysis.title_zh}",
                paper.title,
                f"相关性：{analysis.relevance_score}/10",
                f"核心洞察：{analysis.insight}",
                f"推荐理由：{analysis.why_recommended}",
            ]
        )
        if index <= detail_limit:
            lines.extend(
                [
                    f"研究动机：{analysis.motivation}",
                    "方法：" + "；".join(analysis.method),
                    "实验：" + "；".join(analysis.experiments),
                    f"与你的研究关系：{analysis.relation_to_research}",
                ]
            )
        lines.extend([f"论文：{paper.url}", ""])
    lines.extend(["历史日报和 RSS 可从完整日报页面进入。", ""])
    return "\n".join(lines)


def _render_html(
    report: RunReport,
    papers: tuple[AnalyzedPaper, ...],
    *,
    title: str,
    archive_url: str,
    detail_limit: int,
    template_dir: Path,
) -> str:
    environment = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=select_autoescape(("html", "xml", "j2")),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = environment.get_template("email.html.j2")
    return template.render(
        report=report,
        papers=papers,
        title=title,
        archive_url=archive_url,
        detail_limit=detail_limit,
        tier_labels=TIER_LABELS,
    )


def _subject(report: RunReport, papers: tuple[AnalyzedPaper, ...], *, revision: bool) -> str:
    deep_count = sum(item.tier == "deep_read" for item in papers)
    prefix = "【修订】" if revision else ""
    return (
        f"{prefix}【具身智能科研日报】{report.generated_at:%Y-%m-%d}｜"
        f"{deep_count} 篇必读，{len(papers)} 篇新增"
    )


def build_report_message(
    report: RunReport,
    papers: tuple[AnalyzedPaper, ...],
    *,
    settings: MailSettings,
    config: MailConfig,
    title: str,
    template_dir: Path,
    fingerprint: str,
    revision: bool = False,
) -> EmailMessage:
    subject = _safe_header("Subject", _subject(report, papers, revision=revision))
    archive_url = _archive_url(settings, report)
    detail_limit = min(config.top_detail_limit, len(papers))
    html = _render_html(
        report,
        papers,
        title=title,
        archive_url=archive_url,
        detail_limit=detail_limit,
        template_dir=template_dir,
    )
    if len(html.encode()) > config.html_byte_limit:
        detail_limit = 0
        html = _render_html(
            report,
            papers,
            title=title,
            archive_url=archive_url,
            detail_limit=detail_limit,
            template_dir=template_dir,
        )
    if len(html.encode()) > config.html_byte_limit:
        raise ValueError("紧凑邮件仍超过 HTML 大小上限")
    plain = _render_plain_text(
        report,
        papers,
        title=title,
        archive_url=archive_url,
        detail_limit=detail_limit,
    )
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.sender
    message["To"] = settings.recipient
    message["Date"] = format_datetime(datetime.now(UTC))
    domain = settings.sender.rsplit("@", 1)[-1]
    message["Message-ID"] = (
        f"<auto-research-daily-{report.generated_at:%Y%m%d}-{fingerprint[:16]}@{domain}>"
    )
    message.set_content(plain)
    message.add_alternative(html, subtype="html")
    return message


def build_test_message(settings: MailSettings) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = "【测试】具身智能科研日报邮件通道已接通"
    message["From"] = settings.sender
    message["To"] = settings.recipient
    message["Date"] = format_datetime(datetime.now(UTC))
    message.set_content(
        "QQ/Foxmail 邮件发送通道已经成功连接。正式日报将在网页部署成功后发送。\n"
    )
    message.add_alternative(
        "<p><strong>QQ/Foxmail 邮件发送通道已经成功连接。</strong></p>"
        "<p>正式日报将在网页部署成功后发送。</p>",
        subtype="html",
    )
    return message


def send_message(message: EmailMessage, settings: MailSettings) -> None:
    context = ssl.create_default_context()
    server = smtplib.SMTP_SSL(
        settings.host,
        settings.port,
        timeout=30,
        context=context,
    )
    delivered = False
    try:
        server.login(settings.username, settings.auth_code)
        server.send_message(message)
        delivered = True
        try:
            server.quit()
        except (OSError, smtplib.SMTPException):
            # DATA 已成功后，QUIT 失败不代表邮件未被服务器接受；此时重试会双发。
            server.close()
    except Exception:
        server.close()
        raise
    finally:
        if not delivered:
            server.close()


def notify_report(
    report: RunReport,
    *,
    settings: MailSettings,
    config: MailConfig,
    title: str,
    template_dir: Path,
    state_path: Path,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    state = load_json(state_path, {"schema_version": 1, "editions": {}})
    if not isinstance(state, dict):
        raise ValueError("邮件状态必须是 JSON 对象")
    editions = state.setdefault("editions", {})
    if not isinstance(editions, dict):
        raise ValueError("邮件状态 editions 必须是 JSON 对象")
    date_name = report.generated_at.strftime("%Y-%m-%d")
    existing = editions.get(date_name)
    if existing and not force:
        return {"status": "skipped", "reason": "date_already_sent", "date": date_name}
    papers = tuple(report.papers) if force else select_new_papers(report, state)
    if not papers and not config.send_empty:
        return {"status": "skipped", "reason": "no_new_papers", "date": date_name}
    fingerprint = edition_fingerprint(report, papers, config.template_version)
    message = build_report_message(
        report,
        papers,
        settings=settings,
        config=config,
        title=title,
        template_dir=template_dir,
        fingerprint=fingerprint,
        revision=bool(existing),
    )
    if dry_run:
        return {
            "status": "dry_run",
            "date": date_name,
            "paper_count": len(papers),
            "message_bytes": len(message.as_bytes()),
            "fingerprint": fingerprint,
        }
    send_message(message, settings)
    identities = [item.ranked.paper.identity for item in papers]
    editions[date_name] = {
        "fingerprint": fingerprint,
        "message_id": str(message["Message-ID"]),
        "sent_at": datetime.now(UTC).isoformat(),
        "paper_identities": identities,
    }
    atomic_write_json(state_path, state)
    return {
        "status": "sent",
        "date": date_name,
        "paper_count": len(papers),
        "message_bytes": len(message.as_bytes()),
        "fingerprint": fingerprint,
    }
