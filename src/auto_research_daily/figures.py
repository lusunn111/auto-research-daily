from __future__ import annotations

import io
import logging
import os
import re
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlsplit

import httpx
from PIL import Image, UnidentifiedImageError
from pydantic import TypeAdapter, ValidationError
from selectolax.lexbor import LexborHTMLParser, LexborNode

from auto_research_daily.config import FigureConfig
from auto_research_daily.models import (
    AnalyzedPaper,
    FigureAsset,
    FigureGallery,
    FigurePanel,
)
from auto_research_daily.storage import atomic_write_json, load_json

LOGGER = logging.getLogger(__name__)
ARXIV_ID_RE = re.compile(r"^\d{4}\.\d{4,5}$")
FIGURE_NUMBER_RE = re.compile(
    r"^(?:figure|fig\.)\s*([12])(?!\d|\.\d)\s*[:.]?\s*",
    re.IGNORECASE,
)
ARXIV_HOSTS = frozenset({"arxiv.org", "www.arxiv.org"})
IMAGE_CONTENT_TYPES = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
}
NEGATIVE_CLOCK_SKEW = timedelta(minutes=5)
MAX_PANELS_PER_FIGURE = 8


class FigureUnavailableError(RuntimeError):
    pass


class TransientFigureError(RuntimeError):
    pass


class FigureBudgetExceededError(RuntimeError):
    pass


@dataclass(frozen=True)
class FigureRunStats:
    cache_hits: int = 0
    requests: int = 0
    available: int = 0
    failed: int = 0
    panel_failed: int = 0


def figure_html_url(arxiv_id: str, version: int) -> str:
    if ARXIV_ID_RE.fullmatch(arxiv_id) is None or type(version) is not int or version < 1:
        raise ValueError("invalid arXiv identity")
    return f"https://arxiv.org/html/{arxiv_id}v{version}"


def _normalize_caption(value: str) -> str:
    return " ".join(value.split())


def _is_safe_arxiv_url(
    candidate: str,
    *,
    expected_path: str,
    exact_path: bool,
) -> bool:
    parsed = urlsplit(candidate)
    try:
        port = parsed.port
    except ValueError:
        return False
    decoded_path = unquote(parsed.path)
    encoded_path = parsed.path.casefold()
    if (
        re.search(r"%(?:2e|2f|5c)", encoded_path) is not None
        or "\\" in decoded_path
        or "\x00" in decoded_path
        or any(component in {".", ".."} for component in decoded_path.split("/"))
    ):
        return False
    path_matches = parsed.path == expected_path if exact_path else parsed.path.startswith(
        expected_path.rstrip("/") + "/"
    )
    return (
        parsed.scheme == "https"
        and parsed.hostname in ARXIV_HOSTS
        and parsed.username is None
        and parsed.password is None
        and port in (None, 443)
        and not parsed.query
        and not parsed.fragment
        and path_matches
    )


def _resolve_current_paper_image(source: str, html_url: str) -> str | None:
    expected_path = urlsplit(html_url).path
    for base_url in (html_url, f"{html_url}/"):
        candidate = urljoin(base_url, source)
        if _is_safe_arxiv_url(
            candidate,
            expected_path=expected_path,
            exact_path=False,
        ):
            return candidate
    return None


def _resolved_image_urls(images: list[LexborNode], html_url: str) -> tuple[str, ...]:
    urls: list[str] = []
    seen: set[str] = set()
    for image in images:
        source = (image.attributes.get("src") or "").strip()
        if not source:
            continue
        try:
            candidate = _resolve_current_paper_image(source, html_url)
        except ValueError:
            continue
        if candidate is not None and candidate not in seen:
            seen.add(candidate)
            urls.append(candidate)
            if len(urls) >= MAX_PANELS_PER_FIGURE:
                break
    return tuple(urls)


def _images_owned_by_figure(node: LexborNode) -> list[LexborNode]:
    owned: list[LexborNode] = []
    for image in node.css("img"):
        ancestor = image.parent
        belongs_to_node = False
        while ancestor is not None:
            if ancestor == node:
                belongs_to_node = True
                break
            if ancestor.tag == "figure":
                break
            ancestor = ancestor.parent
        if belongs_to_node:
            owned.append(image)
    return owned


