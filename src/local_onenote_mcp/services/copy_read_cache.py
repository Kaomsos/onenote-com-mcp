"""Operation-local phase snapshots for Copy/Move readback efficiency."""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

from ..hierarchy import parse_hierarchy
from ..page import page_equivalence, page_visible_text_equivalence, title_from_page_xml
from .backend_operation_classification import current_mutation_epoch
from .hierarchy import HierarchyService, HierarchySnapshot
from .pages import PageService, stable_page_content_digest
from .read_reasons import read_reason

__all__ = [
    "CopyReadCache",
    "HierarchySnapshot",
    "PageContentDerivation",
    "current_copy_read_cache",
    "restore_copy_read_cache",
    "set_copy_read_cache",
]

_CURRENT_COPY_READ_CACHE: ContextVar[CopyReadCache | None] = ContextVar(
    "local_onenote_copy_read_cache",
    default=None,
)


def current_copy_read_cache() -> CopyReadCache | None:
    return _CURRENT_COPY_READ_CACHE.get()


def set_copy_read_cache(cache: CopyReadCache | None) -> Token:
    return _CURRENT_COPY_READ_CACHE.set(cache)


def restore_copy_read_cache(token: Token) -> None:
    _CURRENT_COPY_READ_CACHE.reset(token)


@dataclass(frozen=True)
class PageContentDerivation:
    """Memory projection from one Page live observation."""

    page_id: str
    scope: str
    epoch: int
    xml: str
    digest: str

    def title(self) -> str | None:
        return title_from_page_xml(self.xml)

    def equivalence_against(
        self,
        transformed_xml: str,
        *,
        verification_tier: str,
    ) -> dict[str, Any]:
        return page_equivalence(
            transformed_xml,
            self.xml,
            verification_tier=verification_tier,
        )

    def visible_text_equivalence_against(
        self,
        expected_xml: str,
    ) -> dict[str, Any]:
        return page_visible_text_equivalence(expected_xml, self.xml)


class CopyReadCache:
    """Task-local Copy/Move cache; discarded when the public tool call ends."""

    def __init__(self, hierarchy: HierarchyService, pages: PageService) -> None:
        self._hierarchy = hierarchy
        self._pages = pages
        self._hierarchy_entries: dict[tuple[str, str], tuple[int, HierarchySnapshot]] = {}
        self._page_entries: dict[tuple[str, str], tuple[int, PageContentDerivation]] = {}

    def get_hierarchy_snapshot(
        self,
        *,
        reason: str,
        start_id: str = "",
        scope: str = "pages",
    ) -> HierarchySnapshot:
        key = (start_id, scope)
        epoch = current_mutation_epoch()
        cached = self._hierarchy_entries.get(key)
        if cached is not None and cached[0] == epoch:
            return cached[1]
        with read_reason(reason):
            xml = self._hierarchy.hierarchy_xml(start_id, scope)
        snapshot = HierarchySnapshot.from_items(
            start_id=start_id,
            scope=scope,
            epoch=epoch,
            items=parse_hierarchy(xml),
        )
        self._hierarchy_entries[key] = (epoch, snapshot)
        return snapshot

    def resources(
        self,
        *,
        reason: str,
        include_recycle_bin: bool = False,
        start_id: str = "",
        scope: str = "pages",
    ) -> list[dict[str, Any]]:
        return self.get_hierarchy_snapshot(
            reason=reason,
            start_id=start_id,
            scope=scope,
        ).resources(include_recycle_bin)

    def resource(
        self,
        object_id: str,
        resource_type: str | None = None,
        *,
        reason: str,
        start_id: str = "",
        scope: str = "pages",
    ) -> dict[str, Any]:
        return self.get_hierarchy_snapshot(
            reason=reason,
            start_id=start_id,
            scope=scope,
        ).resource(object_id, resource_type)

    def get_page_derivation(
        self,
        page_id: str,
        scope: str = "all",
        *,
        reason: str,
    ) -> PageContentDerivation:
        key = (page_id, scope)
        epoch = current_mutation_epoch()
        cached = self._page_entries.get(key)
        if cached is not None and cached[0] == epoch:
            return cached[1]
        with read_reason(reason):
            xml = self._pages.xml(page_id, scope)
        derivation = PageContentDerivation(
            page_id=page_id,
            scope=scope,
            epoch=epoch,
            xml=xml,
            digest=stable_page_content_digest(xml),
        )
        self._page_entries[key] = (epoch, derivation)
        return derivation

    def page_xml(self, page_id: str, scope: str = "all", *, reason: str) -> str:
        return self.get_page_derivation(page_id, scope, reason=reason).xml
