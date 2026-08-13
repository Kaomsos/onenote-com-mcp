"""Typed OneNote mutation service with policy, confirmation, and read-back."""

from __future__ import annotations

import base64
from pathlib import Path
import re
import time
from typing import Any

from ..bridge import OneNoteBridge
from ..constants import CREATE_FILE_TYPES, NEW_PAGE_STYLES, SPECIAL_LOCATIONS, XML_SCHEMA_2013
from ..hierarchy import display_name
from ..onenote_errors import (
    OneNoteConvergenceTimeoutError,
    OneNoteError,
    idempotent_retry_allowed,
    transient_read_error,
)
from ..page import (
    DELETABLE_PAGE_OBJECT_TYPES,
    build_image_page_update_xml,
    build_page_update_xml,
    collect_page_objects,
    proportional_dimensions,
    tag_definitions_from_page_xml,
)
from ..policy import CopyBudget, MutationPolicy
from .base import BaseService
from .convergence import DEFAULT_CONVERGENCE, ConvergenceResult, converge
from .errors import PartialFailure
from .hierarchy import HierarchyService
from .pages import PageService, stable_page_content_digest
from .position import destination_position, unavailable_destination_position
from .reconciliation import ReconciliationState, reconcile_mutation


REPLACE_BODY_OBJECT_TYPES = {"Outline", "Image", "InkDrawing", "FileAttachment", "InsertedFile", "MediaFile"}