def _figure_images(node: LexborNode, html_url: str) -> tuple[str, ...]:
    urls = _resolved_image_urls(_images_owned_by_figure(node), html_url)
    if urls:
        return urls

    sibling = node.prev
    while sibling is not None and (sibling.tag or "").startswith("-"):
        sibling = sibling.prev
    if sibling is None:
        return ()
    tag = (sibling.tag or "").casefold()
    if (
        tag in {"figure", "img"}
        or re.fullmatch(r"h[1-6]", tag) is not None
        or _normalize_caption(sibling.text(separator=" ", strip=True))
    ):
        return ()
    return _resolved_image_urls(sibling.css("img"), html_url)


def parse_figure_gallery(html: str, html_url: str, checked_at: datetime) -> FigureGallery:
    """Extract reliable Figure 1 and Figure 2 metadata from one arXiv HTML document."""
    tree = LexborHTMLParser(html)
    by_number: dict[int, tuple[str, tuple[str, ...], str]] = {}

    for node in tree.css("figure"):
        caption_node = next(
            (
                candidate
                for candidate in node.css("figcaption")
                if candidate.parent is not None
                and candidate.parent.tag == "figure"
                and candidate.parent == node
            ),
            None,
        )
        if caption_node is None:
            continue
        raw_caption = _normalize_caption(caption_node.text(separator=" ", strip=True))
        match = FIGURE_NUMBER_RE.match(raw_caption)
        if match is None:
            continue
        number = int(match.group(1))
        caption = raw_caption[match.end() :].strip()
        fragment = (node.attributes.get("id") or "").strip()
        if not caption or not fragment:
            continue
        image_urls = _figure_images(node, html_url)
        if not image_urls:
            continue

        current = by_number.get(number)
        if current is None:
            safe_fragment = quote(fragment, safe="-._~:")
            by_number[number] = (caption, image_urls, f"{html_url}#{safe_fragment}")
            continue
        old_caption, old_urls, source_url = current
        merged = tuple(dict.fromkeys((*old_urls, *image_urls)))[:MAX_PANELS_PER_FIGURE]
        by_number[number] = (old_caption, merged, source_url)

    figures = tuple(
        FigureAsset(
            number=number,
            label=f"Figure {number}",
            caption=by_number[number][0],
            panels=tuple(
                FigurePanel(original_url=image_url)
                for image_url in by_number[number][1]
            ),
            source_url=by_number[number][2],
        )
        for number in sorted(by_number)
    )
    return FigureGallery(
        status="available" if figures else "not_found",
        html_url=html_url,
        checked_at=checked_at,
        figures=figures,
    )


def _safe_redirect(current_url: str, location: str, expected_path: str, exact: bool) -> str:
    if not location.strip():
        raise ValueError("arXiv redirect is missing a location")
    candidate = urljoin(current_url, location)
    if not _is_safe_arxiv_url(candidate, expected_path=expected_path, exact_path=exact):
        raise ValueError("arXiv redirect changed the paper identity")
    return candidate


