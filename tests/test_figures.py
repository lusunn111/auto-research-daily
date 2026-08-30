from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from PIL import Image
from pydantic import ValidationError

from auto_research_daily.config import FigureConfig
from auto_research_daily.figures import (
    ArxivFigureClient,
    _cache_is_fresh,
    parse_figure_gallery,
)
from auto_research_daily.models import FigureAsset, FigureGallery, FigurePanel

CHECKED_AT = datetime(2026, 8, 30, tzinfo=UTC)
HTML_URL = "https://arxiv.org/html/2608.12345v2"


def _png() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (64, 48), "white").save(output, format="PNG")
    return output.getvalue()


def test_parser_extracts_figures_one_and_two_and_rejects_unsafe_urls() -> None:
    html = """
    <figure id="S1.F1">
      <img src="overview.png">
      <img src="https://example.com/external.png">
      <img src="https://arxiv.org/html/2608.99999v2/wrong-paper.png">
      <img src="%2e%2e/2608.99999v2/traversal.png">
      <figcaption>Figure 1: The system overview.</figcaption>
    </figure>
    <figure id="S2.F2">
      <img src="panel-a.png"><img src="panel-b.png">
      <figcaption>Fig. 2. Robot experiments.</figcaption>
    </figure>
    <figure id="S10.F10">
      <img src="wrong.png"><figcaption>Figure 10: Not Figure 1.</figcaption>
    </figure>
    """

    gallery = parse_figure_gallery(html, HTML_URL, CHECKED_AT)

    assert gallery.status == "available"
    assert [figure.number for figure in gallery.figures] == [1, 2]
    assert gallery.figures[0].caption == "The system overview."
    assert [panel.original_url for panel in gallery.figures[0].panels] == [
        f"{HTML_URL}/overview.png"
    ]
    assert len(gallery.figures[1].panels) == 2


def test_parser_ignores_nested_caption_and_missing_anchor() -> None:
    html = """
    <figure id="outer">
      <figure id="inner"><img src="inner.png">
        <figcaption>Figure 1: Inner caption.</figcaption>
      </figure>
      <img src="outer.png"><figcaption>Figure 2: Outer caption.</figcaption>
    </figure>
    <figure><img src="unanchored.png">
      <figcaption>Figure 1: No stable source link.</figcaption>
    </figure>
    """

    gallery = parse_figure_gallery(html, HTML_URL, CHECKED_AT)

    assert [figure.number for figure in gallery.figures] == [1, 2]
    assert gallery.figures[0].caption == "Inner caption."
    assert gallery.figures[1].caption == "Outer caption."
    assert gallery.figures[1].panels[0].original_url == f"{HTML_URL}/outer.png"


def test_panel_rejects_site_root_path() -> None:
    with pytest.raises(ValidationError):
        FigurePanel(
            original_url=f"{HTML_URL}/overview.png",
            cached_path="/figures/arxiv/2608.12345/v2/fig1-panel1.png",
        )


def test_panel_rejects_encoded_path_traversal() -> None:
    with pytest.raises(ValidationError):
        FigurePanel(
            original_url=f"{HTML_URL}/%2e%2e/2608.99999v1/attack.png",
        )
    with pytest.raises(ValidationError):
        FigurePanel(
            original_url=f"{HTML_URL}/safe%2f..%2f2608.99999v1/attack.png",
        )


def test_parser_percent_encodes_source_fragment_for_markdown_safety() -> None:
    gallery = parse_figure_gallery(
        """
        <figure id="bad) fragment"><img src="overview.png">
          <figcaption>Figure 1: Safe caption.</figcaption>
        </figure>
        """,
        HTML_URL,
        CHECKED_AT,
    )

    assert gallery.figures[0].source_url.endswith("#bad%29%20fragment")


def test_remote_only_gallery_is_retried_after_negative_cache_ttl(tmp_path: Path) -> None:
    gallery = parse_figure_gallery(
        """
        <figure id="S1.F1"><img src="overview.svg">
          <figcaption>Figure 1: Remote-only panel.</figcaption>
        </figure>
        """,
        HTML_URL,
        CHECKED_AT,
    )

    assert _cache_is_fresh(
        gallery,
        now=CHECKED_AT + timedelta(hours=23),
        site_dir=tmp_path,
        negative_hours=24,
    )
    assert not _cache_is_fresh(
        gallery,
        now=CHECKED_AT + timedelta(hours=24),
        site_dir=tmp_path,
        negative_hours=24,
    )

    cached_path = "figures/arxiv/2608.12345/v2/fig1-panel1.png"
    target = tmp_path / cached_path
    target.parent.mkdir(parents=True)
    target.write_bytes(b"cached")
    cached_figure = FigureAsset(
        number=1,
        label="Figure 1",
        caption="Cached panel.",
        panels=(
            FigurePanel(
                original_url=f"{HTML_URL}/cached.png",
                cached_path=cached_path,
            ),
        ),
        source_url=f"{HTML_URL}#S1.F1",
    )
    mixed = FigureGallery(
        status="available",
        html_url=HTML_URL,
        checked_at=CHECKED_AT,
        figures=(cached_figure, gallery.figures[0].model_copy(update={"number": 2})),
    )
    assert not _cache_is_fresh(
        mixed,
        now=CHECKED_AT + timedelta(hours=24),
        site_dir=tmp_path,
        negative_hours=24,
    )


