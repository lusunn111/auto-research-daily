from __future__ import annotations

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from auto_research_daily.config import AnalysisConfig, ResearchProfileConfig
from auto_research_daily.models import (
    AnalyzedPaper,
    Evidence,
    PaperAnalysis,
    Provenance,
    RankedPaper,
    ReadingScope,
)


@dataclass(frozen=True)
class AnalysisResult:
    paper: AnalyzedPaper
    cache_hit: bool


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _content_payload(ranked: RankedPaper, full_text: str | None) -> dict[str, Any]:
    paper = ranked.paper
    return {
        "identity": paper.identity,
        "title": paper.title,
        "authors": paper.authors,
        "categories": paper.categories,
        "abstract": paper.abstract,
        "full_text": full_text,
    }


def make_input_hash(ranked: RankedPaper, full_text: str | None) -> str:
    return hashlib.sha256(_stable_json(_content_payload(ranked, full_text)).encode()).hexdigest()


def make_cache_key(
    ranked: RankedPaper,
    *,
    full_text: str | None,
    model: str,
    prompt_version: str,
    profile_fingerprint: str = "",
) -> str:
    payload = {
        "identity": ranked.paper.identity,
        "input_hash": make_input_hash(ranked, full_text),
        "reading_scope": "full_text" if full_text else "abstract",
        "model": model,
        "prompt_version": prompt_version,
        "profile_fingerprint": profile_fingerprint,
        "schema_version": 1,
    }
    return hashlib.sha256(_stable_json(payload).encode()).hexdigest()


class OpenAICompatibleAnalyzer:
    def __init__(
        self,
        config: AnalysisConfig,
        profile: ResearchProfileConfig,
        *,
        api_key: str,
        model: str,
        base_url: str,
        prompt: str,
        client: httpx.Client | None = None,
    ) -> None:
        self.config = config
        self.profile = profile
        self.model = model
        self.prompt = prompt
        self._owns_client = client is None
        self.client = client or httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=120,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> OpenAICompatibleAnalyzer:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _user_message(self, ranked: RankedPaper, full_text: str | None) -> str:
        paper = ranked.paper
        evidence_label = "全文节选" if full_text else "标题与摘要"
        return (
            f"研究画像：{self.profile.description}\n"
            f"证据范围：{evidence_label}\n"
            f"论文标识：{paper.identity}\n"
            f"标题：{paper.title}\n"
            f"作者：{', '.join(paper.authors)}\n"
            f"分类：{', '.join(paper.categories)}\n"
            f"摘要：{paper.abstract}\n"
            f"全文节选：{full_text or '未提供，只允许基于摘要分析。'}"
        )

    @staticmethod
    def _extract_json(value: str) -> str:
        stripped = value.strip()
        if stripped.startswith("```"):
            stripped = stripped.removeprefix("```json").removeprefix("```")
            stripped = stripped.removesuffix("```").strip()
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end < start:
            raise ValueError("model response does not contain a JSON object")
        return stripped[start : end + 1]

    @staticmethod
    def _validate_grounding(
        analysis: PaperAnalysis,
        ranked: RankedPaper,
        full_text: str | None,
    ) -> None:
        evidence_text = " ".join(
            f"{ranked.paper.abstract} {full_text or ''}".casefold().split()
        )
        for evidence in analysis.evidence:
            quote = " ".join(evidence.quote.casefold().split())
            if quote not in evidence_text:
                raise ValueError("model evidence quote is not present in supplied material")
            if full_text is None and evidence.location != "abstract":
                raise ValueError("abstract-only analysis cannot cite supplied_full_text")

    def analyze(self, ranked: RankedPaper, full_text: str | None) -> PaperAnalysis:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = self.client.post(
                    "/chat/completions",
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": self.prompt},
                            {"role": "user", "content": self._user_message(ranked, full_text)},
                        ],
                        "temperature": 0.1,
                        "max_tokens": self.config.max_output_tokens,
                        "response_format": {"type": "json_object"},
                    },
                )
                response.raise_for_status()
                payload = response.json()
                content = payload["choices"][0]["message"]["content"]
                analysis = PaperAnalysis.model_validate_json(self._extract_json(str(content)))
                self._validate_grounding(analysis, ranked, full_text)
                return analysis
            except (httpx.HTTPError, KeyError, IndexError, ValueError, ValidationError) as error:
                last_error = error
                if attempt < 2:
                    time.sleep(2**attempt)
        raise RuntimeError(f"analysis failed for {ranked.paper.identity}") from last_error