class ArxivFigureClient:
    def __init__(
        self,
        *,
        config: FigureConfig,
        site_dir: Path,
        user_agent: str,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not user_agent.strip():
            raise ValueError("user agent must not be blank")
        self.config = config
        self.site_dir = site_dir
        self.user_agent = user_agent
        self.client = client or httpx.Client()
        self._owns_client = client is None
        self.sleep = sleep
        self.clock = clock
        self._last_request_at: float | None = None
        self._started_at = self.clock()
        self.request_count = 0
        self.image_attempts = 0
        self.total_downloaded_image_bytes = 0
        self.total_image_bytes = 0
        self.panel_cache_failures = 0

    def __enter__(self) -> ArxivFigureClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def _throttle(self) -> None:
        if self._last_request_at is not None:
            remaining = self.config.request_delay_seconds - (
                self.clock() - self._last_request_at
            )
            if remaining > 0:
                self.sleep(min(remaining, self._remaining_seconds()))
        self._last_request_at = self.clock()

    def _remaining_seconds(self) -> float:
        remaining = self.config.max_elapsed_seconds - (self.clock() - self._started_at)
        if remaining <= 0:
            raise FigureBudgetExceededError("figure extraction exceeded its time budget")
        return remaining

    def _request_bytes(
        self,
        url: str,
        *,
        expected_path: str,
        exact_path: bool,
        max_bytes: int,
        allowed_content_types: frozenset[str],
    ) -> tuple[bytes, str]:
        for attempt in range(1, self.config.retries + 1):
            current_url = url
            redirects = 0
            try:
                while True:
                    self._remaining_seconds()
                    self._throttle()
                    remaining = self._remaining_seconds()
                    self.request_count += 1
                    with self.client.stream(
                        "GET",
                        current_url,
                        headers={"User-Agent": self.user_agent},
                        timeout=min(self.config.timeout_seconds, remaining),
                        follow_redirects=False,
                    ) as response:
                        if 300 <= response.status_code < 400:
                            if redirects >= 3:
                                raise ValueError("arXiv redirect limit exceeded")
                            current_url = _safe_redirect(
                                current_url,
                                response.headers.get("location", ""),
                                expected_path,
                                exact_path,
                            )
                            redirects += 1
                            continue
                        if response.status_code == 404:
                            raise FigureUnavailableError
                        if response.status_code == 429 or response.status_code >= 500:
                            raise TransientFigureError(
                                f"arXiv returned {response.status_code}"
                            )
                        response.raise_for_status()
                        content_type = response.headers.get("content-type", "").split(";", 1)[
                            0
                        ].strip().casefold()
                        if content_type not in allowed_content_types:
                            raise FigureUnavailableError(
                                f"unsupported content type: {content_type or 'missing'}"
                            )
                        declared = int(response.headers.get("content-length", "0") or 0)
                        if declared > max_bytes:
                            raise ValueError("arXiv response exceeds configured size limit")
                        body = bytearray()
                        for chunk in response.iter_bytes():
                            body.extend(chunk)
                            if len(body) > max_bytes:
                                raise ValueError(
                                    "arXiv response exceeds configured size limit"
                                )
                        if not body:
                            raise ValueError("arXiv response is empty")
                        return bytes(body), content_type
            except (httpx.RequestError, TransientFigureError) as error:
                if attempt == self.config.retries:
                    raise TransientFigureError("arXiv request retries exhausted") from error
                wait_seconds = min(2 ** (attempt - 1), 4)
                remaining = self._remaining_seconds()
                self.sleep(min(wait_seconds, remaining))
        raise TransientFigureError("arXiv request retries exhausted")

    def fetch_gallery(self, arxiv_id: str, version: int, checked_at: datetime) -> FigureGallery:
        url = figure_html_url(arxiv_id, version)
        path = urlsplit(url).path
        try:
            body, _content_type = self._request_bytes(
                url,
                expected_path=path,
                exact_path=True,
                max_bytes=self.config.max_html_bytes,
                allowed_content_types=frozenset({"text/html", "application/xhtml+xml"}),
            )
            return parse_figure_gallery(body.decode("utf-8"), url, checked_at)
        except FigureUnavailableError:
            return FigureGallery(
                status="html_unavailable",
                html_url=url,
                checked_at=checked_at,
            )
        except (TransientFigureError, UnicodeError, ValueError):
            LOGGER.warning("论文图 HTML 获取失败 %sv%s", arxiv_id, version)
            return FigureGallery(status="fetch_failed", html_url=url, checked_at=checked_at)
        except Exception:
            LOGGER.exception("论文图 HTML 发生意外错误 %sv%s", arxiv_id, version)
            return FigureGallery(status="fetch_failed", html_url=url, checked_at=checked_at)

    def _validated_image(self, body: bytes, content_type: str) -> tuple[bytes, str]:
        extension = IMAGE_CONTENT_TYPES.get(content_type)
        if extension is None:
            raise FigureUnavailableError("unsupported figure image type")
        try:
            with Image.open(io.BytesIO(body)) as probe:
                probe.verify()
            with Image.open(io.BytesIO(body)) as image:
                width, height = image.size
                if width < 32 or height < 32:
                    raise ValueError("figure image is implausibly small")
                if width * height > self.config.max_image_pixels:
                    raise ValueError("figure image exceeds pixel limit")
                image.load()
                output = io.BytesIO()
                if extension == "jpg":
                    image.convert("RGB").save(output, format="JPEG", quality=95, optimize=True)
                elif extension == "webp":
                    image.save(output, format="WEBP", quality=95, method=4)
                else:
                    image.save(output, format="PNG", optimize=True)
        except (Image.DecompressionBombError, UnidentifiedImageError, OSError) as error:
            raise ValueError("figure image failed decoding") from error
        encoded = output.getvalue()
        if not encoded or len(encoded) > self.config.max_image_bytes:
            raise ValueError("validated figure image exceeds size limit")
        return encoded, extension

    def _install(self, relative_path: str, content: bytes) -> None:
        root = self.site_dir.resolve()
        target = self.site_dir / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        resolved_parent = target.parent.resolve()
        if not resolved_parent.is_relative_to(root) or target.is_symlink():
            raise ValueError("unsafe figure cache target")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.", dir=target.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                os.fchmod(stream.fileno(), 0o644)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def mirror_gallery(
        self,
        gallery: FigureGallery,
        *,
        arxiv_id: str,
        version: int,
        checked_at: datetime | None = None,
    ) -> FigureGallery:
        if gallery.status != "available":
            return gallery
        mirrored_figures: list[FigureAsset] = []
        for figure in gallery.figures[: self.config.max_figures_per_paper]:
            panels: list[FigurePanel] = []
            # The report renders one representative panel per Figure. Mirroring
            # additional panels would only inflate the Pages artifact.
            for panel_number, panel in enumerate(figure.panels[:1], start=1):
                image_path = urlsplit(panel.original_url).path
                try:
                    if self.image_attempts >= self.config.max_total_images:
                        raise FigureBudgetExceededError(
                            "figure extraction exceeded its image budget"
                        )
                    remaining_download_bytes = (
                        self.config.max_total_image_bytes
                        - self.total_downloaded_image_bytes
                    )
                    if remaining_download_bytes <= 0:
                        raise FigureBudgetExceededError(
                            "figure extraction exceeded its download byte budget"
                        )
                    self.image_attempts += 1
                    body, content_type = self._request_bytes(
                        panel.original_url,
                        expected_path=urlsplit(gallery.html_url).path,
                        exact_path=False,
                        max_bytes=min(
                            self.config.max_image_bytes,
                            remaining_download_bytes,
                        ),
                        allowed_content_types=frozenset(IMAGE_CONTENT_TYPES),
                    )
                    self.total_downloaded_image_bytes += len(body)
                    content, extension = self._validated_image(body, content_type)
                    if (
                        self.total_image_bytes + len(content)
                        > self.config.max_total_image_bytes
                    ):
                        raise FigureBudgetExceededError(
                            "figure extraction exceeded its artifact byte budget"
                        )
                    self._remaining_seconds()
                    relative_path = (
                        f"figures/arxiv/{arxiv_id}/v{version}/"
                        f"fig{figure.number}-panel{panel_number}.{extension}"
                    )
                    self._install(relative_path, content)
                    self.total_image_bytes += len(content)
                    panels.append(
                        FigurePanel(
                            original_url=panel.original_url,
                            cached_path=relative_path,
                        )
                    )
                except Exception as error:
                    self.panel_cache_failures += 1
                    LOGGER.warning("论文图面板缓存失败 %s: %s", image_path, error)
                    # A verified same-paper arXiv URL is still a valid display fallback.
                    panels.append(FigurePanel(original_url=panel.original_url))
            if panels:
                mirrored_figures.append(
                    FigureAsset(
                        number=figure.number,
                        label=figure.label,
                        caption=figure.caption,
                        panels=tuple(panels),
                        source_url=figure.source_url,
                        source=figure.source,
                    )
                )
        if not mirrored_figures:
            return FigureGallery(
                status="fetch_failed",
                html_url=gallery.html_url,
                checked_at=checked_at or gallery.checked_at,
            )
        return FigureGallery(
            status="available",
            html_url=gallery.html_url,
            checked_at=checked_at or gallery.checked_at,
            figures=tuple(mirrored_figures),
        )


