from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from auto_research_daily.models import AnalyzedPaper


@dataclass(frozen=True, slots=True)
class ResearchTopic:
    key: str
    label: str
    description: str
    terms: tuple[str, ...]
    abstract_weight: int = 1


# The public taxonomy deliberately stays small and stable. Model-generated
# ``primary_topic`` remains available as a fine-grained description, while this
# layer gives the website predictable shelves that can be linked and filtered.
RESEARCH_TOPICS: tuple[ResearchTopic, ...] = (
    ResearchTopic(
        key="vla",
        label="视觉语言动作（VLA）",
        description="视觉、语言与机器人动作的统一建模、适配与安全。",
        terms=(
            "vision-language-action",
            "vision language action",
            "vision–language–action",
            "视觉语言动作",
            "视觉-语言-动作",
            "视觉—语言—动作",
            "vla",
        ),
    ),
    ResearchTopic(
        key="wam",
        label="世界动作模型（WAM）",
        description="联合建模世界状态、动作与未来转移的具身模型。",
        terms=(
            "world action model",
            "world-action model",
            "world–action model",
            "世界动作模型",
            "wam",
            "action-conditioned world model",
            "action conditioned world model",
            "动作条件世界模型",
            "latent action model",
            "潜在动作模型",
        ),
    ),
    ResearchTopic(
        key="world-model",
        label="世界模型（World Model）",
        description="动态预测、视频生成、世界模拟与物理交互建模。",
        terms=(
            "world model",
            "world-model",
            "世界模型",
            "world simulator",
            "world simulation",
            "世界模拟器",
            "video world model",
            "视频世界模型",
            "action-conditioned video",
            "action conditioned video",
            "动作条件视频",
            "dynamics prediction",
            "dynamics model",
            "动力学预测",
            "jepa",
        ),
    ),
    ResearchTopic(
        key="robot-learning",
        label="机器人学习与控制",
        description="操作、导航、规划、强化学习与跨具身策略。",
        terms=(
            "robot learning",
            "robotic manipulation",
            "robot manipulation",
            "机器人学习",
            "机器人操作",
            "policy learning",
            "策略学习",
            "diffusion policy",
            "扩散策略",
            "reinforcement learning",
            "强化学习",
            "navigation",
            "导航",
            "grasping",
            "抓取",
            "task planning",
            "任务规划",
            "robot control",
            "机器人控制",
            "humanoid",
            "人形机器人",
            "具身",
            "robot",
            "机器人",
        ),
    ),
    ResearchTopic(
        key="dataset",
        label="数据集（Dataset）",
        description="具身数据采集、整理、生成与训练语料建设。",
        terms=(
            "dataset",
            "data collection",
            "data curation",
            "training data",
            "数据集",
            "数据采集",
            "数据整理",
            "训练数据",
        ),
        abstract_weight=0,
    ),
    ResearchTopic(
        key="benchmark",
        label="基准评测（Benchmark）",
        description="任务基准、评价指标、可靠性与系统性对比。",
        terms=(
            "benchmark",
            "evaluation framework",
            "evaluation metric",
            "基准评测",
            "评测基准",
            "评价指标",
            "评估框架",
        ),
        abstract_weight=0,
    ),
    ResearchTopic(
        key="efficient-inference",
        label="高效推理",
        description="推理解码、令牌压缩、早停、时延与吞吐优化。",
        terms=(
            "speculative decoding",
            "推测解码",
            "efficient inference",
            "inference acceleration",
            "inference optimization",
            "推理加速",
            "推理效率",
            "token pruning",
            "visual token pruning",
            "令牌剪枝",
            "early stopping",
            "early exit",
            "自适应早停",
            "latency optimization",
            "throughput optimization",
            "解码采样",
            "计算效率",
        ),
    ),
    ResearchTopic(
        key="quantization",
        label="模型量化（Quantization）",
        description="低比特、混合精度、校准与模型压缩。",
        terms=(
            "quantization",
            "quantized",
            "low-bit",
            "low bit",
            "mixed-precision",
            "mixed precision",
            "model compression",
            "calibration data",
            "量化",
            "低比特",
            "混合精度",
            "模型压缩",
            "校准数据",
        ),
    ),
    ResearchTopic(
        key="frontier",
        label="前沿探索",
        description="与研究画像相邻、但尚不属于稳定主轴的高价值工作。",
        terms=(),
    ),
)

TOPIC_BY_KEY = {topic.key: topic for topic in RESEARCH_TOPICS}


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def classify_paper(item: AnalyzedPaper) -> ResearchTopic:
    """Project a fine-grained analysis onto one stable public research shelf."""

    analysis = item.analysis
    paper = item.ranked.paper
    title = _normalize(paper.title)
    metadata = _normalize(
        " ".join(
            (
                analysis.primary_topic,
                *analysis.tags,
                *item.ranked.score.matched_terms,
            )
        )
    )
    abstract = _normalize(paper.abstract)

    scored: list[tuple[int, int, ResearchTopic]] = []
    for priority, topic in enumerate(RESEARCH_TOPICS[:-1]):
        score = 0
        for raw_term in topic.terms:
            term = _normalize(raw_term)
            if term in title:
                score += 5
            if term in metadata:
                score += 4
            if term in abstract:
                score += topic.abstract_weight
        scored.append((score, -priority, topic))

    best_score, _, best_topic = max(scored, key=lambda entry: (entry[0], entry[1]))
    return best_topic if best_score else TOPIC_BY_KEY["frontier"]


def paper_search_text(item: AnalyzedPaper, topic: ResearchTopic) -> str:
    analysis = item.analysis
    paper = item.ranked.paper
    return _normalize(
        " ".join(
            (
                topic.label,
                analysis.primary_topic,
                *analysis.tags,
                paper.title,
                analysis.title_zh,
                *paper.authors,
                paper.abstract,
                analysis.setting,
                analysis.motivation,
                analysis.insight,
                analysis.analysis,
                *analysis.method,
                *analysis.experiments,
                analysis.relation_to_research,
                analysis.why_recommended,
            )
        )
    )
