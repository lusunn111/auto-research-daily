from __future__ import annotations

from collections import Counter
from html import escape
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from auto_research_daily.models import AnalyzedPaper, RunReport
from auto_research_daily.storage import atomic_write_text

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
        f"  - {entry.claim}：\"{entry.quote}\"（{entry.location}）"
        for entry in analysis.evidence
    )
    method = "\n".join(f"  - {value}" for value in analysis.method)
    challenges = "\n".join(f"  - {value}" for value in analysis.challenges)
    experiments = "\n".join(f"  - {value}" for value in analysis.experiments)
    limitations = "\n".join(f"  - {value}" for value in analysis.limitations)
    return f"""### {index}. {analysis.title_zh}

原题：[{paper.title}]({paper.url})<br>
层级：{TIER_LABELS[item.tier]}；{scope}；综合分 {item.final_score:.3f}；\
相关性 {analysis.relevance_score}/10<br>
作者：{", ".join(paper.authors)}<br>
第一单位：{analysis.first_affiliation}<br>
通讯作者：{", ".join(analysis.corresponding_authors)}<br>
首次上传：{paper.published_at:%Y-%m}；版本：v{paper.version}；分类：{", ".join(paper.categories)}

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
        "| 今日必读 | 值得浏览 | 探索发现 | 抓取 | 初筛 | 模型调用 | 缓存命中 |",
        "|---:|---:|---:|---:|---:|---:|---:|",
        (
            f"| {tier_counts['deep_read']} | {tier_counts['browse']} | "
            f"{tier_counts['explore']} | {report.stats.fetched} | {report.stats.preselected} | "
            f"{report.stats.model_calls} | {report.stats.cache_hits} |"
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


def render_html(report: RunReport, title: str, template_dir: Path) -> str:
    environment = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=select_autoescape(("html", "xml")),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = environment.get_template("index.html.j2")
    grouped = [
        (tier, TIER_LABELS[tier], [paper for paper in report.papers if paper.tier == tier])
        for tier in ("deep_read", "browse", "explore")
    ]
    return template.render(report=report, title=title, grouped=grouped)


def render_rss(report: RunReport, title: str, site_url: str) -> str:
    items = []
    for analyzed in report.papers[:20]:
        paper = analyzed.ranked.paper
        analysis = analyzed.analysis
        description = escape(
            f"{TIER_LABELS[analyzed.tier]}｜{analysis.why_recommended}｜{analysis.insight}"
        )
        items.append(
            "<item>"
            f"<title>{escape(analysis.title_zh)}</title>"
            f"<link>{escape(paper.url)}</link>"
            f'<guid isPermaLink="false">{escape(paper.identity)}</guid>'
            f"<description>{description}</description>"
            f"<pubDate>{paper.updated_at:%a, %d %b %Y %H:%M:%S %z}</pubDate>"
            "</item>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0"><channel>'
        f"<title>{escape(title)}</title><link>{escape(site_url)}</link>"
        f"<description>{escape(title)}的每日论文解读</description>"
        + "".join(items)
        + "</channel></rss>\n"
    )


def render_archive_index(site_dir: Path, title: str) -> str:
    archive_dir = site_dir / "archive"
    entries = sorted(
        (path for path in archive_dir.glob("????-??-??.html")),
        key=lambda path: path.name,
        reverse=True,
    )
    links = "".join(
        f'<li><a href="{escape(path.name)}">{escape(path.stem)}</a></li>' for path in entries
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>历史日报 · {escape(title)}</title>
<style>
body{{max-width:760px;margin:10vh auto;padding:0 5vw;background:#f5f1e8;
color:#16211b;font:17px/1.8 system-ui,sans-serif}}
a{{color:#1f5c42}} h1{{font:700 3rem/1.1 Georgia,serif}} li{{margin:.6rem 0}}
</style>
</head>
<body>
<p><a href="../index.html">返回最新日报</a></p>
<h1>历史日报</h1><ul>{links}</ul>
</body>
</html>
"""


def write_report_artifacts(
    report: RunReport,
    *,
    title: str,
    reports_dir: Path,
    site_dir: Path,
    template_dir: Path,
    site_url: str,
) -> None:
    date_name = report.generated_at.strftime("%Y-%m-%d")
    markdown = render_markdown(report, title)
    html = render_html(report, title, template_dir)
    atomic_write_text(reports_dir / f"{date_name}.md", markdown)
    atomic_write_text(site_dir / "index.html", html)
    atomic_write_text(site_dir / "archive" / f"{date_name}.html", html)
    atomic_write_text(site_dir / "archive" / "index.html", render_archive_index(site_dir, title))
    atomic_write_text(site_dir / "feed.xml", render_rss(report, title, site_url))
