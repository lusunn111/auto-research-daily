from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime

from auto_research_daily.config import RankingConfig, ResearchProfileConfig
from auto_research_daily.models import RankedPaper, RawPaper, ScoreBreakdown, ZoteroDocument

TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9._+-]*", re.IGNORECASE)


def normalize_text(value: str) -> str:
    return " ".join(value.casefold().replace("-", " ").split())


def tokenize(value: str) -> Counter[str]:
    return Counter(TOKEN_RE.findall(normalize_text(value)))


def cosine(left: Counter[str], right: Counter[str]) -> float:
    if not left or not right:
        return 0.0
    shared = set(left).intersection(right)
    numerator = sum(left[token] * right[token] for token in shared)
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    return numerator / (left_norm * right_norm) if left_norm and right_norm else 0.0


def _phrase_matches(text: str, terms: Iterable[str]) -> tuple[str, ...]:
    normalized = normalize_text(text)
    return tuple(term for term in terms if normalize_text(term) in normalized)


def _deterministic_exploration(identity: str, day: str) -> float:
    digest = hashlib.sha256(f"{day}:{identity}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / (2**64 - 1)


def _personal_score(paper: RawPaper, documents: tuple[ZoteroDocument, ...]) -> float:
    if not documents:
        return 0.0
    paper_vector = tokenize(f"{paper.title} {paper.abstract}")
    similarities = sorted(
        (
            cosine(paper_vector, tokenize(f"{document.title} {document.abstract}"))
            for document in documents
        ),
        reverse=True,
    )
    top_k = similarities[:5]
    top_k_mean = sum(top_k) / len(top_k)
    centroid = Counter()
    for document in documents:
        centroid.update(tokenize(f"{document.title} {document.abstract}"))
    return min(1.0, 0.7 * top_k_mean + 0.3 * cosine(paper_vector, centroid))


def score_paper(
    paper: RawPaper,
    *,
    profile: ResearchProfileConfig,
    ranking: RankingConfig,
    documents: tuple[ZoteroDocument, ...],
    now: datetime,
) -> ScoreBreakdown:
    text = f"{paper.title} {paper.abstract} {' '.join(paper.categories)}"
    core = _phrase_matches(text, profile.core_terms)
    adjacent = _phrase_matches(text, profile.adjacent_terms)
    negative = _phrase_matches(text, profile.negative_terms)

    profile_vector = tokenize(
        " ".join((profile.description, *profile.core_terms, *profile.adjacent_terms))
    )
    semantic = cosine(tokenize(text), profile_vector)
    rule_score = min(1.0, 0.25 * len(core) + 0.08 * len(adjacent))
    topic = min(1.0, 0.65 * rule_score + 0.35 * min(1.0, semantic * 4.0))
    if negative:
        topic *= 0.25

    personal = _personal_score(paper, documents)
    age_hours = max(0.0, (now - paper.updated_at.astimezone(UTC)).total_seconds() / 3600)
    recency = math.exp(-age_hours / (24 * 4))
    exploration = _deterministic_exploration(paper.identity, now.date().isoformat())
    base_score = (
        ranking.topic_weight * topic
        + ranking.personal_weight * personal
        + ranking.recency_weight * recency
        + ranking.exploration_weight * exploration
    )
    return ScoreBreakdown(
        topic=topic,
        personal=personal,
        recency=recency,
        exploration=exploration,
        base_score=max(0.0, min(1.0, base_score)),
        matched_terms=tuple((*core, *adjacent)),
        negative_terms=negative,
    )


def _paper_similarity(left: RawPaper, right: RawPaper) -> float:
    return cosine(
        tokenize(f"{left.title} {left.abstract}"),
        tokenize(f"{right.title} {right.abstract}"),
    )


def rank_papers(
    papers: list[RawPaper],
    *,
    profile: ResearchProfileConfig,
    config: RankingConfig,
    documents: list[ZoteroDocument] | None = None,
    now: datetime | None = None,
) -> list[RankedPaper]:
    timestamp = (now or datetime.now(UTC)).astimezone(UTC)
    corpus = tuple(documents or ())
    scored = [
        (
            paper,
            score_paper(
                paper,
                profile=profile,
                ranking=config,
                documents=corpus,
                now=timestamp,
            ),
        )
        for paper in papers
    ]
    eligible = [
        item
        for item in scored
        if item[1].base_score >= config.min_prefilter_score or item[1].matched_terms
    ]
    if not eligible:
        eligible = sorted(scored, key=lambda item: item[1].base_score, reverse=True)[:10]
    remaining = sorted(
        eligible,
        key=lambda item: (-item[1].base_score, item[0].canonical_id),
    )[: config.candidate_pool]

    selected: list[tuple[RawPaper, ScoreBreakdown]] = []
    while remaining:
        best_index = 0
        best_mmr = -1.0
        for index, item in enumerate(remaining):
            max_similarity = max(
                (_paper_similarity(item[0], chosen[0]) for chosen in selected),
                default=0.0,
            )
            mmr = (
                config.diversity_lambda * item[1].base_score
                - (1 - config.diversity_lambda) * max_similarity
            )
            if mmr > best_mmr:
                best_index = index
                best_mmr = mmr
        selected.append(remaining.pop(best_index))

    return [
        RankedPaper(paper=paper, score=score, rank=index)
        for index, (paper, score) in enumerate(selected, start=1)
    ]


def deduplicate_papers(papers: list[RawPaper]) -> list[RawPaper]:
    """Deduplicate by canonical identifier, keeping the newest version."""
    found: dict[tuple[str, str], RawPaper] = {}
    for paper in papers:
        key = (paper.source, paper.canonical_id)
        current = found.get(key)
        if current is None or (paper.version, paper.updated_at) > (
            current.version,
            current.updated_at,
        ):
            found[key] = paper
    return sorted(found.values(), key=lambda paper: (-paper.updated_at.timestamp(), paper.identity))
