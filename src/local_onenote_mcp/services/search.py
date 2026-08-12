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


SEARCH_BACKEND = "onenote_index"
SEARCH_SCOPE_MODES = ("root", "start_node")
DEFAULT_SEARCH_PAGE_SIZE = 200
MAX_SEARCH_PAGE_SIZE = 200
PAGINATION_CONSISTENCY = "live_index"
START_NODE_TYPES = {"notebook", "section_group", "section"}


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
        catalog: list[dict[str, Any]] | None = None,
        notebook_ids: set[str] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        budget = budget or SearchBudget.current()
        items = catalog if catalog is not None else self.hierarchy.resources(include_recycle_bin=True)
        if not include_recycle_bin:
            items = self.hierarchy.without_recycle_bin(items)
        pages = filter_resources(items, "page")
        if notebook_ids is not None:
            pages = [page for page in pages if page.get("notebook_id") in notebook_ids]
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
                if time.monotonic() - started > budget.max_seconds:
                    raise RuntimeError(
                        f"Local search exceeded its {budget.max_seconds}-second budget."
                    )
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
        scope_request: dict[str, Any],
        offset: int = 0,
        page_size: int = DEFAULT_SEARCH_PAGE_SIZE,
        include_snippets: bool = True,
        include_recycle_bin: bool = False,
    ) -> dict[str, Any]:
        if not query.strip():
            raise ValueError("query is required.")
        if offset < 0:
            raise ValueError("offset must be greater than or equal to 0.")
        if page_size < 1 or page_size > MAX_SEARCH_PAGE_SIZE:
            raise ValueError(
                f"page_size must be between 1 and {MAX_SEARCH_PAGE_SIZE}."
            )
        if not isinstance(scope_request, dict):
            raise ValueError("scope must be an object.")
        mode = str(scope_request.get("mode", "")).strip().casefold()
        if mode not in SEARCH_SCOPE_MODES:
            raise ValueError("scope.mode must be one of: root, start_node.")

        catalog = self.hierarchy.resources(include_recycle_bin=True)
        by_id = {str(item.get("id", "")): item for item in catalog if item.get("id")}
        open_notebooks = [
            item
            for item in catalog
            if item.get("resource_type") == "notebook"
            and item.get("is_open") is not False
            and item.get("is_in_recycle_bin") is not True
        ]
        open_notebook_ids = {str(item["id"]) for item in open_notebooks}
        allowed_page_ids: set[str]
        if mode == "root":
            if set(scope_request) != {"mode"}:
                raise ValueError("root scope does not accept additional fields.")
            scope = {
                "resource_type": "root",
                "notebook_count": len(open_notebooks),
            }
            start_id = ""
            allowed_page_ids = {
                str(item["id"])
                for item in catalog
                if item.get("resource_type") == "page"
                and str(item.get("notebook_id", "")) in open_notebook_ids
                and (include_recycle_bin or item.get("is_in_recycle_bin") is not True)
            }
        else:
            if set(scope_request) != {"mode", "start_node_id"}:
                raise ValueError(
                    "start_node scope requires only mode and start_node_id."
                )
            start_node_id = str(scope_request.get("start_node_id", "")).strip()
            if not start_node_id:
                raise ValueError("scope.start_node_id is required for start_node scope.")
            start_node = by_id.get(start_node_id)
            if start_node is None:
                raise ValueError(f"No hierarchy object found for ID '{start_node_id}'.")
            resource_type = str(start_node.get("resource_type", ""))
            if resource_type not in START_NODE_TYPES:
                raise ValueError(
                    "scope.start_node_id must identify a notebook, section_group, or section."
                )
            notebook_id = (
                start_node_id
                if resource_type == "notebook"
                else str(start_node.get("notebook_id", ""))
            )
            if notebook_id not in open_notebook_ids:
                raise ValueError("scope.start_node_id must belong to an open Notebook.")
            if start_node.get("is_in_recycle_bin") is True and not include_recycle_bin:
                raise ValueError(
                    "scope.start_node_id is in the recycle bin; set include_recycle_bin=true to use it."
                )
            scope = dict(start_node)
            start_id = start_node_id

            def belongs_to_start_node(page: dict[str, Any]) -> bool:
                if str(page.get("notebook_id", "")) != notebook_id:
                    return False
                if resource_type == "notebook":
                    return True
                current_id = str(page.get("section_id", ""))
                visited: set[str] = set()
                while current_id and current_id not in visited:
                    if current_id == start_node_id:
                        return True
                    visited.add(current_id)
                    current = by_id.get(current_id)
                    current_id = str(current.get("parent_id", "")) if current else ""
                return False

            allowed_page_ids = {
                str(item["id"])
                for item in catalog
                if item.get("resource_type") == "page"
                and belongs_to_start_node(item)
                and (include_recycle_bin or item.get("is_in_recycle_bin") is not True)
            }

        budget = SearchBudget.current()
        started = time.monotonic()

        def elapsed() -> float:
            return time.monotonic() - started

        def remaining_seconds(label: str) -> float:
            remaining = float(budget.max_seconds) - elapsed()
            if remaining <= 0:
                raise RuntimeError(
                    f"OneNote index search exceeded its {budget.max_seconds}-second budget during {label}."
                )
            return remaining

        if mode == "root" and not open_notebooks:
            candidates: list[dict[str, Any]] = []
        else:
            xml = self.call(
                "find_pages",
                _timeout_seconds=remaining_seconds("FindPages"),
                start_id=start_id,
                query=query,
                include_unindexed=False,
                display=False,
                schema=XML_SCHEMA_2013,
            )["xml"]
            remaining_seconds("FindPages result processing")
            parsed_pages = filter_resources(parse_hierarchy(xml, catalog=catalog), "page")
            candidates = []
            seen_page_ids: set[str] = set()
            for page in parsed_pages:
                page_id = str(page.get("id", ""))
                if (
                    not page_id
                    or page_id in seen_page_ids
                    or page_id not in allowed_page_ids
                ):
                    continue
                seen_page_ids.add(page_id)
                candidates.append(page)
            remaining_seconds("FindPages result processing")

        candidate_pages = len(candidates)
        if candidate_pages > budget.max_pages:
            raise ValueError(
                f"OneNote index returned {candidate_pages} candidate pages, exceeding "
                f"LOCAL_ONENOTE_MAX_SEARCH_PAGES={budget.max_pages}."
            )
        pages = candidates[offset : offset + page_size]
        hydrated_pages = 0
        hydrated_chars = 0
        if include_snippets:
            query_lower = query.casefold()
            for page in pages:
                page_timeout = remaining_seconds("snippet hydration")
                try:
                    text = text_from_page_xml(
                        self.pages.xml(
                            page["id"],
                            "basic",
                            _timeout_seconds=page_timeout,
                        )
                    )
                    hydrated_pages += 1
                    remaining_seconds("snippet hydration")
                    if len(text) > budget.max_page_chars:
                        text = text[: budget.max_page_chars]
                    hydrated_chars += len(text)
                    if hydrated_chars > budget.max_total_chars:
                        raise RuntimeError(
                            "OneNote index snippet hydration exceeded "
                            f"LOCAL_ONENOTE_MAX_SEARCH_TOTAL_CHARS={budget.max_total_chars}."
                        )
                    index = text.casefold().find(query_lower)
                    if index >= 0:
                        radius = max(40, budget.snippet_chars // 2)
                        page["snippet"] = text[
                            max(0, index - radius) : index + len(query) + radius
                        ][: budget.snippet_chars].strip()
                except Exception as exc:
                    if isinstance(exc, RuntimeError):
                        raise
                    page["snippet_error"] = str(exc)
        total_elapsed = elapsed()
        if total_elapsed > budget.max_seconds:
            raise RuntimeError(
                f"OneNote index search exceeded its {budget.max_seconds}-second budget."
            )
        has_more = offset + len(pages) < candidate_pages
        return {
            "pages": pages,
            "count": len(pages),
            "total_matches": candidate_pages,
            "offset": offset,
            "page_size": page_size,
            "has_more": has_more,
            "next_offset": offset + len(pages) if has_more else None,
            "pagination_consistency": PAGINATION_CONSISTENCY,
            "scope": scope,
            "search_backend": SEARCH_BACKEND,
            "scan_budget": {
                "candidate_pages": candidate_pages,
                "hydrated_pages": hydrated_pages,
                "hydrated_chars": hydrated_chars,
                "elapsed_seconds": round(total_elapsed, 6),
                "max_pages": budget.max_pages,
                "max_page_chars": budget.max_page_chars,
                "max_total_chars": budget.max_total_chars,
                "max_seconds": budget.max_seconds,
                "snippet_chars": budget.snippet_chars,
            },
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