class HeuristicAnalyzer:
    """Deterministic offline analyzer for tests and local smoke runs only."""

    model = "offline-heuristic"

    @staticmethod
    def analyze(ranked: RankedPaper, full_text: str | None) -> PaperAnalysis:
        paper = ranked.paper
        abstract = " ".join(paper.abstract.split())
        quote = abstract[: min(360, len(abstract))]
        scope_notice = "已提供全文节选" if full_text else "仅基于标题与摘要"
        matched = ranked.score.matched_terms
        return PaperAnalysis(
            title_zh=paper.title,
            first_affiliation="证据未提供",
            corresponding_authors=("证据未提供",),
            relevance_score=max(1, min(10, round(ranked.score.base_score * 9 + 1))),
            primary_topic=matched[0] if matched else paper.categories[0],
            tags=tuple(matched[:8]) or paper.categories[:8],
            setting=f"{scope_notice}；研究对象与任务设定请以原文为准。",
            motivation=f"论文摘要提出的问题是：{quote[:180]}",
            insight="离线模式不推断摘要以外的核心洞见，需接入大模型后生成忠实解读。",
            challenges=("摘要能够确认的问题边界有限，完整挑战需查阅正文。",),
            analysis="离线模式只验证端到端工作流，不作为可发布的科研解读。",
            method=("摘要所述方法需要结合正文核验。",),
            experiments=("摘要未提供足够实验细节，无法可靠复述。",),
            limitations=("离线启发式解读未调用大模型，仅用于持续集成。",),
            relation_to_research=(
                f"基础相关性得分为 {ranked.score.base_score:.3f}；命中术语："
                f"{', '.join(matched) if matched else '无明确术语命中'}。"
            ),
            why_recommended="由主题、个人语料相似度、时效性和探索性联合排序入选。",
            uncertainty="此条目由离线模式生成，不应替代正式模型分析。",
            evidence=(Evidence(claim="摘要描述", quote=quote, location="abstract"),),
        )


def analyze_ranked_papers(
    ranked_papers: list[RankedPaper],
    *,
    full_texts: dict[str, str],
    analyzer: OpenAICompatibleAnalyzer | HeuristicAnalyzer,
    config: AnalysisConfig,
    cache: dict[str, dict[str, Any]],
    model: str,
    profile_fingerprint: str = "",
) -> tuple[list[AnalysisResult], dict[str, dict[str, Any]], list[str]]:
    results: list[AnalysisResult] = []
    errors: list[str] = []
    misses: list[tuple[RankedPaper, str | None, str]] = []

    for ranked in ranked_papers[: config.max_papers]:
        full_text = full_texts.get(ranked.paper.identity)
        key = make_cache_key(
            ranked,
            full_text=full_text,
            model=model,
            prompt_version=config.prompt_version,
            profile_fingerprint=profile_fingerprint,
        )
        cached = cache.get(key)
        if cached is not None:
            try:
                analysis = PaperAnalysis.model_validate(cached["analysis"])
                provenance = Provenance.model_validate(cached["provenance"])
                final_score = (
                    0.55 * ranked.score.base_score + 0.45 * (analysis.relevance_score / 10)
                )
                results.append(
                    AnalysisResult(
                        paper=AnalyzedPaper(
                            ranked=ranked,
                            analysis=analysis,
                            provenance=provenance,
                            final_score=max(0.0, min(1.0, final_score)),
                            tier=(
                                "deep_read"
                                if provenance.reading_scope is ReadingScope.FULL_TEXT
                                else "browse"
                            ),
                        ),
                        cache_hit=True,
                    )
                )
                continue
            except (KeyError, ValidationError):
                pass
        misses.append((ranked, full_text, key))

    def run_one(item: tuple[RankedPaper, str | None, str]) -> tuple[str, AnalyzedPaper]:
        ranked, full_text, key = item
        analysis = analyzer.analyze(ranked, full_text)
        final_score = 0.55 * ranked.score.base_score + 0.45 * (analysis.relevance_score / 10)
        analyzed = AnalyzedPaper(
            ranked=ranked,
            analysis=analysis,
            provenance=Provenance(
                reading_scope=ReadingScope.FULL_TEXT if full_text else ReadingScope.ABSTRACT,
                model=model,
                prompt_version=config.prompt_version,
                analyzed_at=datetime.now(UTC),
                input_hash=make_input_hash(ranked, full_text),
            ),
            final_score=max(0.0, min(1.0, final_score)),
            tier="deep_read" if full_text else "browse",
        )
        return key, analyzed

    with ThreadPoolExecutor(max_workers=config.max_concurrency) as executor:
        futures = {executor.submit(run_one, item): item[0].paper.identity for item in misses}
        for future in as_completed(futures):
            identity = futures[future]
            try:
                key, analyzed = future.result()
                cache[key] = {
                    "analysis": analyzed.analysis.model_dump(mode="json"),
                    "provenance": analyzed.provenance.model_dump(mode="json"),
                }
                results.append(AnalysisResult(paper=analyzed, cache_hit=False))
            except Exception as error:  # Individual failures are gated below.
                errors.append(f"{identity}: {type(error).__name__}: {error}")

    attempted = len(misses)
    if attempted and len(errors) / attempted > config.max_failure_ratio:
        raise RuntimeError(
            f"analysis failure ratio {len(errors)}/{attempted} exceeds "
            f"{config.max_failure_ratio:.0%}"
        )
    results.sort(key=lambda item: (-item.paper.final_score, item.paper.ranked.paper.identity))
    return results, cache, errors


def load_prompt(root: Path, version: str) -> str:
    path = root / "prompts" / f"{version}.md"
    return path.read_text(encoding="utf-8")
