from __future__ import annotations

import re
import time
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any
from xml.etree import ElementTree

import httpx

from auto_research_daily.config import ArxivSourceConfig
from auto_research_daily.models import RawPaper

OAI_URL = "https://oaipmh.arxiv.org/oai"
OAI_NS = "http://www.openarchives.org/OAI/2.0/"
ARXIV_RAW_NS = "http://arxiv.org/OAI/arXivRaw/"
AUTHOR_SEPARATOR_RE = re.compile(r"\s*(?:,|\band\b)\s*", re.IGNORECASE)


def _required_text(parent: ElementTree.Element, name: str) -> str:
    element = parent.find(f"{{{ARXIV_RAW_NS}}}{name}")
    if element is None or not element.text or not element.text.strip():
        raise ValueError(f"arXiv OAI record is missing {name}")
    return " ".join(element.text.split())


def _parse_version_date(value: str) -> datetime:
    try:
        parsed = parsedate_to_datetime(value.strip())
    except (TypeError, ValueError):
        normalized = value.strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("arXiv version timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


class ArxivSource:
    """Fetch recent arXiv metadata using the OAI-PMH incremental feed."""

    def __init__(
        self,
        config: ArxivSourceConfig,
        *,
        user_agent: str = "auto-research-daily/0.1 (research paper discovery)",
        client: httpx.Client | None = None,
    ) -> None:
        self.config = config
        self._owns_client = client is None
        self.client = client or httpx.Client(
            timeout=config.timeout_seconds,
            headers={"User-Agent": user_agent},
            follow_redirects=True,
        )

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> ArxivSource:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _request(self, params: dict[str, Any]) -> bytes:
        last_error: Exception | None = None
        for attempt in range(self.config.retries):
            try:
                response = self.client.get(OAI_URL, params=params)
                response.raise_for_status()
                return response.content
            except (httpx.HTTPError, TimeoutError) as error:
                last_error = error
                if attempt + 1 < self.config.retries:
                    time.sleep(min(2**attempt, 16))
        raise RuntimeError("arXiv request exhausted all retries") from last_error

    @staticmethod
    def parse_page(
        xml: bytes,
        *,
        categories: frozenset[str],
        since: datetime,
        until: datetime,
    ) -> tuple[list[RawPaper], str | None]:
        if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
            raise ValueError("forbidden XML declaration in arXiv response")
        try:
            root = ElementTree.fromstring(xml)
        except ElementTree.ParseError as error:
            raise ValueError("invalid arXiv OAI XML") from error
        if root.tag != f"{{{OAI_NS}}}OAI-PMH":
            raise ValueError("invalid arXiv OAI root")

        error_element = root.find(f"{{{OAI_NS}}}error")
        if error_element is not None:
            if error_element.attrib.get("code") == "noRecordsMatch":
                return [], None
            raise ValueError(f"arXiv OAI error: {error_element.attrib.get('code')}")

        records = root.find(f"{{{OAI_NS}}}ListRecords")
        if records is None:
            raise ValueError("arXiv OAI response has no ListRecords")

        papers: list[RawPaper] = []
        for record in records.findall(f"{{{OAI_NS}}}record"):
            header = record.find(f"{{{OAI_NS}}}header")
            if header is not None and header.attrib.get("status") == "deleted":
                continue
            metadata = record.find(f"{{{OAI_NS}}}metadata")
            raw = None if metadata is None else metadata.find(f"{{{ARXIV_RAW_NS}}}arXivRaw")
            if raw is None:
                raise ValueError("arXiv OAI record has no arXivRaw metadata")

            paper_categories = tuple(dict.fromkeys(_required_text(raw, "categories").split()))
            if categories.isdisjoint(paper_categories):
                continue
            versions: dict[int, datetime] = {}
            for version_element in raw.findall(f"{{{ARXIV_RAW_NS}}}version"):
                version_name = version_element.attrib.get("version", "")
                if not re.fullmatch(r"v[1-9]\d*", version_name):
                    raise ValueError("invalid arXiv version")
                date_element = version_element.find(f"{{{ARXIV_RAW_NS}}}date")
                if date_element is None or not date_element.text:
                    raise ValueError("arXiv version is missing date")
                version_number = int(version_name[1:])
                if version_number in versions:
                    raise ValueError("duplicate arXiv version")
                versions[version_number] = _parse_version_date(date_element.text)
            if not versions or 1 not in versions:
                raise ValueError("arXiv record has no v1")
            latest_version = max(versions)
            if set(versions) != set(range(1, latest_version + 1)):
                raise ValueError("arXiv versions must be contiguous")
            updated_at = versions[latest_version]
            if not since <= updated_at <= until:
                continue

            arxiv_id = _required_text(raw, "id")
            authors = tuple(
                dict.fromkeys(
                    author.strip()
                    for author in AUTHOR_SEPARATOR_RE.split(_required_text(raw, "authors"))
                    if author.strip()
                )
            )
            comment_element = raw.find(f"{{{ARXIV_RAW_NS}}}comments")
            comment = (
                " ".join(comment_element.text.split())
                if comment_element is not None and comment_element.text
                else None
            )
            papers.append(
                RawPaper(
                    canonical_id=arxiv_id,
                    version=latest_version,
                    published_at=versions[1],
                    updated_at=updated_at,
                    title=_required_text(raw, "title"),
                    authors=authors,
                    categories=paper_categories,
                    abstract=_required_text(raw, "abstract"),
                    comment=comment,
                    url=f"https://arxiv.org/abs/{arxiv_id}",
                    pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
                )
            )

        token_element = records.find(f"{{{OAI_NS}}}resumptionToken")
        token = (
            token_element.text.strip()
            if token_element is not None and token_element.text
            else None
        )
        return papers, token

    def fetch_recent(
        self,
        *,
        now: datetime | None = None,
        lookback_days: int | None = None,
    ) -> list[RawPaper]:
        until = (now or datetime.now(UTC)).astimezone(UTC)
        since = until - timedelta(days=lookback_days or self.config.lookback_days)
        target_categories = frozenset(self.config.categories)
        archives = tuple(
            dict.fromkeys(category.partition(".")[0] for category in target_categories)
        )
        found: dict[str, RawPaper] = {}

        for archive_index, archive in enumerate(archives):
            params: dict[str, Any] = {
                "verb": "ListRecords",
                "metadataPrefix": "arXivRaw",
                "from": since.date().isoformat(),
                "until": until.date().isoformat(),
                "set": archive,
            }
            seen_tokens: set[str] = set()
            while True:
                if archive_index or seen_tokens:
                    time.sleep(self.config.request_delay_seconds)
                page, token = self.parse_page(
                    self._request(params),
                    categories=target_categories,
                    since=since,
                    until=until,
                )
                for paper in page:
                    previous = found.get(paper.canonical_id)
                    if previous is None or paper.version > previous.version:
                        found[paper.canonical_id] = paper
                if len(found) >= self.config.max_records:
                    break
                if token is None:
                    break
                if token in seen_tokens:
                    raise ValueError("arXiv OAI repeated a resumption token")
                seen_tokens.add(token)
                params = {"verb": "ListRecords", "resumptionToken": token}

        return sorted(
            found.values(),
            key=lambda paper: (-paper.updated_at.timestamp(), paper.canonical_id),
        )[: self.config.max_records]