def test_parser_caps_large_multi_panel_figure() -> None:
    images = "".join(f'<img src="panel-{index}.png">' for index in range(12))
    gallery = parse_figure_gallery(
        f'<figure id="S1.F1">{images}<figcaption>Figure 1: Many panels.</figcaption></figure>',
        HTML_URL,
        CHECKED_AT,
    )

    assert gallery.status == "available"
    assert len(gallery.figures[0].panels) == 8


def test_client_fetches_and_mirrors_verified_png(tmp_path: Path) -> None:
    html = """
    <figure id="S1.F1"><img src="overview.png"><img src="unused.png">
      <figcaption>Figure 1: The system overview.</figcaption>
    </figure>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/html/2608.12345v2":
            return httpx.Response(200, headers={"content-type": "text/html"}, text=html)
        if request.url.path == "/html/2608.12345v2/overview.png":
            return httpx.Response(
                200,
                headers={"content-type": "image/png"},
                content=_png(),
            )
        if request.url.path.endswith("unused.png"):
            raise AssertionError("only the displayed first panel should be mirrored")
        return httpx.Response(404)

    config = FigureConfig(request_delay_seconds=0)
    transport = httpx.MockTransport(handler)
    with ArxivFigureClient(
        config=config,
        site_dir=tmp_path,
        user_agent="test/1.0",
        client=httpx.Client(transport=transport),
    ) as client:
        gallery = client.fetch_gallery("2608.12345", 2, CHECKED_AT)
        mirrored = client.mirror_gallery(gallery, arxiv_id="2608.12345", version=2)

    panel = mirrored.figures[0].panels[0]
    assert panel.cached_path == "figures/arxiv/2608.12345/v2/fig1-panel1.png"
    assert (tmp_path / panel.cached_path).stat().st_size > 0
    assert len(mirrored.figures[0].panels) == 1
    assert client.image_attempts == 1


def test_client_rejects_image_content_type_without_breaking_gallery(tmp_path: Path) -> None:
    html = """
    <figure id="S1.F1"><img src="fake.png">
      <figcaption>Figure 1: Fake response.</figcaption>
    </figure>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("fake.png"):
            return httpx.Response(200, headers={"content-type": "text/html"}, text="bad")
        return httpx.Response(200, headers={"content-type": "text/html"}, text=html)

    with ArxivFigureClient(
        config=FigureConfig(request_delay_seconds=0),
        site_dir=tmp_path,
        user_agent="test/1.0",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    ) as client:
        gallery = client.fetch_gallery("2608.12345", 2, CHECKED_AT)
        stale_figure = FigureAsset(
            number=1,
            label="Figure 1",
            caption="Fake response.",
            panels=(
                FigurePanel(
                    original_url=gallery.figures[0].panels[0].original_url,
                    cached_path=(
                        "figures/arxiv/2608.12345/v2/fig1-panel1.png"
                    ),
                ),
            ),
            source_url=gallery.figures[0].source_url,
        )
        gallery = FigureGallery(
            status="available",
            html_url=gallery.html_url,
            checked_at=gallery.checked_at,
            figures=(stale_figure,),
        )
        mirrored = client.mirror_gallery(gallery, arxiv_id="2608.12345", version=2)

    assert mirrored.status == "available"
    assert mirrored.figures[0].panels[0].cached_path is None
    assert client.panel_cache_failures == 1
    assert not list(tmp_path.rglob("*.png"))


def test_client_enforces_total_download_budget(tmp_path: Path) -> None:
    gallery = parse_figure_gallery(
        """
        <figure id="S1.F1"><img src="overview.png">
          <figcaption>Figure 1: Budgeted panel.</figcaption>
        </figure>
        """,
        HTML_URL,
        CHECKED_AT,
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "image/png"},
            content=_png(),
        )

    config = FigureConfig(
        request_delay_seconds=0,
        max_total_image_bytes=1_000_000,
    )
    with ArxivFigureClient(
        config=config,
        site_dir=tmp_path,
        user_agent="test/1.0",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    ) as client:
        client.total_downloaded_image_bytes = config.max_total_image_bytes - 10
        mirrored = client.mirror_gallery(gallery, arxiv_id="2608.12345", version=2)

    assert client.total_downloaded_image_bytes <= config.max_total_image_bytes
    assert mirrored.figures[0].panels[0].cached_path is None
    assert client.panel_cache_failures == 1


def test_client_enforces_elapsed_time_budget_after_throttle(tmp_path: Path) -> None:
    gallery = parse_figure_gallery(
        """
        <figure id="S1.F1"><img src="one.png">
          <figcaption>Figure 1: First panel.</figcaption>
        </figure>
        <figure id="S2.F2"><img src="two.png">
          <figcaption>Figure 2: Second panel.</figcaption>
        </figure>
        """,
        HTML_URL,
        CHECKED_AT,
    )
    current_time = [0.0]

    def clock() -> float:
        return current_time[0]

    def sleep(seconds: float) -> None:
        current_time[0] += seconds

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "image/png"},
            content=_png(),
        )

    with ArxivFigureClient(
        config=FigureConfig(
            request_delay_seconds=0.2,
            max_elapsed_seconds=0.1,
        ),
        site_dir=tmp_path,
        user_agent="test/1.0",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        clock=clock,
        sleep=sleep,
    ) as client:
        mirrored = client.mirror_gallery(gallery, arxiv_id="2608.12345", version=2)

    assert current_time[0] == pytest.approx(0.1)
    assert client.request_count == 1
    assert mirrored.figures[0].panels[0].cached_path is not None
    assert mirrored.figures[1].panels[0].cached_path is None
