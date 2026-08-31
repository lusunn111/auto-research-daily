from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from html import escape
from pathlib import Path
from urllib.parse import urljoin

from jinja2 import Environment, FileSystemLoader, select_autoescape

from auto_research_daily.models import AnalyzedPaper, RunReport
from auto_research_daily.storage import (
    atomic_write_text,
    load_archive_papers,
    load_daily_reports,
)
from auto_research_daily.taxonomy import (
    RESEARCH_TAGS,
    RESEARCH_TOPICS,
    classify_paper,
    classify_tags,
    paper_search_text,
)

TIER_LABELS = {
    "deep_read": "今日必读",
    "browse": "值得浏览",
    "explore": "探索发现",
}


def _markdown_paper(index: int, item: AnalyzedPaper) -> str:
    paper = item.ranked.paper
    analysis = item.analysis
    scope = "全文级解读" if item.provenance.reading_scope.value == "full_text" else "摘要级解读"
    evidence = "\n".join(
        f'  - {entry.claim}："{entry.quote}"（{entry.location}）' for entry in analysis.evidence
    )
    method = "\n".join(f"  - {value}" for value in analysis.method)
    challenges = "\n".join(f"  - {value}" for value in analysis.challenges)
    experiments = "\n".join(f"  - {value}" for value in analysis.experiments)
    limitations = "\n".join(f"  - {value}" for value in analysis.limitations)
    figure_links = "；".join(
        f"[{figure.label}]({figure.source_url})"
        for figure in (item.figure_gallery.figures if item.figure_gallery else ())
    )
    figures = f"<br>论文原图：{figure_links}" if figure_links else ""
    return f"""### {index}. {analysis.title_zh}

原题：[{paper.title}]({paper.url})<br>
层级：{TIER_LABELS[item.tier]}；{scope}；综合分 {item.final_score:.3f}；\
相关性 {analysis.relevance_score}/10<br>
作者：{", ".join(paper.authors)}<br>
第一单位：{analysis.first_affiliation}<br>
通讯作者：{", ".join(analysis.corresponding_authors)}<br>
首次上传：{paper.published_at:%Y-%m}；版本：v{paper.version}；\
分类：{", ".join(paper.categories)}{figures}

Setting（研究设定）：{analysis.setting}

Motivation（研究动机）：{analysis.motivation}

Insight（核心洞察）：{analysis.insight}

Challenge（技术挑战）：
{challenges}

Analyze（问题分析）：{analysis.analysis}

Method（方法）：
{method}

Experiments（实验）：
{experiments}

Limitations（局限）：
{limitations}

与当前研究的关系：{analysis.relation_to_research}

推荐理由：{analysis.why_recommended}

不确定性：{analysis.uncertainty}

证据：
{evidence}
"""


def render_markdown(report: RunReport, title: str) -> str:
    tier_counts = Counter(paper.tier for paper in report.papers)
    lines = [
        f"# {title}：{report.generated_at:%Y-%m-%d}",
        "",
        "本报告先以主题、个人 Zotero 语料、时效性和探索性排序，再对少量高排名论文读取全文。",
        "“摘要级解读”不应被当作全文结论；证据不足的作者机构、实验或局限会明确标注。",
        "",
        (
            "| 今日必读 | 值得浏览 | 探索发现 | 抓取 | 初筛 | 模型调用 | "
            "缓存命中 | 输入令牌 | 输出令牌 |"
        ),
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        (
            f"| {tier_counts['deep_read']} | {tier_counts['browse']} | "
            f"{tier_counts['explore']} | {report.stats.fetched} | {report.stats.preselected} | "
            f"{report.stats.model_calls} | {report.stats.cache_hits} | "
            f"{report.stats.input_tokens} | {report.stats.output_tokens} |"
        ),
        "",
    ]
    for tier in ("deep_read", "browse", "explore"):
        entries = [paper for paper in report.papers if paper.tier == tier]
        if not entries:
            continue
        lines.extend((f"## {TIER_LABELS[tier]}", ""))
        for index, item in enumerate(entries, start=1):
            lines.extend((_markdown_paper(index, item), ""))
    return "\n".join(lines).rstrip() + "\n"


