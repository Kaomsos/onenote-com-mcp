"""Bounded Page text and OneNote index search service."""

from __future__ import annotations

import time
from typing import Any

from ..bridge import OneNoteBridge
from ..constants import XML_SCHEMA_2013
from ..hierarchy import display_name, filter_resources, find_resource_by_id, parse_hierarchy
from ..page import text_from_page_xml
from ..policy import SearchBudget
from .base import BaseService
from .hierarchy import HierarchyService
from .pages import PageService


class SearchService(BaseService):
    def __init__(self, bridge: OneNoteBridge, hierarchy: HierarchyService, pages: PageService) -> None:
        super().__init__(bridge)
        self.hierarchy = hierarchy
        self.pages = pages

    def local_text_search(
        self,
        start_id: str,
        query: str,
        max_results: int,
        include_recycle_bin: bool,
        budget: SearchBudget | None = None,
        include_snippets: bool = True,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        budget = budget or SearchBudget.current()
        items = self.hierarchy.resources(include_recycle_bin)
        pages = filter_resources(items, "page")
        if start_id:
            root = find_resource_by_id(items, start_id)
            if root is None:
                raise ValueError(f"No search scope found for ID '{start_id}'.")
            prefix = root["path"] + "/"
            pages = [page for page in pages if page["id"] == start_id or page["path"].startswith(prefix)]
        if len(pages) > budget.max_pages:
            raise ValueError(
                f"Search scope contains {len(pages)} candidate pages, exceeding "
                f"LOCAL_ONENOTE_MAX_SEARCH_PAGES={budget.max_pages}."
            )
        query_lower = query.casefold()
        matches = []
        total_chars = 0
        scanned_pages = 0
        started = time.monotonic()
        for page in pages:
            if len(matches) >= max(1, max_results):
                break
            if time.monotonic() - started > budget.max_seconds:
                raise RuntimeError(f"Local search exceeded its {budget.max_seconds}-second budget.")
            haystacks = [display_name(page), page.get("path", "")]
            try:
                page_text = text_from_page_xml(self.pages.xml(page["id"], "basic"))
                scanned_pages += 1
                if len(page_text) > budget.max_page_chars:
                    page_text = page_text[: budget.max_page_chars]
                total_chars += len(page_text)
                if total_chars > budget.max_total_chars:
                    raise RuntimeError(
                        f"Local search exceeded LOCAL_ONENOTE_MAX_SEARCH_TOTAL_CHARS={budget.max_total_chars}."
                    )
                haystacks.append(page_text)
            except Exception as exc:
                if isinstance(exc, RuntimeError):
                    raise
                page["scan_error"] = str(exc)
            if any(query_lower in value.casefold() for value in haystacks if value):
                if include_snippets and len(haystacks) > 2:
                    text = haystacks[-1]
                    index = text.casefold().find(query_lower)
                    if index >= 0:
                        radius = max(40, budget.snippet_chars // 2)
                        page["snippet"] = text[
                            max(0, index - radius) : index + len(query) + radius
                        ].strip()
                matches.append(page)
        return matches, {
            "candidate_pages": len(pages),
            "scanned_pages": scanned_pages,
            "scanned_chars": total_chars,
            "max_pages": budget.max_pages,
            "max_page_chars": budget.max_page_chars,
            "max_total_chars": budget.max_total_chars,
            "max_seconds": budget.max_seconds,
        }

    def search(
        self,
        query: str,
        scope_type: str,
        scope_id: str,
        backend: str = "local_scan",
        max_results: int = 20,
        include_snippets: bool = True,
        include_recycle_bin: bool = False,
    ) -> dict[str, Any]:
        if not query.strip():
            raise ValueError("query is required.")
        normalized_scope = scope_type.strip().casefold()
        if normalized_scope not in {"notebook", "section_group", "section"}:
            raise ValueError("scope_type must be one of: notebook, section_group, section.")
        scope = self.hierarchy.resource(scope_id, normalized_scope)
        normalized_backend = backend.strip().casefold()
        budget_data: dict[str, Any] | None = None
        if normalized_backend == "local_scan":
            pages, budget_data = self.local_text_search(
                scope["id"], query, max_results, include_recycle_bin, include_snippets=include_snippets
            )
        elif normalized_backend == "onenote_index":
            xml = self.call(
                "find_pages",
                start_id=scope["id"],
                query=query,
                include_unindexed=False,
                display=False,
                schema=XML_SCHEMA_2013,
            )["xml"]
            catalog = self.hierarchy.resources(include_recycle_bin=True)
            pages = filter_resources(parse_hierarchy(xml, catalog=catalog), "page")
        else:
            raise ValueError("backend must be one of: local_scan, onenote_index.")
        if not include_recycle_bin:
            pages = self.hierarchy.without_recycle_bin(pages)
        pages = pages[: max(1, max_results)]
        if include_snippets and normalized_backend == "onenote_index":
            query_lower = query.casefold()
            for page in pages:
                try:
                    text = text_from_page_xml(self.pages.xml(page["id"], "basic"))
                    index = text.casefold().find(query_lower)
                    if index >= 0:
                        start = max(0, index - 160)
                        end = min(len(text), index + len(query) + SearchBudget.current().snippet_chars)
                        page["snippet"] = text[start:end].strip()
                except Exception as exc:
                    page["snippet_error"] = str(exc)
        return {
            "pages": pages,
            "count": len(pages),
            "scope": scope,
            "search_backend": normalized_backend,
            "scan_budget": budget_data,
        }

    def find_meta(self, start_identifier: str, name: str, include_unindexed: bool = True) -> dict[str, Any]:
        start_id = self.hierarchy.resolve(start_identifier)["id"] if start_identifier else ""
        xml = self.call(
            "find_meta",
            start_id=start_id,
            name=name,
            include_unindexed=include_unindexed,
            schema=XML_SCHEMA_2013,
        )["xml"]
        items = parse_hierarchy(xml, catalog=self.hierarchy.resources(include_recycle_bin=True))
        return {"items": items, "count": len(items), "xml": xml}
