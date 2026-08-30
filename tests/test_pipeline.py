from datetime import UTC, datetime
from pathlib import Path

from auto_research_daily.config import load_config
from auto_research_daily.models import FigureAsset, FigureGallery, FigurePanel, RunReport
from auto_research_daily.pipeline import RunOptions, run_daily
from auto_research_daily.reporting import render_html

ROOT = Path(__file__).parents[1]


def test_offline_pipeline_persists_and_reuses_cache(tmp_path: Path) -> None:
    config = load_config(ROOT / "config/research.yaml")
    output = config.output.model_copy(
        update={
            "data_dir": tmp_path / "data",
            "reports_dir": tmp_path / "reports",
            "site_dir": tmp_path / "site",
        }
    )
    config = config.model_copy(update={"output": output})
    options = RunOptions(
        project_root=ROOT,
        no_llm=True,
        offline_fixture=ROOT / "tests/fixtures/offline_daily.json",
        now=datetime(2026, 8, 29, tzinfo=UTC),
        deep_limit=2,
    )

    first = run_daily(config, options)
    second = run_daily(config, options)

    assert first.stats.fetched == 3
    assert first.generated_at.utcoffset().total_seconds() == 8 * 3600
    assert first.stats.full_text_reads == 2
    assert first.stats.published >= 1
    assert (tmp_path / "data/latest.json").is_file()
    assert (tmp_path / "reports/2026-08-29.md").is_file()
    assert "Setting（研究设定）" in (tmp_path / "reports/2026-08-29.md").read_text()
    assert (tmp_path / "site/index.html").is_file()
    assert (tmp_path / "site/archive/2026-08-29.html").is_file()
    assert "2026-08-29" in (tmp_path / "site/archive/index.html").read_text()
    assert second.stats.model_calls == 0
    assert second.stats.cache_hits == second.stats.preselected

    first_paper = first.papers[0]
    identity = first_paper.ranked.paper.canonical_id
    version = first_paper.ranked.paper.version
    figure = FigureAsset(
        number=1,
        label="Figure 1",
        caption="System overview.",
        panels=(
            FigurePanel(
                original_url=f"https://arxiv.org/html/{identity}v{version}/overview.png",
                cached_path=(
                    f"figures/arxiv/{identity}/v{version}/fig1-panel1.png"
                ),
            ),
        ),
        source_url=f"https://arxiv.org/html/{identity}v{version}#S1.F1",
    )
    gallery = FigureGallery(
        status="available",
        html_url=f"https://arxiv.org/html/{identity}v{version}",
        checked_at=first.generated_at,
        figures=(figure,),
    )
    paper_payload = first_paper.model_dump(mode="python")
    paper_payload["figure_gallery"] = gallery
    report_payload = first.model_dump(mode="python")
    report_payload["papers"] = (
        first_paper.model_validate({**paper_payload}),
        *first.papers[1:],
    )
    html = render_html(
        RunReport.model_validate(report_payload),
        config.output.title,
        ROOT / "src/auto_research_daily/templates",
        "https://example.github.io/auto-research-daily/",
    )
    assert "loading=\"lazy\"" in html
    assert (
        "https://example.github.io/auto-research-daily/figures/arxiv/" in html
    )
    assert "来源：论文官方 arXiv HTML" in html


def test_dry_run_does_not_write(tmp_path: Path) -> None:
    config = load_config(ROOT / "config/research.yaml")
    output = config.output.model_copy(
        update={
            "data_dir": tmp_path / "data",
            "reports_dir": tmp_path / "reports",
            "site_dir": tmp_path / "site",
        }
    )
    report = run_daily(
        config.model_copy(update={"output": output}),
        RunOptions(
            project_root=ROOT,
            dry_run=True,
            no_llm=True,
            offline_fixture=ROOT / "tests/fixtures/offline_daily.json",
            now=datetime(2026, 8, 29, tzinfo=UTC),
        ),
    )
    assert report.dry_run is True
    assert not (tmp_path / "data").exists()
