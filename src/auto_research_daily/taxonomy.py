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


@dataclass(frozen=True, slots=True)
class ResearchTag:
    key: str
    label: str
    description: str
    aliases: tuple[str, ...]


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

RESEARCH_TAGS: tuple[ResearchTag, ...] = (
    ResearchTag(
        "action-prediction",
        "动作预测",
        "动作生成、动作条件预测与轨迹建模。",
        ("动作预测", "动作生成", "action prediction", "action-conditioned", "trajectory"),
    ),
    ResearchTag(
        "data",
        "具身数据",
        "机器人数据采集、整理、生成与训练语料。",
        (
            "数据集",
            "数据采集",
            "数据整理",
            "训练数据",
            "dataset",
            "data collection",
            "data curation",
        ),
    ),
    ResearchTag(
        "efficient-inference",
        "高效推理",
        "推理时延、吞吐、令牌压缩与早停。",
        (
            "高效推理",
            "推理加速",
            "推理效率",
            "计算效率",
            "令牌剪枝",
            "token pruning",
            "efficient inference",
            "latency",
            "throughput",
            "早停",
        ),
    ),
    ResearchTag(
        "evaluation",
        "评测与可靠性",
        "基准、指标、鲁棒性、安全与可靠性评估。",
        (
            "评测",
            "评估",
            "基准",
            "可靠性",
            "鲁棒性",
            "安全",
            "benchmark",
            "evaluation",
            "robustness",
            "safety",
        ),
    ),
    ResearchTag(
        "generalist-robotics",
        "通用机器人",
        "通用策略、跨任务与跨具身泛化。",
        (
            "通用机器人",
            "通用策略",
            "跨具身",
            "任务泛化",
            "generalist",
            "cross-embodiment",
            "cross embodiment",
        ),
    ),
    ResearchTag(
        "model-quantization",
        "模型量化",
        "低比特、混合精度、剪枝与模型压缩。",
        (
            "量化",
            "低比特",
            "混合精度",
            "模型压缩",
            "剪枝",
            "quantization",
            "quantized",
            "low-bit",
            "mixed precision",
            "pruning",
        ),
    ),
    ResearchTag(
        "navigation",
        "具身导航",
        "机器人导航、路径规划与空间推理。",
        ("具身导航", "机器人导航", "人群导航", "路径规划", "navigation", "spatial reasoning"),
    ),
    ResearchTag(
        "physical-ai",
        "物理智能",
        "物理交互、动力学、控制与真实世界执行。",
        (
            "物理智能",
            "物理人工智能",
            "物理交互",
            "动力学",
            "真实机器人",
            "physical ai",
            "physical interaction",
            "dynamics",
        ),
    ),
    ResearchTag(
        "policy-learning",
        "策略学习",
        "模仿学习、强化学习、扩散与流式策略。",
        (
            "策略学习",
            "模仿学习",
            "强化学习",
            "扩散策略",
            "流匹配",
            "policy learning",
            "imitation learning",
            "reinforcement learning",
            "diffusion policy",
            "flow matching",
        ),
    ),
    ResearchTag(
        "robot-learning",
        "机器人学习",
        "在线学习、技能学习与跨任务迁移。",
        (
            "机器人学习",
            "在线学习",
            "技能学习",
            "robot learning",
            "online learning",
            "skill learning",
        ),
    ),
    ResearchTag(
        "robot-manipulation",
        "机器人操作",
        "抓取、双臂操作、长时程和精细操作。",
        ("机器人操作", "抓取", "夹持器", "长时程操作", "manipulation", "grasping", "bimanual"),
    ),
    ResearchTag(
        "simulation",
        "仿真与生成",
        "世界模拟、视频生成和交互式环境生成。",
        (
            "仿真",
            "模拟器",
            "世界模拟",
            "视频生成",
            "生成式仿真",
            "simulation",
            "simulator",
            "world simulation",
            "video generation",
        ),
    ),
    ResearchTag(
        "speculative-decoding",
        "推测解码",
        "草稿、验证与并行解码加速。",
        ("推测解码", "并行解码", "speculative decoding", "parallel decoding"),
    ),
    ResearchTag(
        "vision-language",
        "视觉语言",
        "视觉语言模型、多模态理解与语言条件控制。",
        (
            "视觉语言",
            "多模态大语言模型",
            "视觉语言模型",
            "vision-language",
            "vision language",
            "multimodal large language model",
            "mllm",
            "lvlm",
        ),
    ),
    ResearchTag(
        "world-modeling",
        "世界建模",
        "世界模型、世界动作模型和潜在动态预测。",
        (
            "世界模型",
            "世界动作模型",
            "视频世界模型",
            "world model",
            "world action model",
            "world modeling",
            "latent dynamics",
        ),
    ),
)

TAG_BY_KEY = {tag.key: tag for tag in RESEARCH_TAGS}


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


def classify_tags(item: AnalyzedPaper) -> tuple[ResearchTag, ...]:
    analysis = item.analysis
    paper = item.ranked.paper
    signal = _normalize(
        " ".join(
            (
                analysis.primary_topic,
                *analysis.tags,
                *item.ranked.score.matched_terms,
                paper.title,
                paper.abstract,
            )
        )
    )
    return tuple(
        tag for tag in RESEARCH_TAGS if any(_normalize(alias) in signal for alias in tag.aliases)
    )


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
