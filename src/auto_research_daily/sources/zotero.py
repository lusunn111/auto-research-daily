from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from auto_research_daily.config import ZoteroSourceConfig
from auto_research_daily.models import ZoteroDocument

ZOTERO_API = "https://api.zotero.org"
SUPPORTED_TYPES = {"journalArticle", "conferencePaper", "preprint", "report", "thesis"}


class ZoteroSource:
    """Load a private Zotero library as a transient personal-interest corpus."""

    def __init__(
        self,
        config: ZoteroSourceConfig,
        *,
        user_id: str,
        api_key: str,
        client: httpx.Client | None = None,
    ) -> None:
        self.config = config
        self.user_id = user_id
        self._owns_client = client is None
        self.client = client or httpx.Client(
            base_url=ZOTERO_API,
            timeout=45,
            headers={"Zotero-API-Key": api_key, "Zotero-API-Version": "3"},
        )

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> ZoteroSource:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _collection_names(self) -> dict[str, str]:
        response = self.client.get(f"/users/{self.user_id}/collections", params={"limit": 100})
        response.raise_for_status()
        return {
            str(item["key"]): str(item.get("data", {}).get("name", item["key"]))
            for item in response.json()
        }

    @staticmethod
    def _date_added(value: Any) -> datetime:
        if not isinstance(value, str):
            return datetime.now(UTC)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)

    def fetch_documents(self) -> list[ZoteroDocument]:
        collections = self._collection_names()
        documents: list[ZoteroDocument] = []
        start = 0
        while len(documents) < self.config.max_items:
            response = self.client.get(
                f"/users/{self.user_id}/items/top",
                params={
                    "format": "json",
                    "limit": 100,
                    "start": start,
                    "sort": "dateAdded",
                    "direction": "desc",
                },
            )
            response.raise_for_status()
            page = response.json()
            if not isinstance(page, list):
                raise ValueError("unexpected Zotero response")
            if not page:
                break
            for item in page:
                data = item.get("data", {})
                if data.get("itemType") not in SUPPORTED_TYPES:
                    continue
                title = str(data.get("title", "")).strip()
                abstract = str(data.get("abstractNote", "")).strip()
                if not title or not abstract:
                    continue
                collection_names = tuple(
                    collections.get(str(key), str(key)) for key in data.get("collections", [])
                )
                if self.config.include_collections and not set(collection_names).intersection(
                    self.config.include_collections
                ):
                    continue
                if set(collection_names).intersection(self.config.exclude_collections):
                    continue
                documents.append(
                    ZoteroDocument(
                        key=str(item["key"]),
                        title=title,
                        abstract=abstract,
                        date_added=self._date_added(data.get("dateAdded")),
                        collections=collection_names,
                    )
                )
                if len(documents) >= self.config.max_items:
                    break
            start += len(page)
        return documents
