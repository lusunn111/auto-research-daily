from __future__ import annotations

import hashlib
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter

from auto_research_daily.analysis import (
    HeuristicAnalyzer,
    OpenAICompatibleAnalyzer,
    analyze_ranked_papers,
    load_prompt,
)
from auto_research_daily.config import AppConfig
from auto_research_daily.fulltext import fetch_arxiv_html
from auto_research_daily.models import AnalyzedPaper, RawPaper, RunReport, RunStats, ZoteroDocument
from auto_research_daily.ranking import deduplicate_papers, rank_papers
from auto_research_daily.reporting import write_report_artifacts
from auto_research_daily.sources import ArxivSource, ZoteroSource
from auto_research_daily.storage import (
    load_analysis_cache,
    persist_analysis_cache,
    persist_report,
)

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunOptions:
    project_root: Path
    dry_run: bool = False
    no_llm: bool = False
    offline_fixture: Path | None = None
    lookback_days: int | None = None
    max_papers: int | None = None
    deep_limit: int | None = None
    now: datetime | None = None


def _resolve(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def _load_fixture(path: Path) -> tuple[list[RawPaper], list[ZoteroDocument], dict[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    papers = TypeAdapter(list[RawPaper]).validate_python(payload.get("papers", []))
    documents = TypeAdapter(list[ZoteroDocument]).validate_python(payload.get("zotero", []))
    full_texts = {str(key): str(value) for key, value in payload.get("full_texts", {}).items()}
    return papers, documents, full_texts


def _fetch_live_sources(
    config: AppConfig,
    options: RunOptions,
) -> tuple[list[RawPaper], list[ZoteroDocument]]:
    papers: list[RawPaper] = []
    documents: list[ZoteroDocument] = []
    if config.sources.arxiv.enabled:
        user_agent = os.getenv(
            "ARXIV_USER_AGENT",
            "auto-research-daily/0.1 (set ARXIV_USER_AGENT with a contact address)",
        )
        with ArxivSource(config.sources.arxiv, user_agent=user_agent) as source:
            papers.extend(
                source.fetch_recent(now=options.now, lookback_days=options.lookback_days)
            )

    if config.sources.zotero.enabled:
        user_id = os.getenv("ZOTERO_USER_ID")
        api_key = os.getenv("ZOTERO_API_KEY")
        if user_id and api_key:
            with ZoteroSource(
                config.sources.zotero,
                user_id=user_id,
                api_key=api_key,
            ) as source:
                documents.extend(source.fetch_documents())
        else:
            LOGGER.warning("Zotero 凭据未配置，本次只使用静态研究画像排序")
    return papers, documents


def _fetch_full_texts(
    ranked: list[Any],
    *,
    limit: int,
    max_chars: int,
) -> dict[str, str]:
    if limit <= 0:
        return {}
    full_texts: dict[str, str] = {}
    targets = ranked[:limit]
    with ThreadPoolExecutor(max_workers=min(4, len(targets) or 1)) as executor:
        futures = {
            executor.submit(fetch_arxiv_html, item.paper, max_chars=max_chars): item.paper.identity
            for item in targets
        }
        for future in as_completed(futures):
            identity = futures[future]
            try:
                text = future.result()
            except Exception as error:  # Full-text failure degrades to abstract analysis.
                LOGGER.warning("全文抓取失败 %s: %s", identity, error)
                continue
            if text:
                full_texts[identity] = text
    return full_texts


def _assign_tiers(papers: list[AnalyzedPaper]) -> list[AnalyzedPaper]:
    non_deep = [paper for paper in papers if paper.tier != "deep_read"]
    explore_quota = max(1, round(len(papers) * 0.15)) if papers else 0
    explore_ids = {
        item.ranked.paper.identity
        for item in sorted(
            non_deep,
            key=lambda value: (
                -value.ranked.score.exploration,
                value.ranked.score.topic,
                -value.final_score,
            ),
        )[:explore_quota]
        if item.ranked.score.exploration >= 0.70
    }
    updated: list[AnalyzedPaper] = []
    for paper in papers:
        tier = paper.tier
        if paper.ranked.paper.identity in explore_ids:
            tier = "explore"
        payload = paper.model_dump(mode="python")
        payload["tier"] = tier
        updated.append(AnalyzedPaper.model_validate(payload))
    tier_order = {"deep_read": 0, "browse": 1, "explore": 2}
    return sorted(updated, key=lambda item: (tier_order[item.tier], -item.final_score))


def run_daily(config: AppConfig, options: RunOptions) -> RunReport:
    root = options.project_root.resolve()
    generated_at = (options.now or datetime.now(UTC)).astimezone(
        ZoneInfo(config.output.timezone)
    )
    fixture_full_texts: dict[str, str] = {}
    if options.offline_fixture:
        papers, documents, fixture_full_texts = _load_fixture(options.offline_fixture)
    else:
        papers, documents = _fetch_live_sources(config, options)

    fetched_count = len(papers)
    papers = deduplicate_papers(papers)
    ranked = rank_papers(
        papers,
        profile=config.research_profile,
        config=config.ranking,
        documents=documents,
        now=generated_at,
    )
    max_analysis = options.max_papers or config.analysis.max_papers
    ranked = ranked[:max_analysis]

    deep_limit = (
        options.deep_limit
        if options.deep_limit is not None
        else config.analysis.full_text_top_k
    )
    full_texts = dict(fixture_full_texts)
    if not options.offline_fixture:
        full_texts.update(
            _fetch_full_texts(
                ranked,
                limit=deep_limit,
                max_chars=config.analysis.full_text_max_chars,
            )
        )
    allowed_identities = {item.paper.identity for item in ranked[:deep_limit]}
    full_texts = {
        identity: value for identity, value in full_texts.items() if identity in allowed_identities
    }

    data_dir = _resolve(root, config.output.data_dir)
    cache = load_analysis_cache(data_dir)
    prompt = load_prompt(root, config.analysis.prompt_version)
    profile_fingerprint = hashlib.sha256(
        config.research_profile.model_dump_json().encode()
    ).hexdigest()
    if options.no_llm:
        analyzer: OpenAICompatibleAnalyzer | HeuristicAnalyzer = HeuristicAnalyzer()
        results, cache, errors = analyze_ranked_papers(
            ranked,
            full_texts=full_texts,
            analyzer=analyzer,
            config=config.analysis,
            cache=cache,
            profile_fingerprint=profile_fingerprint,
        )
    else:
        api_key = os.getenv("LLM_API_KEY")
        if not api_key:
            raise RuntimeError("缺少 LLM_API_KEY；本地流程验证可显式使用 --no-llm")
        shared_model = os.getenv("LLM_MODEL")
        brief_model = (
            os.getenv("LLM_BRIEF_MODEL")
            or shared_model
            or config.analysis.brief_model_default
        )
        deep_model = (
            os.getenv("LLM_DEEP_MODEL")
            or shared_model
            or config.analysis.deep_model_default
        )
        base_url = os.getenv("LLM_BASE_URL") or config.analysis.base_url_default
        with OpenAICompatibleAnalyzer(
            config.analysis,
            config.research_profile,
            api_key=api_key,
            brief_model=brief_model,
            deep_model=deep_model,
            brief_reasoning_effort=config.analysis.brief_reasoning_effort,
            deep_reasoning_effort=config.analysis.deep_reasoning_effort,
            base_url=base_url,
            prompt=prompt,
        ) as analyzer:
            results, cache, errors = analyze_ranked_papers(
                ranked,
                full_texts=full_texts,
                analyzer=analyzer,
                config=config.analysis,
                cache=cache,
                profile_fingerprint=profile_fingerprint,
            )

    quality = [
        result.paper
        for result in results
        if result.paper.analysis.relevance_score >= config.ranking.min_llm_relevance
    ][: config.ranking.publish_limit]
    published = _assign_tiers(quality)
    cache_hits = sum(result.cache_hit for result in results)
    model_calls = len(results) - cache_hits + len(errors)
    input_tokens, output_tokens = analyzer.usage()
    report = RunReport(
        generated_at=generated_at,
        dry_run=options.dry_run,
        stats=RunStats(
            fetched=fetched_count,
            deduplicated=len(papers),
            preselected=len(ranked),
            cache_hits=cache_hits,
            model_calls=model_calls,
            full_text_reads=len(full_texts),
            published=len(published),
            failed=len(errors),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        ),
        papers=tuple(published),
    )

    if not options.dry_run:
        reports_dir = _resolve(root, config.output.reports_dir)
        site_dir = _resolve(root, config.output.site_dir)
        write_report_artifacts(
            report,
            title=config.output.title,
            reports_dir=reports_dir,
            site_dir=site_dir,
            template_dir=root / "src" / "auto_research_daily" / "templates",
            site_url=(
                os.getenv("SITE_URL") or "https://example.github.io/auto-research-daily/"
            ),
        )
        persist_analysis_cache(data_dir, cache)
        persist_report(report, data_dir)
    return report
