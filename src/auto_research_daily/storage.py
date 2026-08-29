from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from auto_research_daily.models import RunReport


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
    # latest.json is the commit point: write it only after the archive succeeds.
    atomic_write_json(data_dir / "latest.json", payload)


def load_analysis_cache(data_dir: Path) -> dict[str, dict[str, Any]]:
    payload = load_json(data_dir / "cache" / "analyses.json", {})
    if not isinstance(payload, dict):
        raise ValueError("analysis cache must be a JSON object")
    return payload


def persist_analysis_cache(data_dir: Path, cache: dict[str, dict[str, Any]]) -> None:
    atomic_write_json(data_dir / "cache" / "analyses.json", cache)
