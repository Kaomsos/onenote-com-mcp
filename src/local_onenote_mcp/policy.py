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
    organize_enabled: bool
    copy_enabled: bool
    local_file_io_enabled: bool
    ui_control_enabled: bool
    notebook_lifecycle_enabled: bool
    internal_section_group_reorder_enabled: bool
    raw_xml_enabled: bool

    @classmethod
    def current(cls) -> "MutationPolicy":
        return cls(
            writes_enabled=env_bool("LOCAL_ONENOTE_ENABLE_WRITES"),
            deletes_enabled=env_bool("LOCAL_ONENOTE_ENABLE_DELETES"),
            permanent_deletes_enabled=env_bool("LOCAL_ONENOTE_ENABLE_PERMANENT_DELETES"),
            organize_enabled=env_bool("LOCAL_ONENOTE_ENABLE_ORGANIZE"),
            copy_enabled=env_bool("LOCAL_ONENOTE_ENABLE_COPY"),
            local_file_io_enabled=env_bool("LOCAL_ONENOTE_ENABLE_LOCAL_FILE_IO"),
            ui_control_enabled=env_bool("LOCAL_ONENOTE_ENABLE_UI_CONTROL"),
            notebook_lifecycle_enabled=env_bool(
                "LOCAL_ONENOTE_ENABLE_NOTEBOOK_LIFECYCLE"
            ),
            internal_section_group_reorder_enabled=env_bool(
                "LOCAL_ONENOTE_ENABLE_INTERNAL_SECTION_GROUP_REORDER"
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

    def require_organize(self) -> None:
        self.require_write()
        if not self.organize_enabled:
            raise PermissionError(
                "Organize operations are disabled. Set "
                "LOCAL_ONENOTE_ENABLE_ORGANIZE=true in addition to Writes."
            )

    def require_section_reorder(self, resource_type: str = "section") -> None:
        self.require_write()
        if resource_type == "section":
            return
        if resource_type != "section_group":
            raise ValueError("Container reorder only supports section or section_group.")
        if not self.internal_section_group_reorder_enabled:
            raise PermissionError(
                "SectionGroup reorder is an unsupported internal diagnostic. Set "
                "LOCAL_ONENOTE_ENABLE_INTERNAL_SECTION_GROUP_REORDER=true only for "
                "explicit isolated diagnostic evidence collection."
            )

    def require_copy(self) -> None:
        self.require_write()
        if not self.copy_enabled:
            raise PermissionError(
                "Copy operations are disabled. Set LOCAL_ONENOTE_ENABLE_COPY=true "
                "in addition to Writes."
            )

    def require_move(self) -> None:
        self.require_copy()
        self.require_delete(permanently=False)

    def require_local_file_io(self) -> None:
        if not self.local_file_io_enabled:
            raise PermissionError(
                "Local file I/O is disabled. Set "
                "LOCAL_ONENOTE_ENABLE_LOCAL_FILE_IO=true to enable explicit local file effects."
            )

    def require_ui_control(self) -> None:
        if not self.ui_control_enabled:
            raise PermissionError(
                "UI control is disabled. Set LOCAL_ONENOTE_ENABLE_UI_CONTROL=true "
                "to enable explicit OneNote GUI actions."
            )

    def require_notebook_lifecycle(self) -> None:
        if not self.notebook_lifecycle_enabled:
            raise PermissionError(
                "Notebook lifecycle operations are disabled. Set "
                "LOCAL_ONENOTE_ENABLE_NOTEBOOK_LIFECYCLE=true to enable Sync and Close."
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
            max_pages=env_int("LOCAL_ONENOTE_MAX_SEARCH_PAGES", 1_000),
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
