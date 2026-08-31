from types import SimpleNamespace

import pytest

from auto_research_daily.taxonomy import classify_paper, classify_tags


def paper_item(
    *,
    title: str,
    abstract: str,
    primary_topic: str,
    tags: tuple[str, ...] = (),
    matched_terms: tuple[str, ...] = (),
) -> SimpleNamespace:
    return SimpleNamespace(
        analysis=SimpleNamespace(primary_topic=primary_topic, tags=tags),
        ranked=SimpleNamespace(
            paper=SimpleNamespace(title=title, abstract=abstract),
            score=SimpleNamespace(matched_terms=matched_terms),
        ),
    )


@pytest.mark.parametrize(
    ("expected", "item"),
    [
        (
            "wam",
            paper_item(
                title="A World Action Model for Manipulation",
                abstract="We predict future robot states.",
                primary_topic="具身世界动作模型",
                tags=("世界模型",),
            ),
        ),
        (
            "vla",
            paper_item(
                title="Efficient Vision-Language-Action Adaptation",
                abstract="A compact policy for robot manipulation.",
                primary_topic="视觉语言动作模型",
                tags=("VLA",),
            ),
        ),
        (
            "quantization",
            paper_item(
                title="Mixed-Precision Quantization for Autoregressive Models",
                abstract="The method reduces memory use.",
                primary_topic="模型量化",
            ),
        ),
        (
            "efficient-inference",
            paper_item(
                title="Visual Token Pruning for Faster Inference",
                abstract="We reduce decoding latency.",
                primary_topic="视觉语言模型推理加速",
            ),
        ),
        (
            "frontier",
            paper_item(
                title="A General Multimodal Retrieval Architecture",
                abstract="Training data are curated from several sources.",
                primary_topic="多模态检索",
            ),
        ),
    ],
)
def test_classify_paper_uses_stable_public_topics(
    expected: str,
    item: SimpleNamespace,
) -> None:
    assert classify_paper(item).key == expected


def test_classify_tags_uses_fixed_alias_registry() -> None:
    item = paper_item(
        title="Efficient Vision-Language-Action World Models",
        abstract="We accelerate action-conditioned robot policy inference.",
        primary_topic="视觉语言动作模型",
        tags=("世界模型", "推理加速"),
    )

    keys = {tag.key for tag in classify_tags(item)}

    assert {"efficient-inference", "vision-language", "world-modeling"} <= keys