def _load_cache(data_dir: Path) -> dict[str, FigureGallery]:
    payload = load_json(data_dir / "cache" / "figures.json", {"entries": {}})
    if not isinstance(payload, dict) or not isinstance(payload.get("entries", {}), dict):
        raise ValueError("figure cache must contain an entries object")
    cache: dict[str, FigureGallery] = {}
    adapter = TypeAdapter(FigureGallery)
    for key, value in payload.get("entries", {}).items():
        try:
            cache[str(key)] = adapter.validate_python(value)
        except ValidationError:
            LOGGER.warning("忽略损坏的论文图缓存项 %s", key)
    return cache


def _persist_cache(data_dir: Path, cache: dict[str, FigureGallery]) -> None:
    atomic_write_json(
        data_dir / "cache" / "figures.json",
        {
            "schema_version": 1,
            "entries": {
                key: value.model_dump(mode="json") for key, value in sorted(cache.items())
            },
        },
    )


def _cache_is_fresh(
    gallery: FigureGallery,
    *,
    now: datetime,
    site_dir: Path,
    negative_hours: int,
) -> bool:
    if gallery.status == "available":
        has_cached_panel = False
        has_uncached_panel = False
        for figure in gallery.figures:
            for panel in figure.panels:
                if panel.cached_path is None:
                    has_uncached_panel = True
                    continue
                has_cached_panel = True
                target = site_dir / panel.cached_path
                try:
                    if not target.is_file() or target.is_symlink() or target.stat().st_size == 0:
                        return False
                except OSError:
                    return False
        if has_cached_panel and not has_uncached_panel:
            return True
        age = now - gallery.checked_at
        return -NEGATIVE_CLOCK_SKEW <= age < timedelta(hours=negative_hours)
    age = now - gallery.checked_at
    return -NEGATIVE_CLOCK_SKEW <= age < timedelta(hours=negative_hours)


