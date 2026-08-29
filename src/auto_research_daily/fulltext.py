from __future__ import annotations

from html.parser import HTMLParser

import httpx

from auto_research_daily.models import RawPaper


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "nav", "header", "footer", "noscript"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "nav", "header", "footer", "noscript"}:
            self._skip_depth = max(0, self._skip_depth - 1)

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            cleaned = " ".join(data.split())
            if cleaned:
                self.parts.append(cleaned)


def select_evidence_excerpt(text: str, max_chars: int) -> str:
    """Keep broad section coverage instead of blindly truncating the paper introduction."""
    if len(text) <= max_chars:
        return text
    lower = text.casefold()
    segments: list[tuple[int, int]] = [(0, max_chars // 4)]
    keywords = (
        "method",
        "approach",
        "architecture",
        "experiment",
        "evaluation",
        "results",
        "limitation",
        "conclusion",
    )
    window = max(1200, max_chars // max(4, len(keywords)))
    for keyword in keywords:
        position = lower.find(keyword, 500)
        if position < 0:
            continue
        start = max(0, position - window // 5)
        end = min(len(text), start + window)
        overlaps = any(
            start < existing_end and end > existing_start
            for existing_start, existing_end in segments
        )
        if overlaps:
            continue
        segments.append((start, end))
    segments.sort()
    excerpt = "\n\n".join(text[start:end] for start, end in segments)
    return excerpt[:max_chars]


def fetch_arxiv_html(
    paper: RawPaper,
    *,
    max_chars: int,
    client: httpx.Client | None = None,
) -> str | None:
    if paper.source != "arxiv":
        return None
    owns_client = client is None
    http = client or httpx.Client(timeout=45, follow_redirects=True)
    try:
        response = http.get(f"https://arxiv.org/html/{paper.canonical_id}")
        if response.status_code != 200 or "html" not in response.headers.get("content-type", ""):
            return None
        parser = _VisibleTextParser()
        parser.feed(response.text)
        text = "\n".join(parser.parts)
        return select_evidence_excerpt(text, max_chars) if len(text) >= 500 else None
    except (httpx.HTTPError, UnicodeError):
        return None
    finally:
        if owns_client:
            http.close()
