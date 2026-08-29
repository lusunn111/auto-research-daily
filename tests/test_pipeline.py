from datetime import UTC, datetime
from pathlib import Path

from auto_research_daily.config import load_config
from auto_research_daily.pipeline import RunOptions, run_daily

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
