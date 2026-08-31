from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from auto_research_daily.models import AnalyzedPaper, RunReport

ARCHIVE_NAME_PATTERN = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])\.json$")
DAILY_NAME_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}\.json$")


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        temporary_path.replace(path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        temporary_path.replace(path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def persist_report(report: RunReport, data_dir: Path) -> None:
    payload = report.model_dump(mode="json")
    month = report.generated_at.strftime("%Y-%m")
    archive_path = data_dir / "archive" / f"{month}.json"
    archive = load_json(archive_path, {"schema_version": 1, "papers": {}})
    papers = archive.setdefault("papers", {})
    for analyzed in report.papers:
        papers[analyzed.ranked.paper.identity] = analyzed.model_dump(mode="json")
    archive["updated_at"] = report.generated_at.isoformat()
    atomic_write_json(archive_path, archive)
    atomic_write_json(
        data_dir / "daily" / f"{report.generated_at:%Y-%m-%d}.json",
        report.model_dump(mode="json"),
    )
    # latest.json is the commit point: write it only after the archive succeeds.
    atomic_write_json(data_dir / "latest.json", payload)


def _preferred_paper(candidate: AnalyzedPaper, current: AnalyzedPaper) -> bool:
    candidate_paper = candidate.ranked.paper
    current_paper = current.ranked.paper
    return (
        candidate_paper.version,
        candidate_paper.updated_at,
        candidate.provenance.analyzed_at,
        candidate.final_score,
        candidate_paper.identity,
    ) > (
        current_paper.version,
        current_paper.updated_at,
        current.provenance.analyzed_at,
        current.final_score,
        current_paper.identity,
    )


def select_current_papers(papers: list[AnalyzedPaper]) -> list[AnalyzedPaper]:
    selected: dict[tuple[str, str], AnalyzedPaper] = {}
    for item in papers:
        paper = item.ranked.paper
        key = (paper.source, paper.canonical_id)
        current = selected.get(key)
        if current is None or _preferred_paper(item, current):
            selected[key] = item
    return sorted(
        selected.values(),
        key=lambda item: (
            -item.ranked.paper.published_at.timestamp(),
            -item.analysis.relevance_score,
            item.ranked.paper.canonical_id,
        ),
    )


def load_archive_papers(
    data_dir: Path,
    current_report: RunReport | None = None,
) -> list[AnalyzedPaper]:
    papers: list[AnalyzedPaper] = []
    archive_dir = data_dir / "archive"
    if archive_dir.exists():
        for path in sorted(archive_dir.glob("*.json")):
            if ARCHIVE_NAME_PATTERN.fullmatch(path.name) is None:
                raise ValueError(f"invalid archive filename: {path.name}")
            payload = load_json(path, {})
            entries = payload.get("papers") if isinstance(payload, dict) else None
            if not isinstance(entries, dict):
                raise ValueError(f"archive must contain a papers object: {path.name}")
            papers.extend(AnalyzedPaper.model_validate(value) for value in entries.values())
    if current_report is not None:
        papers.extend(current_report.papers)
    return select_current_papers(papers)


def load_daily_reports(
    data_dir: Path,
    current_report: RunReport | None = None,
) -> list[RunReport]:
    reports: dict[str, RunReport] = {}
    daily_dir = data_dir / "daily"
    if daily_dir.exists():
        for path in sorted(daily_dir.glob("*.json")):
            if DAILY_NAME_PATTERN.fullmatch(path.name) is None:
                raise ValueError(f"invalid daily filename: {path.name}")
            report = RunReport.model_validate(load_json(path, {}))
            reports[path.stem] = report
    if current_report is not None:
        reports[current_report.generated_at.strftime("%Y-%m-%d")] = current_report
    return sorted(reports.values(), key=lambda report: report.generated_at, reverse=True)


def load_analysis_cache(data_dir: Path) -> dict[str, dict[str, Any]]:
    payload = load_json(data_dir / "cache" / "analyses.json", {})
    if not isinstance(payload, dict):
        raise ValueError("analysis cache must be a JSON object")
    return payload


def persist_analysis_cache(data_dir: Path, cache: dict[str, dict[str, Any]]) -> None:
    atomic_write_json(data_dir / "cache" / "analyses.json", cache)
