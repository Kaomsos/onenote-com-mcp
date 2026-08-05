"""Service-level policy for OneNote mutations and bounded local search."""

from __future__ import annotations

import os
from dataclasses import dataclass


TRUE_VALUES = {"1", "true", "yes", "on"}


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    return default if value is None else value.strip().casefold() in TRUE_VALUES


def env_int(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.environ.get(name, str(default))))
    except ValueError:
        return default


@dataclass(frozen=True)
class MutationPolicy:
    writes_enabled: bool
    deletes_enabled: bool
    permanent_deletes_enabled: bool
    experimental_move_section_enabled: bool
    experimental_copy_enabled: bool
    reconstructive_move_page_enabled: bool
    raw_xml_enabled: bool

    @classmethod
    def current(cls) -> "MutationPolicy":
        return cls(
            writes_enabled=env_bool("LOCAL_ONENOTE_ENABLE_WRITES"),
            deletes_enabled=env_bool("LOCAL_ONENOTE_ENABLE_DELETES"),
            permanent_deletes_enabled=env_bool("LOCAL_ONENOTE_ENABLE_PERMANENT_DELETES"),
            experimental_move_section_enabled=env_bool("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_MOVE_SECTION"),
            experimental_copy_enabled=env_bool("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_COPY"),
            reconstructive_move_page_enabled=env_bool(
                "LOCAL_ONENOTE_ENABLE_RECONSTRUCTIVE_MOVE_PAGE"
            ),
            raw_xml_enabled=env_bool("LOCAL_ONENOTE_ENABLE_RAW_XML"),
        )

    def require_write(self) -> None:
        if not self.writes_enabled:
            raise PermissionError("Writes are disabled. Set LOCAL_ONENOTE_ENABLE_WRITES=true to enable typed mutations.")

    def require_delete(self, *, permanently: bool = False) -> None:
        if not self.deletes_enabled:
            raise PermissionError("Deletes are disabled. Set LOCAL_ONENOTE_ENABLE_DELETES=true to enable typed deletes.")
        if permanently and not self.permanent_deletes_enabled:
            raise PermissionError(
                "Permanent deletes are disabled. Set LOCAL_ONENOTE_ENABLE_PERMANENT_DELETES=true in addition to deletes."
            )

    def require_experimental_move(self) -> None:
        self.require_write()
        if not self.experimental_move_section_enabled:
            raise PermissionError(
                "Section move is experimental. Validate it in an isolated notebook, then set "
                "LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_MOVE_SECTION=true."
            )

    def require_experimental_copy(self) -> None:
        self.require_write()
        if not self.experimental_copy_enabled:
            raise PermissionError(
                "Copy is experimental. Validate it in an isolated notebook, then set "
                "LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_COPY=true."
            )

    def require_reconstructive_move_page(self) -> None:
        self.require_experimental_copy()
        self.require_delete(permanently=False)
        if not self.reconstructive_move_page_enabled:
            raise PermissionError(
                "Reconstructive Page move is disabled. Validate it in an isolated notebook, then set "
                "LOCAL_ONENOTE_ENABLE_RECONSTRUCTIVE_MOVE_PAGE=true."
            )

    def require_raw_xml(self) -> None:
        if not self.raw_xml_enabled:
            raise PermissionError("Raw XML mutations are disabled. Set LOCAL_ONENOTE_ENABLE_RAW_XML=true for development use.")


@dataclass(frozen=True)
class SearchBudget:
    max_pages: int
    max_page_chars: int
    max_total_chars: int
    max_seconds: int
    snippet_chars: int

    @classmethod
    def current(cls) -> "SearchBudget":
        return cls(
            max_pages=env_int("LOCAL_ONENOTE_MAX_SEARCH_PAGES", 200),
            max_page_chars=env_int("LOCAL_ONENOTE_MAX_SEARCH_PAGE_CHARS", 100_000),
            max_total_chars=env_int("LOCAL_ONENOTE_MAX_SEARCH_TOTAL_CHARS", 2_000_000),
            max_seconds=env_int("LOCAL_ONENOTE_MAX_SEARCH_SECONDS", 30),
            snippet_chars=env_int("LOCAL_ONENOTE_MAX_SEARCH_SNIPPET_CHARS", 400),
        )


@dataclass(frozen=True)
class CopyBudget:
    max_resources: int
    max_pages: int
    max_content_objects: int
    max_page_xml_bytes: int
    max_total_xml_bytes: int
    max_plan_seconds: int
    max_execute_seconds: int

    @classmethod
    def current(cls) -> "CopyBudget":
        return cls(
            max_resources=env_int("LOCAL_ONENOTE_MAX_COPY_RESOURCES", 1_000),
            max_pages=env_int("LOCAL_ONENOTE_MAX_COPY_PAGES", 200),
            max_content_objects=env_int("LOCAL_ONENOTE_MAX_COPY_CONTENT_OBJECTS", 10_000),
            max_page_xml_bytes=env_int("LOCAL_ONENOTE_MAX_COPY_PAGE_XML_BYTES", 32 * 1024 * 1024),
            max_total_xml_bytes=env_int("LOCAL_ONENOTE_MAX_COPY_TOTAL_XML_BYTES", 256 * 1024 * 1024),
            max_plan_seconds=env_int("LOCAL_ONENOTE_MAX_COPY_PLAN_SECONDS", 300),
            max_execute_seconds=env_int("LOCAL_ONENOTE_MAX_COPY_EXECUTE_SECONDS", 1_800),
        )