class MutationService(BaseService):
    def __init__(self, bridge: OneNoteBridge, hierarchy: HierarchyService, pages: PageService) -> None:
        super().__init__(bridge)
        self.hierarchy = hierarchy
        self.pages = pages

    def _converge(
        self,
        *,
        operation: str,
        observe,
        accept,
        project_identity,
        failure_message: str,
        identity_remap: dict[str, str] | None = None,
    ) -> ConvergenceResult[Any]:
        result = converge(
            observe,
            accept,
            project_identity,
            config=DEFAULT_CONVERGENCE,
            identity_remap=identity_remap,
            transient=transient_read_error,
            clock=time.monotonic,
            sleeper=time.sleep,
        )
        if not result.converged:
            raise OneNoteConvergenceTimeoutError(
                failure_message,
                operation=operation,
                partial=True,
                reconciliation="indeterminate",
                details={
                    "convergence": result.summary(),
                    "manual_recovery_required": True,
                },
            )
        return result

    @staticmethod
    def _raise_failed_reconciliation(
        operation: str,
        result,
        *,
        completed_steps: list[dict[str, Any]] | None = None,
    ) -> None:
        if (
            result.error is None
            or result.execution_succeeded
            or result.state is ReconciliationState.APPLIED
        ):
            return
        if result.state is ReconciliationState.NOT_APPLIED:
            if isinstance(result.error, OneNoteError):
                result.error.reconciliation = ReconciliationState.NOT_APPLIED.value
            raise result.error
        raise PartialFailure(
            f"{operation} failed and live state could not be safely reconciled.",
            partial=result.state is not ReconciliationState.NOT_APPLIED,
            reconciliation=result.state.value,
            retryability="manual_recovery_required",
            completed_steps=list(completed_steps or []),
            failed_step=operation,
            manual_recovery_required=True,
        ) from result.error

    def _reconciled_idempotent_execute(
        self,
        *,
        operation: str,
        execute,
        observe,
        is_pre_state,
        is_post_state,
        is_partial_state=None,
    ):
        result = reconcile_mutation(
            execute=execute,
            observe=observe,
            is_pre_state=is_pre_state,
            is_post_state=is_post_state,
            is_partial_state=is_partial_state,
            retry_if_unchanged=True,
            retry_allowed=idempotent_retry_allowed,
        )
        self._raise_failed_reconciliation(operation, result)
        return result

    @staticmethod
    def safe_leaf_name(name: str) -> str:
        cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", name).strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        if not cleaned:
            raise ValueError("Name cannot be empty.")
        return cleaned

    @staticmethod
    def create_resource_type(create_type: str) -> str | None:
        key = create_type.casefold()
        if key == "section":
            return "section"
        if key in {"folder", "section_group"}:
            return "section_group"
        if key == "notebook":
            return "notebook"
        return None

    def confirm_resource(
        self,
        object_id: str,
        resource_type: str,
        *,
        expected_name: str,
        expected_parent_id: str | None,
        expected_modified: str | None = None,
    ) -> dict[str, Any]:
        item = self.hierarchy.resource(object_id, resource_type)
        actual_name = display_name(item)
        if actual_name != expected_name:
            raise ValueError(f"Confirmation mismatch: expected name '{expected_name}', found '{actual_name}'.")
        if item["parent_id"] != expected_parent_id:
            raise ValueError(
                f"Confirmation mismatch: expected parent_id '{expected_parent_id}', found '{item['parent_id']}'."
            )
        if expected_modified is not None and item.get("modified") != expected_modified:
            raise ValueError(
                f"Confirmation mismatch: expected modified '{expected_modified}', found '{item.get('modified')}'."
            )
        return item

    def open_hierarchy(self, path: str, relative_to_identifier: str = "", create_type: str = "none") -> dict[str, Any]:
        normalized_create_type = create_type.strip().casefold() or "none"
        relative_to_id = ""
        expected_path = path.replace("\\", "/").strip("/")
        expected_parent_id: str | None = None
        if relative_to_identifier:
            parent = self.hierarchy.resolve(relative_to_identifier)
            relative_to_id = parent["id"]
            expected_parent_id = parent["id"]
            expected_path = self.hierarchy.friendly_child_path(parent["path"], path)
        if normalized_create_type == "none":
            existing = self.hierarchy.find_unique_path(expected_path)
            if existing:
                return {
                    "object_id": existing["id"],
                    "item": existing,
                    "opened_existing": True,
                    "accepted": True,
                    "converged": True,
                    "convergence": {
                        "converged": True,
                        "attempts": 1,
                        "elapsed_seconds": 0.0,
                        "stable_observations": 1,
                        "identity_remap": {},
                        "transient_errors": [],
                    },
                }
            if not relative_to_identifier:
                try:
                    existing = self.hierarchy.resolve(path)
                    return {
                        "object_id": existing["id"],
                        "item": existing,
                        "opened_existing": True,
                        "accepted": True,
                        "converged": True,
                        "convergence": {
                            "converged": True,
                            "attempts": 1,
                            "elapsed_seconds": 0.0,
                            "stable_observations": 1,
                            "identity_remap": {},
                            "transient_errors": [],
                        },
                    }
                except ValueError as exc:
                    if not str(exc).startswith("No "):
                        raise
        before_ids = {
            str(item["id"])
            for item in self.hierarchy.resources(include_recycle_bin=True)
            if item.get("id")
        }
        MutationPolicy.current().require_write()
        result = self.call(
            "open_hierarchy",
            path=path,
            relative_to_id=relative_to_id,
            create_file_type=self.enum("create_type", normalized_create_type, CREATE_FILE_TYPES),
        )
        resource_type = self.create_resource_type(normalized_create_type)
        item = (
            self.hierarchy.wait_for_created(
                expected_path,
                resource_type,
                result["object_id"],
                expected_parent_id=expected_parent_id,
                validate_parent=True,
                before_ids=before_ids,
            )
            if resource_type
            else None
        )
        if normalized_create_type == "none":
            # OpenHierarchy may make a previously invisible object active or may
            # return an ID that OneNote subsequently remaps. A COM-returned ID is
            # therefore only an allocation hint, never completion evidence.
            item = self.hierarchy.wait_for_created(
                expected_path,
                None,
                result["object_id"],
                expected_parent_id=expected_parent_id,
                validate_parent=expected_parent_id is not None,
                before_ids=None,
            )
            if item is None:
                raise PartialFailure(
                    "OpenHierarchy accepted the request, but live identity did not converge.",
                    partial=True,
                    accepted=True,
                    converged=False,
                    reconciliation="indeterminate",
                    allocated_ids=[result["object_id"]],
                    resolved_target_ids=[],
                    created_ids=[],
                    completed_steps=[
                        {"operation": "open_hierarchy", "object_id": result["object_id"]}
                    ],
                    failed_step="verify_opened_hierarchy",
                    convergence=self.hierarchy.last_convergence_summary(),
                )
        if resource_type and item is None:
            raise PartialFailure(
                "OpenHierarchy returned success, but the requested created target could not be uniquely verified.",
                partial=True,
                allocated_ids=[result["object_id"]],
                resolved_target_ids=[],
                created_ids=[result["object_id"]] if result["object_id"] not in before_ids else [],
                completed_steps=[{"operation": "open_hierarchy", "object_id": result["object_id"]}],
                failed_step="verify_created_hierarchy",
            )
        data: dict[str, Any] = {
            "object_id": item["id"] if item else result["object_id"],
            "opened_existing": False,
            "allocated_id": result["object_id"],
            "identity_remapped": bool(item and item["id"] != result["object_id"]),
            "accepted": True,
            "converged": item is not None,
            "convergence": self.hierarchy.last_convergence_summary(),
            "reconciliation": {
                "state": "applied",
                "execute_attempts": 1,
                "had_backend_error": False,
            },
        }
        if item:
            data["item"] = item
        return data

    def create_notebook(self, name_or_path: str, base_folder: str = "") -> dict[str, Any]:
        MutationPolicy.current().require_write()
        before_ids = {
            str(item["id"])
            for item in self.hierarchy.resources(include_recycle_bin=True)
            if item.get("id")
        }
        raw = Path(name_or_path)
        if raw.is_absolute():
            notebook_path = raw
        else:
            root = (
                Path(base_folder)
                if base_folder
                else Path(self.call("get_special_location", location=SPECIAL_LOCATIONS["default_notebook_folder"])["path"])
            )
            notebook_path = root / self.safe_leaf_name(name_or_path)
        result = self.call(
            "open_hierarchy",
            path=str(notebook_path),
            relative_to_id="",
            create_file_type=CREATE_FILE_TYPES["notebook"],
        )
        notebook = self.hierarchy.wait_for_created(
            notebook_path.name,
            "notebook",
            result["object_id"],
            expected_parent_id=None,
            validate_parent=True,
            before_ids=before_ids,
        )
        if notebook is None:
            raise PartialFailure(
                "Notebook creation returned success, but the new notebook could not be verified.",
                partial=True,
                allocated_ids=[result["object_id"]],
                resolved_target_ids=[],
                created_ids=[result["object_id"]] if result["object_id"] not in before_ids else [],
                completed_steps=[{"operation": "open_hierarchy", "object_id": result["object_id"]}],
                failed_step="verify_created_notebook",
                convergence=self.hierarchy.last_convergence_summary(),
            )
        return {
            "path": str(notebook_path),
            "notebook_id": notebook["id"],
            "allocated_id": result["object_id"],
            "identity_remapped": notebook["id"] != result["object_id"],
            "item": notebook,
            "convergence": self.hierarchy.last_convergence_summary(),
            "reconciliation": {
                "state": "applied",
                "execute_attempts": 1,
                "had_backend_error": False,
            },
        }

    def create_section(self, parent_id: str, section_name: str) -> dict[str, Any]:
        MutationPolicy.current().require_write()
        parent = self.hierarchy.resource(parent_id)
        if parent["resource_type"] not in {"notebook", "section_group"}:
            raise ValueError("parent_id must identify a notebook or section_group.")
        before_ids = {
            str(item["id"])
            for item in self.hierarchy.resources(include_recycle_bin=True)
            if item.get("id")
        }
        filename = self.safe_leaf_name(section_name)
        if not filename.lower().endswith(".one"):
            filename += ".one"
        result = self.call(
            "open_hierarchy",
            path=filename,
            relative_to_id=parent["id"],
            create_file_type=CREATE_FILE_TYPES["section"],
        )
        expected_path = self.hierarchy.friendly_child_path(parent["path"], filename)
        section = self.hierarchy.wait_for_created(
            expected_path,
            "section",
            result["object_id"],
            expected_parent_id=parent["id"],
            validate_parent=True,
            before_ids=before_ids,
        )
        if section is None:
            raise PartialFailure(
                "Section creation returned success, but the new section could not be verified.",
                partial=True,
                allocated_ids=[result["object_id"]],
                resolved_target_ids=[],
                created_ids=[result["object_id"]] if result["object_id"] not in before_ids else [],
                completed_steps=[{"operation": "open_hierarchy", "object_id": result["object_id"]}],
                failed_step="verify_created_section",
                convergence=self.hierarchy.last_convergence_summary(),
            )
        return {
            "parent": parent,
            "section": section,
            "section_id": section["id"],
            "allocated_id": result["object_id"],
            "identity_remapped": section["id"] != result["object_id"],
            "name": section_name,
            "path": expected_path,
            "convergence": self.hierarchy.last_convergence_summary(),
            "reconciliation": {
                "state": "applied",
                "execute_attempts": 1,
                "had_backend_error": False,
            },
        }

    def create_section_group(self, parent_id: str, group_name: str) -> dict[str, Any]:
        MutationPolicy.current().require_write()
        parent = self.hierarchy.resource(parent_id)
        if parent["resource_type"] not in {"notebook", "section_group"}:
            raise ValueError("parent_id must identify a notebook or section_group.")
        before_ids = {
            str(item["id"])
            for item in self.hierarchy.resources(include_recycle_bin=True)
            if item.get("id")
        }
        result = self.call(
            "open_hierarchy",
            path=self.safe_leaf_name(group_name),
            relative_to_id=parent["id"],
            create_file_type=CREATE_FILE_TYPES["section_group"],
        )
        expected_path = self.hierarchy.friendly_child_path(parent["path"], group_name)
        group = self.hierarchy.wait_for_created(
            expected_path,
            "section_group",
            result["object_id"],
            expected_parent_id=parent["id"],
            validate_parent=True,
            before_ids=before_ids,
        )
        if group is None:
            raise PartialFailure(
                "Section-group creation returned success, but the new group could not be verified.",
                partial=True,
                allocated_ids=[result["object_id"]],
                resolved_target_ids=[],
                created_ids=[result["object_id"]] if result["object_id"] not in before_ids else [],
                completed_steps=[{"operation": "open_hierarchy", "object_id": result["object_id"]}],
                failed_step="verify_created_section_group",
                convergence=self.hierarchy.last_convergence_summary(),
            )
        return {
            "parent": parent,
            "section_group": group,
            "section_group_id": group["id"],
            "allocated_id": result["object_id"],
            "identity_remapped": group["id"] != result["object_id"],
            "name": group_name,
            "path": expected_path,
            "convergence": self.hierarchy.last_convergence_summary(),
            "reconciliation": {
                "state": "applied",
                "execute_attempts": 1,
                "had_backend_error": False,
            },
        }

    def create_page(
        self,
        section_id: str,
        title: str,
        content: str = "",
        content_format: str = "plain",
        new_page_style: str = "blank_with_title",
        *,
        forbidden_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        MutationPolicy.current().require_write()
        section = self.hierarchy.resource(section_id, "section")
        before_ids = {
            str(item["id"])
            for item in self.hierarchy.resources(include_recycle_bin=True)
            if item.get("id")
        }
        page_id = self.call(
            "create_new_page",
            section_id=section["id"],
            new_page_style=self.enum("new_page_style", new_page_style, NEW_PAGE_STYLES),
        )["page_id"]
        page_is_fresh_allocation = (
            page_id not in before_ids and page_id not in (forbidden_ids or set())
        )
        completed_steps = [{"operation": "create_new_page", "object_id": page_id}]
        try:
            if not page_is_fresh_allocation:
                raise RuntimeError(
                    "CreateNewPage returned an ID that was already active or forbidden for this operation."
                )
            xml = build_page_update_xml(page_id, title=title, content=content, content_format=content_format)
            self.call("update_page_content", xml=xml, schema=XML_SCHEMA_2013, force=False)
            completed_steps.append({"operation": "update_page_content", "object_id": page_id})
            expected_path = self.hierarchy.friendly_child_path(section["path"], title)
            page = self.hierarchy.wait_for_created(
                expected_path,
                "page",
                page_id,
                expected_parent_id=section["id"],
                validate_parent=True,
                before_ids=before_ids,
            )
            if page is None:
                raise RuntimeError("Page creation returned success, but the new page could not be verified.")
        except Exception as exc:
            raise PartialFailure(
                str(exc),
                partial=True,
                allocated_ids=[page_id],
                resolved_target_ids=[],
                created_ids=[page_id] if page_is_fresh_allocation else [],
                source_touched=False,
                topology_touched=page_is_fresh_allocation,
                manual_recovery_required=page_is_fresh_allocation,
                completed_steps=completed_steps,
                failed_step=(
                    "verify_created_page"
                    if any(step["operation"] == "update_page_content" for step in completed_steps)
                    else "initialize_created_page"
                ),
                convergence=self.hierarchy.last_convergence_summary(),
            ) from exc
        return {
            "page_id": page["id"],
            "allocated_id": page_id,
            "identity_remapped": page["id"] != page_id,
            "page": page,
            "section": section,
            "title": title,
            "path": expected_path,
            "convergence": self.hierarchy.last_convergence_summary(),
            "reconciliation": {
                "state": "applied",
                "execute_attempts": 1,
                "had_backend_error": False,
            },
        }

    def update_page_title(
        self,
        page_id: str,
        title: str,
        expected_title: str,
        expected_section_id: str,
        expected_modified: str | None = None,
    ) -> dict[str, Any]:
        MutationPolicy.current().require_write()
        self.pages.confirm(
            page_id,
            expected_title=expected_title,
            expected_section_id=expected_section_id,
            expected_modified=expected_modified,
        )
        def observe():
            try:
                return self.hierarchy.resource(page_id, "page")
            except ValueError:
                return None

        reconciliation = self._reconciled_idempotent_execute(
            operation="update_page_title",
            execute=lambda: self.call(
                "update_page_content",
                xml=build_page_update_xml(page_id, title=title),
                schema=XML_SCHEMA_2013,
                force=False,
            ),
            observe=observe,
            is_pre_state=lambda value: value is not None and value.get("title") == expected_title,
            is_post_state=lambda value: value is not None and value.get("title") == title,
        )
        stable = self._converge(
            operation="update_page_title",
            observe=observe,
            accept=lambda value: value is not None and value.get("title") == title,
            project_identity=self.hierarchy._resource_identity,
            failure_message="Update was accepted, but the Page title did not converge.",
        )
        return {
            "item": stable.value,
            "convergence": stable.summary(),
            "reconciliation": reconciliation.summary(),
        }

    def rename_resource(
        self,
        object_id: str,
        resource_type: str,
        new_name: str,
        expected_name: str,
        expected_parent_id: str,
        expected_modified: str | None,
    ) -> dict[str, Any]:
        MutationPolicy.current().require_write()
        item = self.confirm_resource(
            object_id,
            resource_type,
            expected_name=expected_name,
            expected_parent_id=expected_parent_id,
            expected_modified=expected_modified,
        )
        normalized_name = self.safe_leaf_name(new_name)
        def observe():
            try:
                return self.hierarchy.resource(object_id, resource_type)
            except ValueError:
                return None

        reconciliation = self._reconciled_idempotent_execute(
            operation="rename_resource",
            execute=lambda: self.call(
                "update_hierarchy",
                xml=self.hierarchy.update_xml(item, name=normalized_name),
                schema=XML_SCHEMA_2013,
            ),
            observe=observe,
            is_pre_state=lambda value: value is not None
            and display_name(value) == expected_name
            and value.get("parent_id") == expected_parent_id,
            is_post_state=lambda value: value is not None
            and display_name(value) == normalized_name
            and value.get("parent_id") == expected_parent_id,
        )
        stable = self._converge(
            operation="rename_resource",
            observe=observe,
            accept=lambda value: value is not None
            and display_name(value) == normalized_name
            and value.get("parent_id") == expected_parent_id,
            project_identity=self.hierarchy._resource_identity,
            failure_message="Rename was accepted, but the hierarchy identity did not converge.",
        )
        return {
            "item": stable.value,
            "previous_name": expected_name,
            "convergence": stable.summary(),
            "reconciliation": reconciliation.summary(),
        }

    def reorder_page(
        self,
        page_id: str,
        expected_title: str,
        expected_section_id: str,
        after_page_id: str = "",
        page_level: int = 0,
        expected_modified: str | None = None,
    ) -> dict[str, Any]:
        MutationPolicy.current().require_write()
        page = self.pages.confirm(
            page_id,
            expected_title=expected_title,
            expected_section_id=expected_section_id,
            expected_modified=expected_modified,
        )
        section = self.hierarchy.resource(expected_section_id, "section")
        pages = [
            item
            for item in self.hierarchy.resources(include_recycle_bin=False)
            if item["resource_type"] == "page" and item["section_id"] == expected_section_id
        ]
        pages.sort(key=lambda item: item["order"])
        before_signature = tuple(
            (item["id"], int(item.get("order", 0)), int(item.get("page_level", 1)))
            for item in pages
        )
        pages = [item for item in pages if item["id"] != page_id]
        if after_page_id:
            if after_page_id == page_id:
                raise ValueError("after_page_id cannot equal page_id.")
            indexes = [index for index, item in enumerate(pages) if item["id"] == after_page_id]
            if not indexes:
                raise ValueError("after_page_id must identify another page in the same section.")
            insertion_index = indexes[0] + 1
        else:
            insertion_index = 0
        target_level = page_level or page["page_level"]
        if target_level < 1:
            raise ValueError("page_level must be zero (preserve) or at least 1.")
        if insertion_index == 0 and target_level != 1:
            raise ValueError("The first page in a section must have page_level=1.")
        if insertion_index > 0 and target_level > pages[insertion_index - 1]["page_level"] + 1:
            raise ValueError("page_level cannot jump by more than one level from the preceding page.")
        pages.insert(insertion_index, {**page, "page_level": target_level})
        expected_signature = tuple(
            (item["id"], order, int(item.get("page_level", 1)))
            for order, item in enumerate(pages)
        )

        def observe():
            refreshed_pages = [
                item
                for item in self.hierarchy.resources(include_recycle_bin=False)
                if item["resource_type"] == "page"
                and item["section_id"] == expected_section_id
            ]
            refreshed_pages.sort(key=lambda item: item["order"])
            return refreshed_pages

        def signature(values):
            return tuple(
                (item["id"], int(item.get("order", 0)), int(item.get("page_level", 1)))
                for item in values
            )

        reconciliation = self._reconciled_idempotent_execute(
            operation="reorder_page",
            execute=lambda: self.call(
                "update_hierarchy",
                xml=self.hierarchy.page_order_xml(section, pages),
                schema=XML_SCHEMA_2013,
            ),
            observe=observe,
            is_pre_state=lambda values: signature(values) == before_signature,
            is_post_state=lambda values: signature(values) == expected_signature,
            is_partial_state=lambda values: {item["id"] for item in values}
            != {item[0] for item in before_signature},
        )
        stable = self._converge(
            operation="reorder_page",
            observe=observe,
            accept=lambda values: signature(values) == expected_signature,
            project_identity=signature,
            failure_message="Reorder was accepted, but Page order did not converge.",
        )
        refreshed_pages = stable.value or []
        refreshed = next(item for item in refreshed_pages if item["id"] == page_id)
        return {
            "item": refreshed,
            "pages": refreshed_pages,
            "convergence": stable.summary(),
            "reconciliation": reconciliation.summary(),
        }

    @staticmethod
    def _container_subtree(
        items: list[dict[str, Any]], root_id: str
    ) -> list[dict[str, Any]]:
        descendant_ids = {root_id}
        while True:
            added = {
                item["id"]
                for item in items
                if item.get("parent_id") in descendant_ids and item.get("id") not in descendant_ids
            }
            if not added:
                break
            descendant_ids.update(added)
        return [item for item in items if item.get("id") in descendant_ids]

    @staticmethod
    def _container_subtree_signature(items: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
        return sorted(
            (
                item.get("id"),
                item.get("resource_type"),
                item.get("parent_id"),
                item.get("notebook_id"),
                item.get("section_id"),
                item.get("order"),
                item.get("page_level"),
                item.get("parent_page_id"),
                display_name(item),
            )
            for item in items
        )

    def _page_digests(self, pages: list[dict[str, Any]]) -> dict[str, str]:
        budget = CopyBudget.current()
        if len(pages) > budget.max_pages:
            raise ValueError(
                f"Reorder verification includes {len(pages)} Pages, above the configured "
                f"maximum of {budget.max_pages}."
            )
        total_bytes = 0
        digests: dict[str, str] = {}
        for page in pages:
            xml = self.pages.xml(page["id"], "all")
            size = len(xml.encode("utf-8"))
            if size > budget.max_page_xml_bytes:
                raise ValueError(
                    f"Reorder verification Page {page['id']} exceeds the configured per-Page XML budget."
                )
            total_bytes += size
            if total_bytes > budget.max_total_xml_bytes:
                raise ValueError("Reorder verification exceeds the configured total XML budget.")
            digests[page["id"]] = self.pages.digest(xml)
        return digests

    def _reorder_container(
        self,
        object_id: str,
        resource_type: str,
        expected_name: str,
        expected_parent_id: str,
        after_id: str,
        expected_modified: str | None,
    ) -> dict[str, Any]:
        MutationPolicy.current().require_experimental_reorder(resource_type)
        all_items = self.hierarchy.resources(include_recycle_bin=True)
        by_id = {item["id"]: item for item in all_items if item.get("id")}
        item = by_id.get(object_id)
        if item is None:
            raise ValueError(f"No {resource_type} found for ID '{object_id}'.")
        if item.get("resource_type") != resource_type:
            raise ValueError(f"ID '{object_id}' does not identify a {resource_type}.")
        if item.get("is_in_recycle_bin") is True:
            raise ValueError(f"Cannot reorder a {resource_type} in the recycle bin.")
        if display_name(item) != expected_name:
            raise ValueError(
                f"Confirmation mismatch: expected name '{expected_name}', found '{display_name(item)}'."
            )
        if item.get("parent_id") != expected_parent_id:
            raise ValueError(
                f"Confirmation mismatch: expected parent_id '{expected_parent_id}', "
                f"found '{item.get('parent_id')}'."
            )
        if expected_modified is not None and item.get("modified") != expected_modified:
            raise ValueError(
                f"Confirmation mismatch: expected modified '{expected_modified}', "
                f"found '{item.get('modified')}'."
            )

        parent = by_id.get(expected_parent_id)
        allowed_parent_types = {"notebook", "section_group"}
        if parent is None or parent.get("resource_type") not in allowed_parent_types:
            raise ValueError(
                "expected_parent_id must identify an active notebook or section_group."
            )
        if parent.get("is_in_recycle_bin") is True:
            raise ValueError("Cannot reorder an object below a recycle-bin parent.")

        active_items = self.hierarchy.without_recycle_bin(all_items)
        direct_children = [
            candidate
            for candidate in active_items
            if candidate.get("parent_id") == expected_parent_id
            and candidate.get("resource_type") in {"section", "section_group"}
        ]
        siblings = [
            candidate for candidate in direct_children if candidate["resource_type"] == resource_type
        ]
        if object_id not in {candidate["id"] for candidate in siblings}:
            raise RuntimeError("Reorder target is absent from its active direct sibling sequence.")

        remaining = [candidate for candidate in siblings if candidate["id"] != object_id]
        if after_id:
            if after_id == object_id:
                parameter = "after_section_id" if resource_type == "section" else "after_section_group_id"
                raise ValueError(f"{parameter} cannot equal the target ID.")
            predecessor = by_id.get(after_id)
            if predecessor is None:
                raise ValueError(f"Predecessor ID '{after_id}' does not exist.")
            if predecessor.get("is_in_recycle_bin") is True:
                raise ValueError("Predecessor cannot be in the recycle bin.")
            if predecessor.get("resource_type") != resource_type:
                raise ValueError(f"Predecessor must identify another {resource_type}.")
            if predecessor.get("parent_id") != expected_parent_id:
                raise ValueError("Predecessor must have the same parent as the reorder target.")
            insertion_index = next(
                index for index, candidate in enumerate(remaining) if candidate["id"] == after_id
            ) + 1
        else:
            insertion_index = 0
        ordered_siblings = [*remaining]
        ordered_siblings.insert(insertion_index, item)

        sibling_iterator = iter(ordered_siblings)
        ordered_children = [
            next(sibling_iterator) if child["resource_type"] == resource_type else child
            for child in direct_children
        ]
        before_subtree = self._container_subtree(active_items, object_id)
        if len(before_subtree) > CopyBudget.current().max_resources:
            raise ValueError("Reorder verification exceeds the configured hierarchy resource budget.")
        before_signature = self._container_subtree_signature(before_subtree)
        before_pages = [node for node in before_subtree if node["resource_type"] == "page"]
        before_page_digests = self._page_digests(before_pages)
        before_direct_ids = {child["id"] for child in direct_children}
        expected_sibling_ids = [candidate["id"] for candidate in ordered_siblings]

        update_xml = self.hierarchy.container_order_xml(
            parent,
            ordered_children,
            catalog=active_items,
        )

        def direct_signature(values):
            direct = [
                candidate
                for candidate in values
                if candidate.get("parent_id") == expected_parent_id
                and candidate.get("resource_type") in {"section", "section_group"}
            ]
            typed = [candidate["id"] for candidate in direct if candidate["resource_type"] == resource_type]
            return tuple(typed), frozenset(candidate["id"] for candidate in direct)

        before_direct_signature = direct_signature(active_items)
        expected_direct_signature = (tuple(expected_sibling_ids), frozenset(before_direct_ids))
        reconciliation = self._reconciled_idempotent_execute(
            operation=f"reorder_{resource_type}",
            execute=lambda: self.call(
                "update_hierarchy", xml=update_xml, schema=XML_SCHEMA_2013
            ),
            observe=lambda: self.hierarchy.resources(include_recycle_bin=False),
            is_pre_state=lambda values: direct_signature(values) == before_direct_signature,
            is_post_state=lambda values: direct_signature(values) == expected_direct_signature,
            is_partial_state=lambda values: direct_signature(values)[1]
            != before_direct_signature[1],
        )

        def validated_snapshot():
            refreshed_items = self.hierarchy.resources(include_recycle_bin=False)
            refreshed_by_id = {candidate["id"]: candidate for candidate in refreshed_items}
            refreshed = refreshed_by_id.get(object_id)
            if refreshed is None or refreshed.get("resource_type") != resource_type:
                return None
            if refreshed.get("parent_id") != expected_parent_id:
                return None
            refreshed_direct = [
                candidate
                for candidate in refreshed_items
                if candidate.get("parent_id") == expected_parent_id
                and candidate.get("resource_type") in {"section", "section_group"}
            ]
            if {candidate["id"] for candidate in refreshed_direct} != before_direct_ids:
                return None
            refreshed_siblings = [
                candidate
                for candidate in refreshed_direct
                if candidate["resource_type"] == resource_type
            ]
            if [candidate["id"] for candidate in refreshed_siblings] != expected_sibling_ids:
                return None
            refreshed_subtree = self._container_subtree(refreshed_items, object_id)
            if self._container_subtree_signature(refreshed_subtree) != before_signature:
                return None
            refreshed_pages = [
                node for node in refreshed_subtree if node["resource_type"] == "page"
            ]
            if self._page_digests(refreshed_pages) != before_page_digests:
                return None
            return {"item": refreshed, "siblings": refreshed_siblings}

        stable = self._converge(
            operation=f"reorder_{resource_type}",
            observe=validated_snapshot,
            accept=lambda value: value is not None,
            project_identity=lambda value: (
                value["item"]["id"],
                tuple(item["id"] for item in value["siblings"]),
            ),
            failure_message="Reorder was accepted, but container topology did not converge.",
        )
        assert stable.value is not None
        refreshed = stable.value["item"]
        refreshed_siblings = stable.value["siblings"]
        return {
            "item": refreshed,
            "siblings": refreshed_siblings,
            "after_id": after_id,
            "verified": {
                "parent_unchanged": True,
                "sibling_ids_unchanged": True,
                "descendants_unchanged": True,
                "page_content_unchanged": True,
            },
            "convergence": stable.summary(),
            "reconciliation": reconciliation.summary(),
        }

    def reorder_section(
        self,
        section_id: str,
        expected_name: str,
        expected_parent_id: str,
        after_section_id: str = "",
        expected_modified: str | None = None,
    ) -> dict[str, Any]:
        return self._reorder_container(
            section_id,
            "section",
            expected_name,
            expected_parent_id,
            after_section_id,
            expected_modified,
        )

    def reorder_section_group(
        self,
        section_group_id: str,
        expected_name: str,
        expected_parent_id: str,
        after_section_group_id: str = "",
        expected_modified: str | None = None,
    ) -> dict[str, Any]:
        return self._reorder_container(
            section_group_id,
            "section_group",
            expected_name,
            expected_parent_id,
            after_section_group_id,
            expected_modified,
        )

    @staticmethod
    def _resource_notebook_id(item: dict[str, Any]) -> str | None:
        return (
            item.get("id")
            if item.get("resource_type") == "notebook"
            else item.get("notebook_id")
        )

    @staticmethod
    def _notebook_items(items: list[dict[str, Any]], notebook_id: str) -> list[dict[str, Any]]:
        return [
            item
            for item in items
            if item.get("id") == notebook_id or item.get("notebook_id") == notebook_id
        ]

    def _capture_reparent_snapshot(self, notebook_id: str) -> dict[str, Any]:
        """Capture bounded hierarchy and Page evidence for one active Notebook."""

        budget = CopyBudget.current()
        initial = self._notebook_items(
            self.hierarchy.resources(include_recycle_bin=False), notebook_id
        )
        if not any(item.get("id") == notebook_id for item in initial):
            raise ValueError(f"No active notebook found for ID '{notebook_id}'.")
        if len(initial) > budget.max_resources:
            raise ValueError("Reparent verification exceeds the configured hierarchy resource budget.")
        pages = [item for item in initial if item.get("resource_type") == "page"]
        if len(pages) > budget.max_pages:
            raise ValueError("Reparent verification exceeds the configured Page budget.")

        total_bytes = 0
        page_xml: dict[str, str] = {}
        for page in pages:
            xml = self.pages.xml(page["id"], "all")
            size = len(xml.encode("utf-8"))
            if size > budget.max_page_xml_bytes:
                raise ValueError(
                    f"Reparent verification Page {page['id']} exceeds the per-Page XML budget."
                )
            total_bytes += size
            if total_bytes > budget.max_total_xml_bytes:
                raise ValueError("Reparent verification exceeds the total Page XML budget.")
            page_xml[page["id"]] = xml

        refreshed = self._notebook_items(
            self.hierarchy.resources(include_recycle_bin=False), notebook_id
        )
        if {item["id"] for item in refreshed} != {item["id"] for item in initial}:
            raise RuntimeError(
                "Notebook hierarchy changed while Reparent verification evidence was collected."
            )
        return {"items": refreshed, "page_xml": page_xml}

    @staticmethod
    def _ordered_children(
        items: list[dict[str, Any]], excluded_ids: set[str]
    ) -> dict[str, list[tuple[str, str]]]:
        result: dict[str, list[tuple[str, str]]] = {}
        for item in items:
            if item.get("id") in excluded_ids or item.get("resource_type") == "notebook":
                continue
            parent_id = (
                item.get("section_id")
                if item.get("resource_type") == "page"
                else item.get("parent_id")
            )
            if parent_id:
                result.setdefault(parent_id, []).append((item["resource_type"], item["id"]))
        return result

    def _validate_reparent_snapshots(
        self,
        before: dict[str, Any],
        after: dict[str, Any],
        *,
        target_id: str,
        destination_parent_id: str,
        resource_type: str,
    ) -> tuple[dict[str, Any], dict[str, str], dict[str, bool]]:
        before_items = before["items"]
        after_items = after["items"]
        before_by_id = {item["id"]: item for item in before_items}
        after_by_id = {item["id"]: item for item in after_items}
        before_ids = set(before_by_id)
        after_ids = set(after_by_id)
        before_target = before_by_id[target_id]

        current_target_id = target_id
        if resource_type == "page" and target_id not in after_by_id:
            removed = before_ids - after_ids
            added = after_ids - before_ids
            if removed != {target_id} or len(added) != 1:
                raise RuntimeError("Page reparent did not produce one exact old-to-new ID transition.")
            current_target_id = next(iter(added))
        elif before_ids != after_ids:
            raise RuntimeError("Reparent changed one or more unexpected hierarchy object IDs.")

        current = after_by_id.get(current_target_id)
        if current is None or current.get("resource_type") != resource_type:
            raise RuntimeError("Reparent returned success, but the typed target could not be read back.")
        observed_parent = (
            current.get("section_id") if resource_type == "page" else current.get("parent_id")
        )
        if observed_parent != destination_parent_id:
            raise RuntimeError("Reparent returned success, but the requested parent was not observed.")
        if display_name(current) != display_name(before_target):
            raise RuntimeError("Reparent changed the target name or title.")
        if self._resource_notebook_id(current) != self._resource_notebook_id(before_target):
            raise RuntimeError("Reparent changed the target Notebook identity.")

        id_map = {target_id: current_target_id}
        excluded_before = {target_id}
        excluded_after = {current_target_id}
        if self._ordered_children(before_items, excluded_before) != self._ordered_children(
            after_items, excluded_after
        ):
            raise RuntimeError("Reparent changed unrelated hierarchy topology or sibling order.")

        relationship_fields = ("parent_id", "section_id", "page_level", "parent_page_id")
        for object_id, before_item in before_by_id.items():
            if object_id == target_id:
                continue
            after_item = after_by_id.get(object_id)
            if after_item is None:
                raise RuntimeError(f"Reparent removed unrelated hierarchy object {object_id}.")
            if (
                after_item.get("resource_type") != before_item.get("resource_type")
                or display_name(after_item) != display_name(before_item)
                or self._resource_notebook_id(after_item)
                != self._resource_notebook_id(before_item)
            ):
                raise RuntimeError(f"Reparent changed unrelated hierarchy object {object_id}.")
            if any(after_item.get(field) != before_item.get(field) for field in relationship_fields):
                raise RuntimeError(f"Reparent changed an unrelated relationship for {object_id}.")

        for page_id, before_xml in before["page_xml"].items():
            if page_id == target_id:
                continue
            after_xml = after["page_xml"].get(page_id)
            if after_xml is None or self.pages.digest(after_xml) != self.pages.digest(before_xml):
                raise RuntimeError(f"Reparent changed unrelated Page content for {page_id}.")

        if resource_type == "page":
            if (
                current.get("page_level") != before_target.get("page_level")
                or current.get("parent_page_id") != before_target.get("parent_page_id")
            ):
                raise RuntimeError("Page reparent changed indentation topology.")
            before_xml = before["page_xml"][target_id]
            after_xml = after["page_xml"].get(current_target_id)
            if after_xml is None:
                raise RuntimeError("Page reparent read-back is missing target Page content.")
            if self.pages.reparent_digest(after_xml) != self.pages.reparent_digest(before_xml):
                raise RuntimeError("Page reparent changed rich Page content semantics.")
            id_map.update(self.pages.observable_id_map(before_xml, after_xml))
            verified = {
                "parent_applied": True,
                "target_id_transition_valid": True,
                "same_notebook_preserved": True,
                "page_topology_preserved": True,
                "rich_content_preserved": True,
                "content_object_ids_mapped": True,
                "unrelated_objects_preserved": True,
            }
        else:
            verified = {
                "parent_applied": True,
                f"{resource_type}_id_preserved": True,
                "same_notebook_preserved": True,
                "descendant_topology_preserved": True,
                "page_content_preserved": True,
                "unrelated_objects_preserved": True,
            }
        return current, id_map, verified

    @staticmethod
    def _page_scope(items: list[dict[str, Any]], page_id: str) -> list[dict[str, Any]]:
        target = next(
            (
                item
                for item in items
                if item.get("id") == page_id and item.get("resource_type") == "page"
            ),
            None,
        )
        if target is None:
            raise ValueError(f"No active Page found for ID '{page_id}'.")
        pages = sorted(
            (
                item
                for item in items
                if item.get("resource_type") == "page"
                and item.get("section_id") == target.get("section_id")
            ),
            key=lambda item: int(item.get("order", 0)),
        )
        start = next(index for index, item in enumerate(pages) if item["id"] == page_id)
        root_level = int(target.get("page_level") or 1)
        selected = [target]
        for candidate in pages[start + 1 :]:
            if int(candidate.get("page_level") or 1) <= root_level:
                break
            selected.append(candidate)
        return selected

    @staticmethod
    def _page_parent_map(pages: list[dict[str, Any]]) -> dict[str, str | None]:
        result: dict[str, str | None] = {}
        stack: list[dict[str, Any]] = []
        for page in pages:
            level = int(page.get("page_level") or 1)
            while stack and int(stack[-1].get("page_level") or 1) >= level:
                stack.pop()
            result[str(page["id"])] = str(stack[-1]["id"]) if stack else None
            stack.append(page)
        return result

    def _promote_reparent_descendants(
        self,
        before: dict[str, Any],
        target: dict[str, Any],
        descendants: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if not descendants:
            return before, {"promoted": False, "preserved_descendant_ids": []}
        section_id = str(target["section_id"])
        section = next(
            item
            for item in before["items"]
            if item.get("id") == section_id and item.get("resource_type") == "section"
        )
        pages = sorted(
            (
                dict(item)
                for item in before["items"]
                if item.get("resource_type") == "page"
                and str(item.get("section_id")) == section_id
            ),
            key=lambda item: int(item.get("order", 0)),
        )
        descendant_ids = {str(item["id"]) for item in descendants}
        adjusted = [
            {
                **page,
                "page_level": (
                    int(page.get("page_level") or 1) - 1
                    if str(page["id"]) in descendant_ids
                    else int(page.get("page_level") or 1)
                ),
            }
            for page in pages
        ]
        if any(int(page["page_level"]) < 1 for page in adjusted):
            raise RuntimeError("A preserved Reparent descendant cannot be promoted above level 1.")
        self.call(
            "update_hierarchy",
            xml=self.hierarchy.page_order_xml(section, adjusted),
            schema=XML_SCHEMA_2013,
        )
        after = self._capture_reparent_snapshot(str(target["notebook_id"]))
        current_pages = sorted(
            (
                item
                for item in after["items"]
                if item.get("resource_type") == "page"
                and str(item.get("section_id")) == section_id
            ),
            key=lambda item: int(item.get("order", 0)),
        )
        if [item["id"] for item in current_pages] != [item["id"] for item in adjusted]:
            raise RuntimeError("Descendant promotion changed source Page identity or order.")
        expected_parents = self._page_parent_map(adjusted)
        for expected, current in zip(adjusted, current_pages, strict=True):
            page_id = str(expected["id"])
            if (
                int(current.get("page_level") or 0) != int(expected["page_level"])
                or current.get("parent_page_id") != expected_parents[page_id]
                or stable_page_content_digest(after["page_xml"][page_id])
                != stable_page_content_digest(before["page_xml"][page_id])
            ):
                raise RuntimeError("Descendant promotion did not preserve Page topology/content.")
        return after, {
            "promoted": True,
            "preserved_descendant_ids": [str(item["id"]) for item in descendants],
        }

    def _validate_reparent_page_scope(
        self,
        before: dict[str, Any],
        after: dict[str, Any],
        *,
        selected: list[dict[str, Any]],
        destination_section_id: str,
        include_descendants: bool,
    ) -> tuple[dict[str, Any], dict[str, str], dict[str, bool]]:
        before_by_id = {str(item["id"]): item for item in before["items"]}
        after_by_id = {str(item["id"]): item for item in after["items"]}
        selected_ids = [str(item["id"]) for item in selected]
        root = selected[0]
        added_ids = set(after_by_id) - set(before_by_id)

        root_candidates = []
        for item in after["items"]:
            item_id = str(item.get("id", ""))
            if (
                item.get("resource_type") == "page"
                and item.get("section_id") == destination_section_id
                and int(item.get("page_level") or 0) == 1
                and item.get("parent_page_id") is None
                and (item_id == selected_ids[0] or item_id in added_ids)
                and display_name(item) == display_name(root)
            ):
                xml = after["page_xml"].get(item_id)
                if xml is not None and self.pages.reparent_digest(xml) == self.pages.reparent_digest(
                    before["page_xml"][selected_ids[0]]
                ):
                    root_candidates.append(item)
        if len(root_candidates) != 1:
            raise RuntimeError("Page reparent did not yield one exact destination root Page.")
        current_root = root_candidates[0]

        destination_pages = sorted(
            (
                item
                for item in after["items"]
                if item.get("resource_type") == "page"
                and item.get("section_id") == destination_section_id
            ),
            key=lambda item: int(item.get("order", 0)),
        )
        root_index = next(
            index for index, item in enumerate(destination_pages) if item["id"] == current_root["id"]
        )
        current_scope = [current_root]
        if include_descendants:
            for item in destination_pages[root_index + 1 :]:
                if int(item.get("page_level") or 1) <= 1:
                    break
                current_scope.append(item)
        if len(current_scope) != len(selected):
            raise RuntimeError("Page reparent produced an incomplete or expanded destination scope.")

        id_map: dict[str, str] = {}
        reverse_ids: set[str] = set()
        root_level = int(root.get("page_level") or 1)
        for source, current in zip(selected, current_scope, strict=True):
            source_id = str(source["id"])
            current_id = str(current["id"])
            expected_level = int(source.get("page_level") or 1) - root_level + 1
            if (
                current.get("section_id") != destination_section_id
                or int(current.get("page_level") or 0) != expected_level
                or display_name(current) != display_name(source)
                or self.pages.reparent_digest(after["page_xml"][current_id])
                != self.pages.reparent_digest(before["page_xml"][source_id])
            ):
                raise RuntimeError("Page reparent changed selected Page topology/content.")
            if current_id in reverse_ids:
                raise RuntimeError("Page reparent produced a non-injective Page ID mapping.")
            id_map[source_id] = current_id
            reverse_ids.add(current_id)
            observable = self.pages.observable_id_map(
                before["page_xml"][source_id], after["page_xml"][current_id]
            )
            for old_id, new_id in observable.items():
                if old_id in id_map and id_map[old_id] != new_id:
                    raise RuntimeError("Page reparent produced an ambiguous observable ID mapping.")
                if new_id in reverse_ids:
                    raise RuntimeError("Page reparent produced a non-injective observable ID mapping.")
                id_map[old_id] = new_id
                reverse_ids.add(new_id)

        expected_scope_parents = self._page_parent_map(
            [
                {**source, "id": id_map[str(source["id"])], "page_level": int(source.get("page_level") or 1) - root_level + 1}
                for source in selected
            ]
        )
        for current in current_scope:
            if current.get("parent_page_id") != expected_scope_parents[str(current["id"])]:
                raise RuntimeError("Page reparent changed selected Page parent topology.")

        current_ids = {str(item["id"]) for item in current_scope}
        if set(before_by_id) - set(selected_ids) != set(after_by_id) - current_ids:
            raise RuntimeError("Page reparent changed unrelated hierarchy identities.")

        source_section_id = str(root["section_id"])
        remaining_source = sorted(
            (
                dict(item)
                for item in before["items"]
                if item.get("resource_type") == "page"
                and str(item.get("section_id")) == source_section_id
                and str(item["id"]) not in selected_ids
            ),
            key=lambda item: int(item.get("order", 0)),
        )
        if not include_descendants:
            preserved_ids = {str(item["id"]) for item in self._page_scope(before["items"], selected_ids[0])[1:]}
            remaining_source = [
                {
                    **item,
                    "page_level": int(item.get("page_level") or 1) - 1
                    if str(item["id"]) in preserved_ids
                    else int(item.get("page_level") or 1),
                }
                for item in remaining_source
            ]
        expected_source_parents = self._page_parent_map(remaining_source)

        relationship_fields = ("parent_id", "section_id", "page_level", "parent_page_id")
        for object_id, old in before_by_id.items():
            if object_id in selected_ids:
                continue
            current = after_by_id[object_id]
            if (
                current.get("resource_type") != old.get("resource_type")
                or display_name(current) != display_name(old)
                or self._resource_notebook_id(current) != self._resource_notebook_id(old)
            ):
                raise RuntimeError(f"Page reparent changed unrelated object {object_id}.")
            if old.get("resource_type") == "page" and old.get("section_id") == source_section_id:
                expected_page = next(item for item in remaining_source if str(item["id"]) == object_id)
                if (
                    current.get("section_id") != source_section_id
                    or int(current.get("page_level") or 0) != int(expected_page["page_level"])
                    or current.get("parent_page_id") != expected_source_parents[object_id]
                ):
                    raise RuntimeError("Page reparent changed preserved source Page topology.")
            elif any(current.get(field) != old.get(field) for field in relationship_fields):
                raise RuntimeError(f"Page reparent changed an unrelated relationship for {object_id}.")
            if old.get("resource_type") == "page" and stable_page_content_digest(
                after["page_xml"][object_id]
            ) != stable_page_content_digest(before["page_xml"][object_id]):
                raise RuntimeError(f"Page reparent changed unrelated Page content for {object_id}.")

        return current_root, id_map, {
            "parent_applied": True,
            "target_id_transition_valid": True,
            "same_notebook_preserved": True,
            "page_scope_complete": True,
            "page_topology_preserved": True,
            "rich_content_preserved": True,
            "content_object_ids_mapped": True,
            "unrelated_objects_preserved": True,
        }

    def _partial_reparent_page_position(
        self,
        before: dict[str, Any],
        candidate: dict[str, Any] | None,
        *,
        selected_source_ids: list[str],
        destination_section_id: str,
    ) -> tuple[dict[str, Any], list[str], list[str]]:
        """Return partial Page location evidence without claiming scope verification."""

        if candidate is None:
            return (
                unavailable_destination_position(
                    "page", "destination_snapshot_unavailable"
                ),
                [],
                [],
            )
        source_root_id = selected_source_ids[0]
        before_by_id = {str(item["id"]): item for item in before["items"]}
        after_by_id = {str(item["id"]): item for item in candidate["items"]}
        root = before_by_id[source_root_id]
        added_ids = set(after_by_id) - set(before_by_id)
        candidates = [
            item
            for item in candidate["items"]
            if item.get("resource_type") == "page"
            and item.get("section_id") == destination_section_id
            and int(item.get("page_level") or 0) == 1
            and item.get("parent_page_id") in {None, ""}
            and (str(item.get("id")) == source_root_id or str(item.get("id")) in added_ids)
            and display_name(item) == display_name(root)
        ]
        observed = []
        for item in candidates:
            item_id = str(item["id"])
            xml = candidate.get("page_xml", {}).get(item_id)
            if (
                xml is not None
                and self.pages.reparent_digest(xml)
                == self.pages.reparent_digest(before["page_xml"][source_root_id])
            ):
                observed.append(item)
        source_ids = [
            source_id
            for source_id in selected_source_ids
            if source_id in after_by_id
            and after_by_id[source_id].get("section_id") != destination_section_id
        ]
        destination_ids = [str(item["id"]) for item in observed]
        if len(observed) == 1:
            try:
                return (
                    destination_position(candidate["items"], destination_ids[0]),
                    source_ids,
                    destination_ids,
                )
            except RuntimeError:
                pass
        return (
            unavailable_destination_position(
                "page", "destination_target_not_uniquely_observed"
            ),
            source_ids,
            destination_ids,
        )

    def _reparent(
        self,
        object_id: str,
        resource_type: str,
        destination_parent_id: str,
        expected_name: str,
        expected_parent_id: str,
        expected_modified: str | None,
        include_descendants: bool = False,
    ) -> dict[str, Any]:
        MutationPolicy.current().require_experimental_reparent()
        all_items = self.hierarchy.resources(include_recycle_bin=True)
        by_id = {item["id"]: item for item in all_items if item.get("id")}
        target = by_id.get(object_id)
        if target is None or target.get("resource_type") != resource_type:
            raise ValueError(f"No {resource_type} found for ID '{object_id}'.")
        if target.get("is_in_recycle_bin") is True:
            raise ValueError(f"Cannot reparent a {resource_type} in the recycle bin.")
        if display_name(target) != expected_name:
            field = "title" if resource_type == "page" else "name"
            raise ValueError(
                f"Confirmation mismatch: expected {field} '{expected_name}', "
                f"found '{display_name(target)}'."
            )
        actual_parent_id = (
            target.get("section_id") if resource_type == "page" else target.get("parent_id")
        )
        if actual_parent_id != expected_parent_id:
            field = "section_id" if resource_type == "page" else "parent_id"
            raise ValueError(
                f"Confirmation mismatch: expected {field} '{expected_parent_id}', "
                f"found '{actual_parent_id}'."
            )
        if expected_modified is not None and target.get("modified") != expected_modified:
            raise ValueError(
                f"Confirmation mismatch: expected modified '{expected_modified}', "
                f"found '{target.get('modified')}'."
            )
        if destination_parent_id == expected_parent_id:
            raise ValueError("destination parent must differ from the current parent.")

        allowed_destination_types = {
            "page": {"section"},
            "section": {"notebook", "section_group"},
            "section_group": {"notebook", "section_group"},
        }
        source = by_id.get(expected_parent_id)
        if (
            source is None
            or source.get("resource_type") not in allowed_destination_types[resource_type]
            or source.get("is_in_recycle_bin") is True
        ):
            raise ValueError("The confirmed current parent is not an active legal parent.")
        destination = by_id.get(destination_parent_id)
        if (
            destination is None
            or destination.get("resource_type") not in allowed_destination_types[resource_type]
        ):
            label = "section" if resource_type == "page" else "notebook or section_group"
            parameter = (
                "destination_section_id"
                if resource_type == "page"
                else "destination_parent_id"
            )
            raise ValueError(f"{parameter} must identify an active {label}.")
        if destination.get("is_in_recycle_bin") is True:
            raise ValueError("Cannot reparent below a recycle-bin destination.")
        notebook_id = self._resource_notebook_id(target)
        if notebook_id is None or self._resource_notebook_id(destination) != notebook_id:
            raise ValueError(f"reparent_{resource_type} only supports destinations in the same notebook.")

        active_items = self.hierarchy.without_recycle_bin(all_items)
        if resource_type == "section_group":
            descendants = {item["id"] for item in self._container_subtree(active_items, object_id)}
            if destination_parent_id in descendants:
                raise ValueError("A section_group cannot be reparented below itself or its descendant.")
        before = self._capture_reparent_snapshot(notebook_id)
        before_by_id = {item["id"]: item for item in before["items"]}
        snap_target = before_by_id.get(object_id)
        snap_destination = before_by_id.get(destination_parent_id)
        if snap_target != target or snap_destination != destination:
            raise RuntimeError("Hierarchy changed after Reparent confirmation; mutation was not attempted.")

        selected: list[dict[str, Any]] = []
        preservation: dict[str, Any] = {
            "promoted": False,
            "preserved_descendant_ids": [],
        }
        mutation_catalog = active_items
        mutation_target = target
        if resource_type == "page":
            complete_scope = self._page_scope(before["items"], object_id)
            selected = complete_scope if include_descendants else complete_scope[:1]
            if not include_descendants:
                try:
                    promoted, preservation = self._promote_reparent_descendants(
                        before,
                        target,
                        complete_scope[1:],
                    )
                except Exception as exc:
                    raise PartialFailure(
                        "Excluded Page descendants could not be promoted and verified; "
                        "Reparent was not attempted.",
                        partial=True,
                        outcome="descendant_promotion_unverified",
                        reparent_attempted=False,
                        destination_position=unavailable_destination_position(
                            "page", "destination_target_not_created"
                        ),
                        preserved_descendant_ids=[
                            str(item["id"]) for item in complete_scope[1:]
                        ],
                        promotion_error=str(exc),
                    ) from exc
                mutation_catalog = promoted["items"]
                mutation_by_id = {item["id"]: item for item in mutation_catalog}
                mutation_target = mutation_by_id[object_id]
                selected = [mutation_target]
            xml = self.hierarchy.reparent_page_scope_xml(
                selected,
                destination,
                catalog=mutation_catalog,
            )
        else:
            xml = self.hierarchy.reparent_xml(
                target,
                destination,
                catalog=active_items,
            )
        try:
            self.call(
                "update_hierarchy",
                xml=xml,
                schema=XML_SCHEMA_2013,
            )
        except Exception as exc:
            if resource_type == "page" and preservation.get("promoted"):
                failure_snapshot: dict[str, Any] | None = None
                try:
                    failure_snapshot = self._capture_reparent_snapshot(notebook_id)
                except Exception:
                    pass
                partial_position, active_source_ids, observed_destination_ids = (
                    self._partial_reparent_page_position(
                        before,
                        failure_snapshot,
                        selected_source_ids=[str(item["id"]) for item in complete_scope[:1]],
                        destination_section_id=destination_parent_id,
                    )
                )
                raise PartialFailure(
                    "Excluded descendants were promoted, but the selected Page was not reparented.",
                    partial=True,
                    outcome="descendants_promoted_reparent_not_completed",
                    reparent_attempted=True,
                    destination_position=partial_position,
                    active_source_ids=active_source_ids,
                    observed_destination_ids=observed_destination_ids,
                    preserved_descendants=preservation,
                    reparent_error=str(exc),
                ) from exc
            raise

        last_candidate: dict[str, Any] | None = None
        last_error: RuntimeError | None = None

        def observe_reparent():
            nonlocal last_candidate, last_error
            try:
                candidate = self._capture_reparent_snapshot(notebook_id)
                last_candidate = candidate
                if resource_type == "page":
                    current, id_map, verified = self._validate_reparent_page_scope(
                        before,
                        candidate,
                        selected=(complete_scope if include_descendants else complete_scope[:1]),
                        destination_section_id=destination_parent_id,
                        include_descendants=include_descendants,
                    )
                else:
                    current, id_map, verified = self._validate_reparent_snapshots(
                        before,
                        candidate,
                        target_id=object_id,
                        destination_parent_id=destination_parent_id,
                        resource_type=resource_type,
                    )
                return {
                    "after": candidate,
                    "current": current,
                    "id_map": id_map,
                    "verified": verified,
                }
            except OneNoteError:
                raise
            except RuntimeError as exc:
                last_error = exc
                return None

        stable = converge(
            observe_reparent,
            lambda value: value is not None,
            lambda value: (
                value["current"]["id"],
                tuple(sorted(value["id_map"].items())),
                tuple(
                    sorted(
                        (item["id"], item.get("parent_id"), item.get("section_id"))
                        for item in value["after"]["items"]
                    )
                ),
            ),
            config=DEFAULT_CONVERGENCE,
            clock=time.monotonic,
            sleeper=time.sleep,
            transient=transient_read_error,
        )
        if not stable.converged or stable.value is None:
            if resource_type == "page":
                partial_position, active_source_ids, observed_destination_ids = (
                    self._partial_reparent_page_position(
                        before,
                        last_candidate,
                        selected_source_ids=[str(item["id"]) for item in complete_scope],
                        destination_section_id=destination_parent_id,
                    )
                )
                raise PartialFailure(
                    f"Page Reparent returned, but scope read-back verification failed: {last_error}",
                    partial=True,
                    outcome="reparent_subtree_incomplete",
                    reparent_attempted=True,
                    include_descendants=bool(include_descendants),
                    destination_position=partial_position,
                    active_source_ids=active_source_ids,
                    observed_destination_ids=observed_destination_ids,
                    preserved_descendants=preservation,
                    manual_recovery_required=True,
                )
            raise RuntimeError(
                f"Reparent returned success, but read-back verification failed: {last_error}"
            )
        after = stable.value["after"]
        current = stable.value["current"]
        id_map = stable.value["id_map"]
        verified = stable.value["verified"]
        return {
            "item": current,
            "destination_position": destination_position(
                after["items"],
                str(current["id"]),
            ),
            "previous_parent_id": expected_parent_id,
            "destination_parent_id": destination_parent_id,
            "id_map": id_map,
            "verified": verified,
            "convergence": stable.summary(),
            **(
                {
                    "include_descendants": bool(include_descendants),
                    "preserved_descendants": preservation,
                }
                if resource_type == "page"
                else {}
            ),
            "warnings": [
                "Experimental COM behavior verified only for the documented isolated OneNote/Office scenarios."
            ],
        }

    def reparent_page(
        self,
        page_id: str,
        destination_section_id: str,
        expected_title: str,
        expected_section_id: str,
        expected_modified: str | None = None,
        include_descendants: bool = False,
    ) -> dict[str, Any]:
        return self._reparent(
            page_id,
            "page",
            destination_section_id,
            expected_title,
            expected_section_id,
            expected_modified,
            include_descendants,
        )

    def reparent_section(
        self,
        section_id: str,
        destination_parent_id: str,
        expected_name: str,
        expected_parent_id: str,
        expected_modified: str | None = None,
    ) -> dict[str, Any]:
        return self._reparent(
            section_id,
            "section",
            destination_parent_id,
            expected_name,
            expected_parent_id,
            expected_modified,
        )

    def reparent_section_group(
        self,
        section_group_id: str,
        destination_parent_id: str,
        expected_name: str,
        expected_parent_id: str,
        expected_modified: str | None = None,
    ) -> dict[str, Any]:
        return self._reparent(
            section_group_id,
            "section_group",
            destination_parent_id,
            expected_name,
            expected_parent_id,
            expected_modified,
        )

    def append_to_page(
        self,
        page_id: str,
        content: str,
        expected_title: str,
        expected_section_id: str,
        expected_modified: str | None = None,
        content_format: str = "plain",
        x: float | None = None,
        y: float | None = None,
    ) -> dict[str, Any]:
        MutationPolicy.current().require_write()
        before = self.pages.confirm(
            page_id,
            expected_title=expected_title,
            expected_section_id=expected_section_id,
            expected_modified=expected_modified,
        )
        before_xml = self.pages.xml(page_id, "all")
        before_hash = self.pages.digest(before_xml)
        xml = build_page_update_xml(
            page_id,
            content=content,
            content_format=content_format,
            x=x,
            y=y,
            existing_tag_definitions=tag_definitions_from_page_xml(before_xml),
        )
        observe = lambda: self.pages.digest(self.pages.xml(page_id, "all"))
        reconciliation = self._reconciled_idempotent_execute(
            operation="append_to_page",
            execute=lambda: self.call(
                "update_page_content", xml=xml, schema=XML_SCHEMA_2013, force=False
            ),
            observe=observe,
            is_pre_state=lambda digest: digest == before_hash,
            is_post_state=lambda digest: digest != before_hash,
        )
        stable = self._converge(
            operation="append_to_page",
            observe=observe,
            accept=lambda digest: digest != before_hash,
            project_identity=lambda digest: digest,
            failure_message="Append was accepted, but Page content did not converge.",
        )
        item = self.hierarchy.wait_for(page_id, "page")
        if item is None:
            raise OneNoteConvergenceTimeoutError(
                "Append content converged, but Page hierarchy identity did not stabilize.",
                operation="append_to_page",
                partial=True,
                reconciliation="indeterminate",
                details={
                    "convergence": self.hierarchy.last_convergence_summary(),
                    "manual_recovery_required": True,
                },
            )
        return {
            "item": item,
            "before_modified": before.get("modified"),
            "appended": True,
            "convergence": stable.summary(),
            "reconciliation": reconciliation.summary(),
        }

    def add_image_to_page(
        self,
        page_id: str,
        image_path: str,
        expected_title: str,
        expected_section_id: str,
        expected_modified: str | None = None,
        image_format: str = "",
        x: float = 36.0,
        y: float = 120.0,
        width: float | None = None,
        height: float | None = None,
    ) -> dict[str, Any]:
        MutationPolicy.current().require_write()
        self.pages.confirm(
            page_id,
            expected_title=expected_title,
            expected_section_id=expected_section_id,
            expected_modified=expected_modified,
        )
        before_hash = self.pages.digest(self.pages.xml(page_id, "all"))
        path = Path(image_path)
        if not path.is_file():
            raise ValueError(f"Image file not found: {image_path}")
        fmt = image_format or path.suffix.lstrip(".")
        if not fmt:
            raise ValueError("image_format is required when image_path has no extension.")
        resolved_width, resolved_height = proportional_dimensions(path, width, height)
        xml = build_image_page_update_xml(
            page_id,
            image_base64=base64.b64encode(path.read_bytes()).decode("ascii"),
            image_format=fmt,
            x=x,
            y=y,
            width=resolved_width,
            height=resolved_height,
        )
        observe = lambda: self.pages.digest(self.pages.xml(page_id, "all"))
        reconciliation = self._reconciled_idempotent_execute(
            operation="add_image_to_page",
            execute=lambda: self.call(
                "update_page_content", xml=xml, schema=XML_SCHEMA_2013, force=False
            ),
            observe=observe,
            is_pre_state=lambda digest: digest == before_hash,
            is_post_state=lambda digest: digest != before_hash,
        )
        stable = self._converge(
            operation="add_image_to_page",
            observe=observe,
            accept=lambda digest: digest != before_hash,
            project_identity=lambda digest: digest,
            failure_message="Image update was accepted, but Page content did not converge.",
        )
        item = self.hierarchy.wait_for(page_id, "page")
        if item is None:
            raise OneNoteConvergenceTimeoutError(
                "Image content converged, but Page hierarchy identity did not stabilize.",
                operation="add_image_to_page",
                partial=True,
                reconciliation="indeterminate",
                details={
                    "convergence": self.hierarchy.last_convergence_summary(),
                    "manual_recovery_required": True,
                },
            )
        return {
            "item": item,
            "image_path": str(path),
            "width": resolved_width,
            "height": resolved_height,
            "convergence": stable.summary(),
            "reconciliation": reconciliation.summary(),
        }

    def replace_page_body(
        self,
        page_id: str,
        content: str,
        expected_title: str,
        expected_section_id: str,
        expected_modified: str | None = None,
        title: str | None = None,
        content_format: str = "plain",
    ) -> dict[str, Any]:
        policy = MutationPolicy.current()
        policy.require_write()
        policy.require_delete()
        self.pages.confirm(
            page_id,
            expected_title=expected_title,
            expected_section_id=expected_section_id,
            expected_modified=expected_modified,
        )
        page_xml = self.pages.xml(page_id, "all")
        before_hash = self.pages.digest(page_xml)
        deleted: list[str] = []
        try:
            for obj in collect_page_objects(page_xml):
                if obj.get("type") not in REPLACE_BODY_OBJECT_TYPES:
                    continue
                object_id = obj.get("object_id")
                if not object_id:
                    continue
                self.call("delete_page_content", page_id=page_id, object_id=object_id, force=False)
                deleted.append(object_id)
            update_xml = build_page_update_xml(
                    page_id,
                    title=title,
                    content=content,
                    content_format=content_format,
                    existing_tag_definitions=tag_definitions_from_page_xml(page_xml),
                )
            observe = lambda: self.pages.digest(self.pages.xml(page_id, "all"))
            reconciliation = self._reconciled_idempotent_execute(
                operation="replace_page_body",
                execute=lambda: self.call(
                    "update_page_content",
                    xml=update_xml,
                    schema=XML_SCHEMA_2013,
                    force=False,
                ),
                observe=observe,
                is_pre_state=lambda digest: digest == before_hash and not deleted,
                is_post_state=lambda digest: digest != before_hash,
                is_partial_state=lambda digest: bool(deleted) and digest == before_hash,
            )
            stable = self._converge(
                operation="replace_page_body",
                observe=observe,
                accept=lambda digest: digest != before_hash,
                project_identity=lambda digest: digest,
                failure_message="Page rebuild was accepted, but content did not converge.",
            )
        except Exception as exc:
            if deleted:
                raise PartialFailure(
                    str(exc),
                    partial=True,
                    completed_steps=[{"operation": "delete_page_content", "object_id": value} for value in deleted],
                    reconciliation="partially_applied",
                    manual_recovery_required=True,
                ) from exc
            raise
        item = self.hierarchy.wait_for(page_id, "page")
        if item is None:
            raise PartialFailure(
                "Page body content converged, but hierarchy identity did not stabilize.",
                partial=True,
                reconciliation="indeterminate",
                manual_recovery_required=True,
                completed_steps=[{"operation": "delete_page_content", "object_id": value} for value in deleted],
                convergence=self.hierarchy.last_convergence_summary(),
            )
        return {
            "item": item,
            "deleted_objects": deleted,
            "replaced": True,
            "partial": False,
            "convergence": stable.summary(),
            "reconciliation": reconciliation.summary(),
        }

    def delete_page_content(
        self,
        page_id: str,
        object_id: str,
        expected_title: str,
        expected_section_id: str,
        expected_modified: str | None = None,
    ) -> dict[str, Any]:
        MutationPolicy.current().require_delete()
        self.pages.confirm(
            page_id,
            expected_title=expected_title,
            expected_section_id=expected_section_id,
            expected_modified=expected_modified,
        )
        objects = collect_page_objects(self.pages.xml(page_id, "all"))
        matched = next((obj for obj in objects if obj.get("object_id") == object_id), None)
        if matched and not matched.get("delete_supported"):
            suggested_id = matched.get("delete_object_id")
            if suggested_id:
                raise ValueError(
                    f"Object '{object_id}' is a {matched.get('type')} child and is not directly deletable by OneNote COM. "
                    f"Delete its parent content object '{suggested_id}' instead."
                )
            allowed = ", ".join(sorted(DELETABLE_PAGE_OBJECT_TYPES))
            raise ValueError(
                f"Object '{object_id}' is a {matched.get('type')} child and is not directly deletable by OneNote COM. "
                f"Deletable object types: {allowed}."
            )
        if matched is None or not matched.get("delete_supported"):
            raise ValueError("object_id is not a currently verified deletable page content object.")
        def observe():
            return collect_page_objects(self.pages.xml(page_id, "all"))

        reconciliation = self._reconciled_idempotent_execute(
            operation="delete_page_content",
            execute=lambda: self.call(
                "delete_page_content", page_id=page_id, object_id=object_id, force=False
            ),
            observe=observe,
            is_pre_state=lambda values: any(item.get("object_id") == object_id for item in values),
            is_post_state=lambda values: not any(item.get("object_id") == object_id for item in values),
        )
        stable = self._converge(
            operation="delete_page_content",
            observe=observe,
            accept=lambda values: not any(item.get("object_id") == object_id for item in values),
            project_identity=lambda values: tuple(
                sorted(str(item.get("object_id")) for item in values if item.get("object_id"))
            ),
            failure_message="Delete was accepted, but the Page content object remained visible.",
        )
        return {
            "page_id": page_id,
            "object_id": object_id,
            "deleted": True,
            "convergence": stable.summary(),
            "reconciliation": reconciliation.summary(),
        }

    def delete_resource(
        self,
        object_id: str,
        resource_type: str,
        expected_name: str,
        expected_parent_id: str,
        expected_modified: str | None,
        permanently: bool,
    ) -> dict[str, Any]:
        MutationPolicy.current().require_delete(permanently=permanently)
        item = self.confirm_resource(
            object_id,
            resource_type,
            expected_name=expected_name,
            expected_parent_id=expected_parent_id,
            expected_modified=expected_modified,
        )
        def observe():
            try:
                return self.hierarchy.resource(object_id, resource_type)
            except ValueError:
                return None

        postcondition = lambda value: value is None or (
            not permanently and value.get("is_in_recycle_bin") is True
        )
        reconciliation = reconcile_mutation(
            execute=lambda: self.call(
                "delete_hierarchy", object_id=object_id, permanently=permanently
            ),
            observe=observe,
            is_pre_state=lambda value: value is not None
            and value.get("is_in_recycle_bin") is not True,
            is_post_state=postcondition,
            is_partial_state=lambda value: value is not None
            and value.get("is_in_recycle_bin") is True
            and permanently,
            retry_if_unchanged=False,
        )
        self._raise_failed_reconciliation("delete_hierarchy", reconciliation)
        stable = self._converge(
            operation="delete_hierarchy",
            observe=observe,
            accept=postcondition,
            project_identity=lambda value: self.hierarchy._resource_identity(value),
            failure_message="Delete was accepted, but the object state did not converge.",
        )
        return {
            "item": item,
            "object_id": object_id,
            "permanently": permanently,
            "deleted": True,
            "final_state": stable.value,
            "convergence": stable.summary(),
            "reconciliation": reconciliation.summary(),
        }

    def delete_page(
        self,
        page_id: str,
        expected_title: str,
        expected_section_id: str,
        expected_modified: str | None = None,
        permanently: bool = False,
    ) -> dict[str, Any]:
        page = self.pages.confirm(
            page_id,
            expected_title=expected_title,
            expected_section_id=expected_section_id,
            expected_modified=expected_modified,
        )
        return self.delete_resource(
            page_id, "page", page["title"], page["parent_id"], expected_modified, permanently
        )

    def update_page_xml(self, xml: str) -> dict[str, Any]:
        policy = MutationPolicy.current()
        policy.require_raw_xml()
        policy.require_write()
        self.call("update_page_content", xml=xml, schema=XML_SCHEMA_2013, force=False)
        return {"updated": True}
