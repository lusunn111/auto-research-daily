from auto_research_daily.fulltext import select_evidence_excerpt


def test_evidence_excerpt_preserves_late_method_and_experiment_sections() -> None:
    text = (
        "Introduction "
        + "background " * 1800
        + "Method We propose an action-conditioned latent world model. "
        + "details " * 800
        + "Experiments We evaluate manipulation success and inference latency. "
        + "results " * 800
        + "Limitations The benchmark covers only tabletop robots."
    )
    excerpt = select_evidence_excerpt(text, 6000)
    assert len(excerpt) <= 6000
    assert "action-conditioned latent world model" in excerpt
    assert "evaluate manipulation success" in excerpt