def enrich_papers_with_figures(
    papers: list[AnalyzedPaper],
    *,
    config: FigureConfig,
    data_dir: Path,
    site_dir: Path,
    checked_at: datetime,
    user_agent: str,
) -> tuple[list[AnalyzedPaper], FigureRunStats]:
    """Attach cached arXiv figures to selected papers without blocking publication."""
    if not config.enabled or config.max_papers == 0 or not papers:
        return papers, FigureRunStats()

    cache = _load_cache(data_dir)
    updated: list[AnalyzedPaper] = []
    cache_hits = 0
    available = 0
    failed = 0

    with ArxivFigureClient(
        config=config,
        site_dir=site_dir,
        user_agent=user_agent,
    ) as client:
        for index, analyzed in enumerate(papers):
            paper = analyzed.ranked.paper
            gallery: FigureGallery | None = None
            if index < config.max_papers and paper.source == "arxiv":
                cached = cache.get(paper.identity)
                if cached is not None and cached.status == "available":
                    cache_hits += 1
                    gallery = cached
                    if not _cache_is_fresh(
                        cached,
                        now=checked_at,
                        site_dir=site_dir,
                        negative_hours=config.negative_cache_hours,
                    ):
                        # Versioned arXiv metadata is immutable. A fresh runner only
                        # needs to restore the ephemeral Pages assets, not refetch HTML.
                        gallery = client.mirror_gallery(
                            cached,
                            arxiv_id=paper.canonical_id,
                            version=paper.version,
                            checked_at=checked_at,
                        )
                        cache[paper.identity] = gallery
                elif cached is not None and _cache_is_fresh(
                    cached,
                    now=checked_at,
                    site_dir=site_dir,
                    negative_hours=config.negative_cache_hours,
                ):
                    gallery = cached
                    cache_hits += 1
                else:
                    gallery = client.fetch_gallery(
                        paper.canonical_id,
                        paper.version,
                        checked_at,
                    )
                    gallery = client.mirror_gallery(
                        gallery,
                        arxiv_id=paper.canonical_id,
                        version=paper.version,
                        checked_at=checked_at,
                    )
                    cache[paper.identity] = gallery
                if gallery.status == "available":
                    available += 1
                elif gallery.status == "fetch_failed":
                    failed += 1

            payload = analyzed.model_dump(mode="python")
            payload["figure_gallery"] = gallery
            updated.append(AnalyzedPaper.model_validate(payload))

        requests = client.request_count
        panel_failed = client.panel_cache_failures

    _persist_cache(data_dir, cache)
    return updated, FigureRunStats(
        cache_hits=cache_hits,
        requests=requests,
        available=available,
        failed=failed,
        panel_failed=panel_failed,
    )
