"""Application service composition container."""

from __future__ import annotations

from dataclasses import dataclass

from ..bridge import OneNoteBridge
from .hierarchy import HierarchyService
from .mutations import MutationService
from .operations import OperationsService
from .pages import PageService
from .search import SearchService


@dataclass(frozen=True)
class ServiceContainer:
    hierarchy: HierarchyService
    pages: PageService
    search: SearchService
    mutations: MutationService
    operations: OperationsService

    @classmethod
    def build(cls, bridge: OneNoteBridge, *, max_text_chars: int) -> "ServiceContainer":
        hierarchy = HierarchyService(bridge)
        pages = PageService(bridge, hierarchy, max_text_chars)
        search = SearchService(bridge, hierarchy, pages)
        mutations = MutationService(bridge, hierarchy, pages)
        operations = OperationsService(bridge, hierarchy, mutations)
        return cls(
            hierarchy=hierarchy,
            pages=pages,
            search=search,
            mutations=mutations,
            operations=operations,
        )