def _environment(template_dir: Path) -> Environment:
    return Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=select_autoescape(("html", "xml", "j2")),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _safe_component(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9._-]+", value):
        return value

    readable = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")[:40] or "paper"
    return f"{readable}-{hashlib.sha256(value.encode()).hexdigest()[:10]}"


def _paper_route(item: AnalyzedPaper) -> str:
    paper = item.ranked.paper
    return f"papers/{_safe_component(paper.source)}/{_safe_component(paper.canonical_id)}/"


def _build_explorer(
    papers: list[AnalyzedPaper] | tuple[AnalyzedPaper, ...],
    site_url: str,
) -> dict[str, object]:
    views = []
    filter_records = []
    topic_counts: Counter[str] = Counter()
    tag_counts: Counter[str] = Counter()
    for item in papers:
        topic = classify_paper(item)
        stable_tags = classify_tags(item)
        identity = item.ranked.paper.identity
        has_figure = bool(
            item.figure_gallery
            and item.figure_gallery.status == "available"
            and item.figure_gallery.figures
        )
        tag_views = [
            {
                "key": tag.key,
                "label": tag.label,
                "url": urljoin(site_url, f"tags/{tag.key}/"),
            }
            for tag in stable_tags
        ]
        view = {
            "id": identity,
            "item": item,
            "topic": topic,
            "tags": tag_views,
            "has_figure": has_figure,
            "detail_url": urljoin(site_url, _paper_route(item)),
        }
        views.append(view)
        filter_records.append(
            {
                "id": identity,
                "topic": topic.key,
                "tier": item.tier,
                "scope": item.provenance.reading_scope.value,
                "score": item.analysis.relevance_score,
                "hasFigure": has_figure,
                "tags": [tag.key for tag in stable_tags],
                "searchText": paper_search_text(item, topic),
            }
        )
        topic_counts[topic.key] += 1
        tag_counts.update(tag.key for tag in stable_tags)

    grouped = [
        (tier, TIER_LABELS[tier], [view for view in views if view["item"].tier == tier])
        for tier in ("deep_read", "browse", "explore")
    ]
    topics = [
        {"topic": topic, "count": topic_counts[topic.key]}
        for topic in RESEARCH_TOPICS
        if topic_counts[topic.key]
    ]
    filter_json = (
        json.dumps(filter_records, ensure_ascii=False, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    return {
        "views": views,
        "grouped": grouped,
        "topics": topics,
        "topic_counts": topic_counts,
        "tag_counts": tag_counts,
        "filter_json": filter_json,
    }


def _render_page(
    report: RunReport,
    *,
    title: str,
    template_dir: Path,
    site_url: str,
    papers: list[AnalyzedPaper] | tuple[AnalyzedPaper, ...] = (),
    archive_papers: list[AnalyzedPaper] | tuple[AnalyzedPaper, ...] = (),
    page_kind: str = "home",
    page_title: str | None = None,
    page_lede: str | None = None,
    gallery_title: str | None = None,
    gallery_description: str | None = None,
    directory_cards: list[dict[str, object]] | None = None,
    compact_cards: bool = False,
    expanded: bool = False,
) -> str:
    normalized_site_url = site_url.rstrip("/") + "/"
    explorer = _build_explorer(papers, normalized_site_url)
    archive_explorer = _build_explorer(archive_papers, normalized_site_url)
    archive_topic_counts = archive_explorer["topic_counts"]
    archive_tag_counts = archive_explorer["tag_counts"]
    portal_topics = [
        {
            "label": topic.label,
            "description": topic.description,
            "count": archive_topic_counts[topic.key],
            "url": urljoin(normalized_site_url, f"topics/{topic.key}/"),
        }
        for topic in RESEARCH_TOPICS
        if archive_topic_counts[topic.key]
    ]
    portal_tags = [
        {
            "label": tag.label,
            "description": tag.description,
            "count": archive_tag_counts[tag.key],
            "url": urljoin(normalized_site_url, f"tags/{tag.key}/"),
        }
        for tag in RESEARCH_TAGS
        if archive_tag_counts[tag.key]
    ]
    detail_view = explorer["views"][0] if page_kind == "detail" and papers else None
    return (
        _environment(template_dir)
        .get_template("index.html.j2")
        .render(
            report=report,
            title=title,
            page_kind=page_kind,
            page_title=page_title or title,
            page_lede=page_lede,
            gallery_title=gallery_title,
            gallery_description=gallery_description,
            paper_count=len(papers),
            archive_count=len(archive_papers),
            archive_topic_count=len(portal_topics),
            archive_tag_count=len(portal_tags),
            archive_full_text=sum(
                item.provenance.reading_scope.value == "full_text" for item in archive_papers
            ),
            archive_figure_count=sum(
                bool(item.figure_gallery and item.figure_gallery.status == "available")
                for item in archive_papers
            ),
            portal_topics=portal_topics,
            portal_tags=portal_tags,
            directory_cards=directory_cards or [],
            compact_cards=compact_cards,
            expanded=expanded,
            detail_view=detail_view,
            site_url=normalized_site_url,
            home_url=normalized_site_url,
            daily_index_url=urljoin(normalized_site_url, "daily/"),
            latest_daily_url=urljoin(normalized_site_url, f"daily/{report.generated_at:%Y-%m-%d}/"),
            library_url=urljoin(normalized_site_url, "library/"),
            topics_index_url=urljoin(normalized_site_url, "topics/"),
            tags_index_url=urljoin(normalized_site_url, "tags/"),
            archive_index_url=urljoin(normalized_site_url, "archive/"),
            search_url=urljoin(normalized_site_url, "search/"),
            feed_url=urljoin(normalized_site_url, "feed.xml"),
            **explorer,
        )
    )


def render_html(report: RunReport, title: str, template_dir: Path, site_url: str) -> str:
    return _render_page(
        report,
        title=title,
        template_dir=template_dir,
        site_url=site_url,
        papers=report.papers,
        archive_papers=report.papers,
        page_kind="home",
        page_title=title,
        page_lede=(
            "今天的论文发现与完整解读位于本页；长期论文库、稳定主题、标签、"
            "月份归档和单篇详情由独立永久页面维护。"
        ),
        gallery_title="最新一期日报",
        gallery_description="本区只展示本次运行入选论文，不混入历史档案。",
    )


def render_rss(report: RunReport, title: str, site_url: str) -> str:
    items = []
    normalized_site_url = site_url.rstrip("/") + "/"
    for analyzed in report.papers[:20]:
        paper = analyzed.ranked.paper
        analysis = analyzed.analysis
        detail_url = urljoin(normalized_site_url, _paper_route(analyzed))
        description = escape(
            f"{TIER_LABELS[analyzed.tier]}｜{analysis.why_recommended}｜{analysis.insight}"
        )
        items.append(
            "<item>"
            f"<title>{escape(analysis.title_zh)}</title>"
            f"<link>{escape(detail_url)}</link>"
            f'<guid isPermaLink="false">{escape(paper.identity)}</guid>'
            f"<description>{description}</description>"
            f"<pubDate>{paper.updated_at:%a, %d %b %Y %H:%M:%S %z}</pubDate>"
            "</item>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0"><channel>'
        f"<title>{escape(title)}</title><link>{escape(normalized_site_url)}</link>"
        f"<description>{escape(title)}的每日论文解读</description>"
        + "".join(items)
        + "</channel></rss>\n"
    )


def write_report_artifacts(
    report: RunReport,
    *,
    title: str,
    reports_dir: Path,
    data_dir: Path,
    site_dir: Path,
    template_dir: Path,
    site_url: str,
) -> None:
    date_name = report.generated_at.strftime("%Y-%m-%d")
    markdown = render_markdown(report, title)
    archive_papers = load_archive_papers(data_dir, report)
    daily_reports = load_daily_reports(data_dir, report)
    html = _render_page(
        report,
        title=title,
        template_dir=template_dir,
        site_url=site_url,
        papers=report.papers,
        archive_papers=archive_papers,
        page_kind="home",
        page_title=title,
        page_lede=(
            "今天的论文发现与完整解读位于本页；长期论文库、稳定主题、标签、"
            "月份归档和单篇详情由独立永久页面维护。"
        ),
        gallery_title="最新一期日报",
        gallery_description="本区只展示本次运行入选论文，不混入历史档案。",
    )
    atomic_write_text(reports_dir / f"{date_name}.md", markdown)
    atomic_write_text(site_dir / "index.html", html)
    for daily in daily_reports:
        daily_name = daily.generated_at.strftime("%Y-%m-%d")
        daily_html = _render_page(
            daily,
            title=title,
            template_dir=template_dir,
            site_url=site_url,
            papers=daily.papers,
            archive_papers=archive_papers,
            page_kind="collection",
            page_title=f"{daily_name} 科研日报",
            page_lede="保留该次运行的论文顺序、编辑层级、分析范围与结构化解读。",
            gallery_title="本期全部论文",
            gallery_description="这是按日期保存的每日日报快照，不混入长期论文库。",
        )
        atomic_write_text(site_dir / "daily" / daily_name / "index.html", daily_html)
        # Keep the original flat route working for links sent by older emails.
        atomic_write_text(site_dir / "archive" / f"{daily_name}.html", daily_html)

    daily_cards = [
        {
            "label": f"{daily.generated_at:%Y-%m-%d} 科研日报",
            "count": len(daily.papers),
            "description": (
                f"抓取 {daily.stats.fetched} 篇，全文阅读 {daily.stats.full_text_reads} 篇。"
            ),
            "url": urljoin(site_url.rstrip("/") + "/", f"daily/{daily.generated_at:%Y-%m-%d}/"),
        }
        for daily in daily_reports
    ]
    atomic_write_text(
        site_dir / "daily" / "index.html",
        _render_page(
            report,
            title=title,
            template_dir=template_dir,
            site_url=site_url,
            archive_papers=archive_papers,
            page_kind="directory",
            page_title="每日日报",
            page_lede="按系统运行日期保存日报快照，与论文发表月份归档分开。",
            directory_cards=daily_cards,
        ),
    )

    atomic_write_text(
        site_dir / "library" / "index.html",
        _render_page(
            report,
            title=title,
            template_dir=template_dir,
            site_url=site_url,
            papers=archive_papers,
            archive_papers=archive_papers,
            page_kind="collection",
            page_title="长期论文库",
            page_lede="汇总全部月份归档，并为同一论文选择当前最高版本。",
            gallery_title="全部研究",
            gallery_description="可按主题、标签、层级、证据范围和相关性组合筛选。",
        ),
    )
    atomic_write_text(
        site_dir / "search" / "index.html",
        _render_page(
            report,
            title=title,
            template_dir=template_dir,
            site_url=site_url,
            papers=archive_papers,
            archive_papers=archive_papers,
            page_kind="collection",
            page_title="论文档案搜索",
            page_lede="搜索论文元数据与结构化研究笔记，不宣称搜索论文 PDF 全文。",
            gallery_title="搜索长期论文库",
            gallery_description="输入题目、作者、方法、实验或标签关键词。",
        ),
    )

    archive_explorer = _build_explorer(archive_papers, site_url.rstrip("/") + "/")
    topic_counts = archive_explorer["topic_counts"]
    topic_cards = []
    for topic in RESEARCH_TOPICS:
        topic_papers = [item for item in archive_papers if classify_paper(item).key == topic.key]
        topic_url = urljoin(site_url.rstrip("/") + "/", f"topics/{topic.key}/")
        topic_cards.append(
            {
                "label": topic.label,
                "count": topic_counts[topic.key],
                "description": topic.description,
                "url": topic_url,
            }
        )
        atomic_write_text(
            site_dir / "topics" / topic.key / "index.html",
            _render_page(
                report,
                title=title,
                template_dir=template_dir,
                site_url=site_url,
                papers=topic_papers,
                archive_papers=archive_papers,
                page_kind="collection",
                page_title=topic.label,
                page_lede=topic.description,
                gallery_title=f"{topic.label}长期档案",
                gallery_description="来自全部月份归档的当前首选版本。",
            ),
        )
    atomic_write_text(
        site_dir / "topics" / "index.html",
        _render_page(
            report,
            title=title,
            template_dir=template_dir,
            site_url=site_url,
            archive_papers=archive_papers,
            page_kind="directory",
            page_title="研究主题",
            page_lede="稳定主题轴拥有永久网址；模型生成的细粒度主题只作为解读元数据。",
            directory_cards=topic_cards,
        ),
    )

    tag_counts = archive_explorer["tag_counts"]
    tag_cards = []
    for tag in RESEARCH_TAGS:
        tag_papers = [
            item
            for item in archive_papers
            if tag.key in {value.key for value in classify_tags(item)}
        ]
        tag_url = urljoin(site_url.rstrip("/") + "/", f"tags/{tag.key}/")
        tag_cards.append(
            {
                "label": tag.label,
                "count": tag_counts[tag.key],
                "description": tag.description,
                "url": tag_url,
            }
        )
        atomic_write_text(
            site_dir / "tags" / tag.key / "index.html",
            _render_page(
                report,
                title=title,
                template_dir=template_dir,
                site_url=site_url,
                papers=tag_papers,
                archive_papers=archive_papers,
                page_kind="collection",
                page_title=tag.label,
                page_lede=tag.description,
                gallery_title=f"{tag.label}标签档案",
                gallery_description="标签由固定别名表归一化，不直接使用模型自由文本生成网址。",
            ),
        )
    atomic_write_text(
        site_dir / "tags" / "index.html",
        _render_page(
            report,
            title=title,
            template_dir=template_dir,
            site_url=site_url,
            archive_papers=archive_papers,
            page_kind="directory",
            page_title="研究标签",
            page_lede="固定标签覆盖方法、任务、数据、评测与系统属性，并拥有永久网址。",
            directory_cards=tag_cards,
        ),
    )

    months: dict[str, list[AnalyzedPaper]] = {}
    for item in archive_papers:
        months.setdefault(item.ranked.paper.published_at.strftime("%Y-%m"), []).append(item)
    month_cards = []
    for month, month_papers in sorted(months.items(), reverse=True):
        month_url = urljoin(site_url.rstrip("/") + "/", f"archive/{month}/")
        month_cards.append(
            {
                "label": f"{month} 论文归档",
                "count": len(month_papers),
                "description": "按 arXiv 首次发表月份整理的当前版本论文。",
                "url": month_url,
            }
        )
        atomic_write_text(
            site_dir / "archive" / month / "index.html",
            _render_page(
                report,
                title=title,
                template_dir=template_dir,
                site_url=site_url,
                papers=month_papers,
                archive_papers=archive_papers,
                page_kind="collection",
                page_title=f"{month} 论文归档",
                page_lede="按论文首次发表时间归档，与每日日报运行日期分开。",
                gallery_title=f"{month} 全部论文",
                gallery_description="同一论文仅展示当前首选版本。",
                compact_cards=True,
            ),
        )
    atomic_write_text(
        site_dir / "archive" / "index.html",
        _render_page(
            report,
            title=title,
            template_dir=template_dir,
            site_url=site_url,
            archive_papers=archive_papers,
            page_kind="directory",
            page_title="论文发表归档",
            page_lede="按论文首次发表月份组织长期论文库，不等同于每日日报历史。",
            directory_cards=month_cards,
        ),
    )

    for item in archive_papers:
        atomic_write_text(
            site_dir / _paper_route(item) / "index.html",
            _render_page(
                report,
                title=title,
                template_dir=template_dir,
                site_url=site_url,
                papers=[item],
                archive_papers=archive_papers,
                page_kind="detail",
                page_title=item.analysis.title_zh,
                page_lede=item.ranked.paper.title,
                expanded=True,
            ),
        )
    atomic_write_text(site_dir / "feed.xml", render_rss(report, title, site_url))
