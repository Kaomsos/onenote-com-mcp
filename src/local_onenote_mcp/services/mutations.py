"""Typed OneNote mutation service with policy, confirmation, and read-back."""

from __future__ import annotations

import base64
from datetime import datetime
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
    image_file_format,
    proportional_dimensions,
    tag_definitions_from_page_xml,
)
from ..policy import CopyBudget, MutationPolicy
from .base import BaseService
from .convergence import (
    DEFAULT_CONVERGENCE,
    ConvergenceConfig,
    ConvergenceResult,
    converge,
)
from .errors import MutationFailure, MutationPreflightFailure, PartialFailure
from .hierarchy import HierarchyService
from .mutation_control import (
    MutationAttemptExecutor,
    MutationAttemptOutcome,
    mutation_attempt_policy,
)
from .operation_runtime import record_backend_call
from .pages import PageService, stable_page_content_digest
from .position import destination_position, unavailable_destination_position
from .reconciliation import ReconciliationState, reconcile_mutation


REPLACE_BODY_OBJECT_TYPES = {"Outline", "Image", "InkDrawing", "FileAttachment", "InsertedFile", "MediaFile"}
MAX_BATCH_ITEMS = 20
MAX_SORT_CHILDREN = 1_000
SECTION_GROUP_REPARENT_CONVERGENCE = ConvergenceConfig(
    deadline_seconds=DEFAULT_CONVERGENCE.deadline_seconds,
    interval_seconds=DEFAULT_CONVERGENCE.interval_seconds,
    required_stable_observations=4,
    max_observations=DEFAULT_CONVERGENCE.max_observations,
)


class MutationService(BaseService):
    def __init__(self, bridge: OneNoteBridge, hierarchy: HierarchyService, pages: PageService) -> None:
        super().__init__(bridge)
        self.hierarchy = hierarchy
        self.pages = pages
        self.mutation_attempts = MutationAttemptExecutor()

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

    def _execute_mutation_attempt(
        self,
        *,
        operation: str,
        execute,
        observe,
        is_pre_state,
        is_post_state,
        is_partial_state=None,
        retry_observation_if=None,
        observation_retry=None,
    ) -> MutationAttemptOutcome[Any, Any]:
        outcome = self.mutation_attempts.execute(
            mutation_attempt_policy(operation),
            execute=execute,
            observe=observe,
            is_pre_state=is_pre_state,
            is_post_state=is_post_state,
            is_partial_state=is_partial_state,
            retry_observation_if=retry_observation_if,
            observation_retry=observation_retry,
        )
        self._raise_failed_controlled_outcome(outcome)
        return outcome

    @staticmethod
    def _raise_failed_controlled_outcome(
        outcome: MutationAttemptOutcome[Any, Any],
        *,
        completed_steps: list[dict[str, Any]] | None = None,
        extra_details: dict[str, Any] | None = None,
    ) -> None:
        if outcome.applied:
            return
        result = outcome.reconciliation
        details = outcome.failure_details()
        details.update(extra_details or {})
        if result.state is ReconciliationState.NOT_APPLIED:
            if isinstance(result.error, OneNoteError):
                result.error.reconciliation = result.state.value
                result.error.details.update(details)
                raise result.error
            raise MutationFailure(
                f"{outcome.policy.policy_id} did not apply; live pre-state remained unchanged.",
                code="mutation_not_applied",
                partial=False,
                reconciliation=result.state.value,
                **details,
            ) from result.error
        raise PartialFailure(
            f"{outcome.policy.policy_id} did not reach a safely verified postcondition.",
            partial=result.state is ReconciliationState.PARTIALLY_APPLIED,
            reconciliation=result.state.value,
            completed_steps=list(completed_steps or []),
            failed_step=outcome.policy.policy_id,
            **details,
        ) from result.error

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
        policy = MutationPolicy.current()
        if self.create_resource_type(normalized_create_type) is not None:
            policy.require_create()
        else:
            policy.require_write()
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
        MutationPolicy.current().require_create()
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
        MutationPolicy.current().require_create()
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
        MutationPolicy.current().require_create()
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
        policy = MutationPolicy.current()
        policy.require_create()
        policy.require_write()
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

        reconciliation = self._execute_mutation_attempt(
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

        reconciliation = self._execute_mutation_attempt(
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

        reconciliation = self._execute_mutation_attempt(
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

    def _reorder_container(
        self,
        object_id: str,
        resource_type: str,
        expected_name: str,
        expected_parent_id: str,
        after_id: str,
        expected_modified: str | None,
    ) -> dict[str, Any]:
        MutationPolicy.current().require_section_reorder(resource_type)
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
        execute_reorder = lambda: self.call(
            "update_hierarchy", xml=update_xml, schema=XML_SCHEMA_2013
        )
        reconciliation = (
            self._execute_mutation_attempt(
                operation="reorder_section",
                execute=execute_reorder,
                observe=lambda: self.hierarchy.resources(include_recycle_bin=False),
                is_pre_state=lambda values: direct_signature(values)
                == before_direct_signature,
                is_post_state=lambda values: direct_signature(values)
                == expected_direct_signature,
                is_partial_state=lambda values: direct_signature(values)[1]
                != before_direct_signature[1],
            )
            if resource_type == "section"
            else self._reconciled_idempotent_execute(
                operation=f"reorder_{resource_type}",
                execute=execute_reorder,
                observe=lambda: self.hierarchy.resources(include_recycle_bin=False),
                is_pre_state=lambda values: direct_signature(values)
                == before_direct_signature,
                is_post_state=lambda values: direct_signature(values)
                == expected_direct_signature,
                is_partial_state=lambda values: direct_signature(values)[1]
                != before_direct_signature[1],
            )
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
            },
            "verification_scope": {"page_content": "not_read"},
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

    def _capture_reparent_hierarchy(self, notebook_id: str) -> list[dict[str, Any]]:
        """Capture one bounded, content-free hierarchy observation for Reparent."""

        budget = CopyBudget.current()
        items = self._notebook_items(
            self.hierarchy.resources(include_recycle_bin=False), notebook_id
        )
        if not any(item.get("id") == notebook_id for item in items):
            raise ValueError(f"No active notebook found for ID '{notebook_id}'.")
        if len(items) > budget.max_resources:
            raise ValueError(
                "Reparent verification exceeds the configured hierarchy resource budget."
            )
        if sum(item.get("resource_type") == "page" for item in items) > budget.max_pages:
            raise ValueError("Reparent verification exceeds the configured Page budget.")
        return items

    @staticmethod
    def _reparent_hierarchy_signature(items: list[dict[str, Any]]) -> tuple[Any, ...]:
        """Return a content-free signature including relationships and sibling order."""

        relationships = tuple(
            sorted(
                (
                    str(item.get("id", "")),
                    str(item.get("resource_type", "")),
                    str(item.get("parent_id") or ""),
                    str(item.get("section_id") or ""),
                    int(item.get("page_level") or 0),
                    str(item.get("parent_page_id") or ""),
                    int(item.get("order") or 0),
                )
                for item in items
            )
        )
        children: dict[str, list[tuple[str, str]]] = {}
        for item in items:
            if item.get("resource_type") == "notebook":
                continue
            parent_id = (
                item.get("section_id")
                if item.get("resource_type") == "page"
                else item.get("parent_id")
            )
            if parent_id:
                children.setdefault(str(parent_id), []).append(
                    (str(item.get("resource_type", "")), str(item.get("id", "")))
                )
        sibling_order = tuple(
            (parent_id, tuple(sequence))
            for parent_id, sequence in sorted(children.items())
        )
        return relationships, sibling_order

    @staticmethod
    def _reparent_confirmation_signature(item: dict[str, Any] | None) -> tuple[Any, ...] | None:
        """Project the hierarchy facts that authorize a native Reparent.

        OneNote can advance ``modified`` on a Page or container while it
        finishes persisting an already-observed hierarchy.  Reparent binds the
        caller's clock once above, then uses this semantic projection to make
        sure the hierarchy evidence capture still names the same typed object and
        relationship.  Volatile clocks and derived aggregate fields therefore
        cannot be the sole reason a native move is rejected.
        """

        if item is None:
            return None
        return (
            str(item.get("id", "")),
            str(item.get("resource_type", "")),
            str(item.get("name") or ""),
            str(item.get("title") or ""),
            str(item.get("parent_id") or ""),
            str(item.get("notebook_id") or ""),
            str(item.get("section_id") or ""),
            int(item.get("page_level") or 0),
            str(item.get("parent_page_id") or ""),
            int(item.get("order") or 0),
            bool(item.get("is_in_recycle_bin") is True),
        )

    def _capture_reparent_snapshot(self, notebook_id: str) -> dict[str, Any]:
        """Capture a bounded, stable, content-free hierarchy for Reparent.

        Production Reparent establishes its postcondition from typed hierarchy
        identity, parentage and sibling order.  Full Page XML comparison is
        intentionally owned by the human-gated validation scenarios instead:
        reading every Page in a Notebook makes Section/SectionGroup Reparent
        latency scale with unrelated Page content.
        """

        initial = self._capture_reparent_hierarchy(notebook_id)
        refreshed = self._capture_reparent_hierarchy(notebook_id)
        if self._reparent_hierarchy_signature(refreshed) != self._reparent_hierarchy_signature(
            initial
        ):
            raise RuntimeError(
                "Notebook hierarchy changed while Reparent verification evidence was collected."
            )
        return {"items": refreshed}

    def _reparent_topology_target(
        self,
        before_items: list[dict[str, Any]],
        after_items: list[dict[str, Any]],
        *,
        target_id: str,
        destination_parent_id: str,
        resource_type: str,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """Resolve the moved target using hierarchy evidence only."""

        before_by_id = {str(item["id"]): item for item in before_items}
        after_by_id = {str(item["id"]): item for item in after_items}
        before_target = before_by_id[target_id]
        if resource_type != "page":
            current = after_by_id.get(target_id)
            if current is None or current.get("resource_type") != resource_type:
                raise RuntimeError("Reparent hierarchy read-back is missing the typed target.")
            if current.get("parent_id") != destination_parent_id:
                raise RuntimeError("Reparent hierarchy read-back has not observed the requested parent.")
            return current, {target_id: target_id}

        added_ids = set(after_by_id) - set(before_by_id)
        candidates = [
            item
            for item in after_items
            if item.get("resource_type") == "page"
            and item.get("section_id") == destination_parent_id
            and int(item.get("page_level") or 0) == 1
            and item.get("parent_page_id") in {None, ""}
            and str(item.get("id", "")) in ({target_id} | added_ids)
            and display_name(item) == display_name(before_target)
        ]
        if len(candidates) != 1:
            raise RuntimeError(
                "Page Reparent hierarchy read-back did not identify one unique destination root."
            )
        current = candidates[0]
        return current, {target_id: str(current["id"])}

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

        if resource_type == "page":
            raise ValueError("Page Reparent uses its scoped hierarchy validator.")
        verified = {
            "parent_applied": True,
            f"{resource_type}_id_preserved": True,
            "same_notebook_preserved": True,
            "descendant_topology_preserved": True,
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
            ):
                raise RuntimeError("Descendant promotion did not preserve Page topology.")
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
            ):
                raise RuntimeError("Page reparent changed selected Page topology.")
            if current_id in reverse_ids:
                raise RuntimeError("Page reparent produced a non-injective Page ID mapping.")
            id_map[source_id] = current_id
            reverse_ids.add(current_id)

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

        return current_root, id_map, {
            "parent_applied": True,
            "target_id_transition_valid": True,
            "same_notebook_preserved": True,
            "page_scope_complete": True,
            "page_topology_preserved": True,
            "page_ids_mapped": True,
            "unrelated_objects_preserved": True,
        }

    def _reparent_snapshot_signature(self, snapshot: dict[str, Any]) -> tuple[Any, ...]:
        """Project the frozen hierarchy state used to prove not-applied."""

        hierarchy = tuple(
            sorted(
                (
                    str(item.get("id", "")),
                    self._reparent_confirmation_signature(item),
                )
                for item in snapshot["items"]
            )
        )
        return hierarchy

    def _observe_reparent_state(
        self,
        before: dict[str, Any],
        *,
        notebook_id: str,
        object_id: str,
        resource_type: str,
        destination_parent_id: str,
        selected: list[dict[str, Any]],
        include_descendants: bool,
        preservation: dict[str, Any],
        snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Observe one content-free Reparent state for success and error paths."""

        snapshot = snapshot or self._capture_reparent_snapshot(notebook_id)
        validated: tuple[dict[str, Any], dict[str, str], dict[str, bool]] | None = None
        validation_error: Exception | None = None
        try:
            if resource_type == "page":
                validated = self._validate_reparent_page_scope(
                    before,
                    snapshot,
                    selected=selected,
                    destination_section_id=destination_parent_id,
                    include_descendants=include_descendants,
                )
            else:
                validated = self._validate_reparent_snapshots(
                    before,
                    snapshot,
                    target_id=object_id,
                    destination_parent_id=destination_parent_id,
                    resource_type=resource_type,
                )
        except Exception as exc:
            validation_error = exc

        exact_pre_state = (
            not preservation.get("promoted")
            and self._reparent_snapshot_signature(snapshot)
            == self._reparent_snapshot_signature(before)
        )
        ambiguous_destination = False
        if resource_type == "page" and validated is None:
            _position, _source_ids, destination_ids = self._partial_reparent_page_position(
                before,
                snapshot,
                selected_source_ids=[str(item["id"]) for item in selected],
                destination_section_id=destination_parent_id,
            )
            ambiguous_destination = len(destination_ids) > 1
        changed = not exact_pre_state
        partial = bool(preservation.get("promoted")) or (
            changed and not ambiguous_destination
        )
        return {
            "snapshot": snapshot,
            "validated": validated,
            "validation_error": validation_error,
            "exact_pre_state": exact_pre_state,
            "partial": partial,
            "ambiguous_destination": ambiguous_destination,
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
        observed = candidates
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

    def _reparent_impl(
        self,
        object_id: str,
        resource_type: str,
        destination_parent_id: str,
        expected_name: str,
        expected_parent_id: str,
        expected_modified: str | None,
        include_descendants: bool = False,
    ) -> dict[str, Any]:
        MutationPolicy.current().require_organize()
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
        if (
            self._reparent_confirmation_signature(snap_target)
            != self._reparent_confirmation_signature(target)
            or self._reparent_confirmation_signature(snap_destination)
            != self._reparent_confirmation_signature(destination)
        ):
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
        operation = f"reparent_{resource_type}"
        execution_result: dict[str, Any] | None = None
        execution_error: Exception | None = None
        try:
            execution_result = self.call(
                "update_hierarchy",
                xml=xml,
                schema=XML_SCHEMA_2013,
            )
        except Exception as exc:
            execution_error = exc

        last_topology: dict[str, Any] | None = None
        last_topology_error: RuntimeError | None = None

        def observe_reparent_topology():
            nonlocal last_topology, last_topology_error
            items = self._capture_reparent_hierarchy(notebook_id)
            try:
                current, id_map = self._reparent_topology_target(
                    before["items"],
                    items,
                    target_id=object_id,
                    destination_parent_id=destination_parent_id,
                    resource_type=resource_type,
                )
            except RuntimeError as exc:
                last_topology_error = exc
                return None
            last_topology = {"items": items, "current": current, "id_map": id_map}
            return last_topology

        convergence_config = (
            SECTION_GROUP_REPARENT_CONVERGENCE
            if resource_type == "section_group"
            else DEFAULT_CONVERGENCE
        )
        stable: ConvergenceResult[Any] | None = None
        topology_read_error: Exception | None = None
        try:
            stable = converge(
                observe_reparent_topology,
                lambda value: value is not None,
                lambda value: (
                    str(value["current"]["id"]),
                    tuple(sorted(value["id_map"].items())),
                    self._reparent_hierarchy_signature(value["items"]),
                ),
                config=convergence_config,
                clock=time.monotonic,
                sleeper=time.sleep,
                transient=transient_read_error,
            )
        except Exception as exc:
            topology_read_error = exc
        if stable is not None and (not stable.converged or stable.value is None):
            diagnostic = (
                str(last_topology_error)
                if last_topology_error is not None
                else (
                    "deadline exceeded after "
                    f"{stable.stable_observations}/"
                    f"{convergence_config.required_stable_observations} stable observations"
                )
            )
            topology_read_error = RuntimeError(diagnostic)

        after: dict[str, Any] | None = None
        capture_error: Exception | None = None
        capture_attempts = 0
        for capture_attempts in range(1, 3):
            try:
                after = self._capture_reparent_snapshot(notebook_id)
                capture_error = None
                break
            except Exception as exc:
                capture_error = exc
                retryable_capture = isinstance(exc, RuntimeError) or (
                    isinstance(exc, OneNoteError) and transient_read_error(exc)
                )
                if capture_attempts == 1 and retryable_capture:
                    time.sleep(DEFAULT_CONVERGENCE.interval_seconds)
                    continue
                break
        if after is None:
            assert capture_error is not None
            reconciliation = self.mutation_attempts.reconcile_observation(
                mutation_attempt_policy(operation),
                observation=None,
                is_pre_state=lambda _value: False,
                is_post_state=lambda _value: False,
                execution_result=execution_result,
                execution_error=execution_error,
                execution_succeeded=execution_error is None,
                observation_error=capture_error,
                observation_attempts=capture_attempts,
            )
            details: dict[str, Any] = {
                "outcome": "reparent_readback_incomplete",
                "reparent_attempted": True,
                "readback_phase": "hierarchy_evidence_capture",
                "readback_error_type": type(capture_error).__name__,
                "capture_attempts": capture_attempts,
                "convergence": stable.summary() if stable is not None else {
                    "converged": False,
                    "attempts": 0,
                    "stable_observations": 0,
                    "transient_errors": [],
                    "identity_remap": {},
                },
                "mutation_replayed": False,
            }
            if resource_type == "page" and stable is not None and stable.value is not None:
                topology_current = stable.value["current"]
                topology_items = stable.value["items"]
                topology_by_id = {
                    str(item["id"]): item for item in topology_items
                }
                selected_source_ids = [
                    str(item["id"])
                    for item in (
                        complete_scope
                        if include_descendants
                        else complete_scope[:1]
                    )
                ]
                details.update(
                    outcome="reparent_subtree_incomplete",
                    include_descendants=bool(include_descendants),
                    destination_position=destination_position(
                        topology_items, str(topology_current["id"])
                    ),
                    active_source_ids=[
                        source_id
                        for source_id in selected_source_ids
                        if source_id in topology_by_id
                        and topology_by_id[source_id].get("section_id")
                        != destination_parent_id
                    ],
                    observed_destination_ids=[str(topology_current["id"])],
                    preserved_descendants=preservation,
                )
            self._raise_failed_controlled_outcome(
                reconciliation,
                completed_steps=(
                    [{"operation": "promote_reparent_descendants"}]
                    if preservation.get("promoted")
                    else []
                ),
                extra_details=details,
            )
            raise AssertionError("indeterminate Reparent outcome did not raise")

        observed_state = self._observe_reparent_state(
            before,
            notebook_id=notebook_id,
            object_id=object_id,
            resource_type=resource_type,
            destination_parent_id=destination_parent_id,
            selected=(
                complete_scope
                if resource_type == "page" and include_descendants
                else selected
            ),
            include_descendants=include_descendants,
            preservation=preservation,
            snapshot=after,
        )
        if (
            last_topology is not None
            and self._reparent_hierarchy_signature(last_topology["items"])
            != self._reparent_hierarchy_signature(after["items"])
        ):
            observed_state.update(
                validated=None,
                validation_error=RuntimeError(
                    "Reparent topology and hierarchy evidence observations did not agree."
                ),
                exact_pre_state=False,
                partial=False,
                evidence_inconsistent=True,
            )
        reconciliation = self.mutation_attempts.reconcile_observation(
            mutation_attempt_policy(operation),
            observation=observed_state,
            is_pre_state=lambda value: bool(value["exact_pre_state"]),
            is_post_state=lambda value: value["validated"] is not None,
            is_partial_state=lambda value: bool(value["partial"]),
            execution_result=execution_result,
            execution_error=execution_error,
            execution_succeeded=execution_error is None,
            observation_attempts=capture_attempts,
        )
        if not reconciliation.applied:
            details = {
                "outcome": "reparent_readback_incomplete",
                "reparent_attempted": True,
                "readback_phase": "invariant_validation",
                "readback_error_type": (
                    type(observed_state["validation_error"]).__name__
                    if observed_state["validation_error"] is not None
                    else None
                ),
                "capture_attempts": capture_attempts,
                "convergence": stable.summary() if stable is not None else {
                    "converged": False,
                    "attempts": 0,
                    "stable_observations": 0,
                    "transient_errors": [],
                    "identity_remap": {},
                },
                "mutation_replayed": False,
            }
            if resource_type == "page":
                partial_position, active_source_ids, observed_destination_ids = (
                    self._partial_reparent_page_position(
                        before,
                        after,
                        selected_source_ids=[
                            str(item["id"])
                            for item in (
                                complete_scope
                                if include_descendants
                                else complete_scope[:1]
                            )
                        ],
                        destination_section_id=destination_parent_id,
                    )
                )
                details.update(
                    outcome=(
                        "descendants_promoted_reparent_not_completed"
                        if preservation.get("promoted")
                        else "reparent_subtree_incomplete"
                    ),
                    include_descendants=bool(include_descendants),
                    destination_position=partial_position,
                    active_source_ids=active_source_ids,
                    observed_destination_ids=observed_destination_ids,
                    preserved_descendants=preservation,
                )
            self._raise_failed_controlled_outcome(
                reconciliation,
                completed_steps=(
                    [{"operation": "promote_reparent_descendants"}]
                    if preservation.get("promoted")
                    else []
                ),
                extra_details=details,
            )
            raise AssertionError("failed Reparent outcome did not raise")

        validated = observed_state["validated"]
        assert validated is not None
        current, id_map, verified = validated
        if stable is None or not stable.converged or stable.value is None:
            raise PartialFailure(
                "Reparent postcondition was observed, but hierarchy convergence was indeterminate.",
                partial=False,
                reconciliation="indeterminate",
                mutation_stage="postcondition",
                mutation_attempted=True,
                mutation_attempts=1,
                mutation_replayed=False,
                observed_outcome="indeterminate",
                preflight_state="logical_ready",
                persistence_checkpoint="not_observable",
                retry_safety="do_not_replay",
                recommended_action="query_current_state_with_read_only_tools_before_recovery",
                manual_recovery_required=True,
                readback_phase="hierarchy_convergence",
                readback_error_type=(
                    type(topology_read_error).__name__
                    if topology_read_error is not None
                    else "ConvergenceIncomplete"
                ),
                convergence=stable.summary() if stable is not None else None,
            ) from topology_read_error
        try:
            observed_destination_position = destination_position(
                after["items"],
                str(current["id"]),
            )
        except RuntimeError as exc:
            raise PartialFailure(
                "Reparent postcondition converged, but its destination position could not be projected.",
                partial=False,
                reconciliation="indeterminate",
                mutation_stage="postcondition",
                mutation_attempted=True,
                mutation_attempts=1,
                mutation_replayed=False,
                observed_outcome="indeterminate",
                preflight_state="logical_ready",
                persistence_checkpoint="not_observable",
                retry_safety="do_not_replay",
                recommended_action="query_current_state_with_read_only_tools_before_recovery",
                manual_recovery_required=True,
                readback_phase="destination_position",
                readback_error_type=type(exc).__name__,
                convergence=stable.summary(),
            ) from exc
        return {
            "item": current,
            "destination_position": observed_destination_position,
            "previous_parent_id": expected_parent_id,
            "destination_parent_id": destination_parent_id,
            "id_map": id_map,
            "verified": verified,
            "convergence": stable.summary(),
            "reconciliation": reconciliation.summary(),
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
        policy = mutation_attempt_policy(f"reparent_{resource_type}")
        try:
            return self._reparent_impl(
                object_id,
                resource_type,
                destination_parent_id,
                expected_name,
                expected_parent_id,
                expected_modified,
                include_descendants,
            )
        except MutationFailure:
            raise
        except OneNoteError as exc:
            if "mutation_stage" not in exc.details:
                exc.reconciliation = "not_applied"
                exc.details.update(
                    mutation_stage="preflight",
                    mutation_attempted=False,
                    mutation_attempts=0,
                    mutation_replayed=False,
                    observed_outcome="not_applied",
                    preflight_state="rejected",
                    persistence_checkpoint=policy.persistence_checkpoint,
                    retry_safety="new_call_after_read_only_refresh",
                    recommended_action="refresh_typed_ids_and_confirmation_fields",
                    manual_recovery_required=False,
                )
            raise
        except PermissionError as exc:
            raise MutationFailure(
                str(exc),
                code="policy_disabled",
                partial=False,
                reconciliation="not_applied",
                mutation_stage="policy",
                mutation_attempted=False,
                mutation_attempts=0,
                mutation_replayed=False,
                observed_outcome="not_applied",
                preflight_state="not_started",
                persistence_checkpoint=policy.persistence_checkpoint,
                retry_safety="enable_required_policy",
                recommended_action="enable_the_documented_reparent_policy_then_submit_a_new_call",
                manual_recovery_required=False,
            ) from exc
        except PartialFailure as exc:
            attempted = bool(
                exc.details.get(
                    "mutation_attempted",
                    exc.details.get("reparent_attempted", False),
                )
            )
            exc.details.setdefault(
                "mutation_stage", "execute" if attempted else "preflight"
            )
            exc.details.setdefault("mutation_attempted", attempted)
            exc.details.setdefault("mutation_attempts", 1 if attempted else 0)
            exc.details.setdefault("mutation_replayed", False)
            exc.details.setdefault("observed_outcome", "partially_applied")
            exc.details.setdefault("preflight_state", "logical_ready")
            exc.details.setdefault(
                "persistence_checkpoint", policy.persistence_checkpoint
            )
            exc.details.setdefault("retry_safety", "do_not_replay")
            exc.details.setdefault(
                "recommended_action",
                "query_current_ids_and_locations_then_recover_manually",
            )
            exc.details.setdefault("manual_recovery_required", True)
            raise
        except ValueError as exc:
            raise MutationPreflightFailure(
                str(exc),
                mutation_stage="preflight",
                mutation_attempted=False,
                mutation_attempts=0,
                mutation_replayed=False,
                observed_outcome="not_applied",
                preflight_state="rejected",
                persistence_checkpoint=policy.persistence_checkpoint,
                retry_safety="correct_request",
                recommended_action="refresh_typed_ids_and_confirmation_fields",
                manual_recovery_required=False,
                preflight_error_type=type(exc).__name__,
            ) from exc
        except RuntimeError as exc:
            raise MutationFailure(
                str(exc),
                code="mutation_preflight_failed",
                partial=False,
                reconciliation="not_applied",
                mutation_stage="preflight",
                mutation_attempted=False,
                mutation_attempts=0,
                mutation_replayed=False,
                observed_outcome="not_applied",
                preflight_state="rejected",
                persistence_checkpoint=policy.persistence_checkpoint,
                retry_safety="new_call_after_read_only_refresh",
                recommended_action="refresh_typed_ids_and_confirmation_fields",
                manual_recovery_required=False,
                preflight_error_type=type(exc).__name__,
            ) from exc

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
        reconciliation = self._execute_mutation_attempt(
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
        policy = MutationPolicy.current()
        policy.require_write()
        policy.require_local_file_io()
        self.pages.confirm(
            page_id,
            expected_title=expected_title,
            expected_section_id=expected_section_id,
            expected_modified=expected_modified,
        )
        before_hash = self.pages.digest(self.pages.xml(page_id, "all"))
        path = Path(image_path)
        record_backend_call("filesystem:image_source_is_file")
        if not path.is_file():
            raise ValueError(f"Image file not found: {image_path}")
        detected_format = image_file_format(path)
        if image_format:
            requested_format = image_format.strip().casefold()
            requested_format = "jpeg" if requested_format in {"jpg", "jpeg"} else requested_format
            if requested_format != detected_format:
                raise ValueError("image_format does not match the local image file content.")
        fmt = detected_format
        record_backend_call("filesystem:image_dimension_read")
        resolved_width, resolved_height = proportional_dimensions(path, width, height)
        record_backend_call("filesystem:image_source_read")
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
        reconciliation = self._execute_mutation_attempt(
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

    @staticmethod
    def _validate_batch_size(items: list[dict[str, Any]]) -> None:
        if not 1 <= len(items) <= MAX_BATCH_ITEMS:
            raise MutationPreflightFailure(
                f"Batch items must contain between 1 and {MAX_BATCH_ITEMS} entries.",
                mutation_stage="preflight",
                mutation_attempted=False,
                max_items=MAX_BATCH_ITEMS,
            )

    @staticmethod
    def _batch_item_id(item: dict[str, Any]) -> str | None:
        value = (
            item.get("page_id")
            or item.get("section_id")
            or item.get("section_group_id")
            or item.get("object_id")
        )
        return str(value) if value else None

    def _batch_snapshot(self) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        snapshot = self.hierarchy.resources(include_recycle_bin=True)
        return snapshot, {
            str(item["id"]): item for item in snapshot if item.get("id")
        }

    @staticmethod
    def _active_item(
        by_id: dict[str, dict[str, Any]], object_id: str, resource_type: str
    ) -> dict[str, Any]:
        item = by_id.get(object_id)
        if item is None or item.get("resource_type") != resource_type:
            raise MutationPreflightFailure(
                f"Exact ID '{object_id}' does not identify an active {resource_type}.",
                mutation_stage="preflight",
                mutation_attempted=False,
            )
        if item.get("is_in_recycle_bin") is True:
            raise MutationPreflightFailure(
                f"Batch target '{object_id}' is in the recycle bin.",
                mutation_stage="preflight",
                mutation_attempted=False,
            )
        return item

    @staticmethod
    def _confirm_batch_item(
        actual: dict[str, Any], supplied: dict[str, Any], resource_type: str
    ) -> None:
        if resource_type == "page":
            expected_name = supplied["expected_title"]
            expected_parent = supplied["expected_section_id"]
        else:
            expected_name = supplied["expected_name"]
            expected_parent = supplied["expected_parent_id"]
        if display_name(actual) != expected_name:
            raise MutationPreflightFailure(
                f"Confirmation mismatch for exact ID '{actual['id']}': display name changed.",
                mutation_stage="preflight",
                mutation_attempted=False,
            )
        actual_parent = (
            actual.get("section_id") if resource_type == "page" else actual.get("parent_id")
        )
        if actual_parent != expected_parent:
            raise MutationPreflightFailure(
                f"Confirmation mismatch for exact ID '{actual['id']}': parent changed.",
                mutation_stage="preflight",
                mutation_attempted=False,
            )
        expected_modified = supplied.get("expected_modified")
        if expected_modified is not None and actual.get("modified") != expected_modified:
            raise MutationPreflightFailure(
                f"Confirmation mismatch for exact ID '{actual['id']}': modified value changed.",
                mutation_stage="preflight",
                mutation_attempted=False,
            )

    def _preflight_batch_targets(
        self, items: list[dict[str, Any]], resource_type: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, Any]]]:
        self._validate_batch_size(items)
        snapshot, by_id = self._batch_snapshot()
        ids = [self._batch_item_id(item) for item in items]
        if any(value is None for value in ids) or len(ids) != len(set(ids)):
            raise MutationPreflightFailure(
                "Batch target IDs must be present and unique.",
                mutation_stage="preflight",
                mutation_attempted=False,
            )
        targets = []
        for supplied, object_id in zip(items, ids):
            assert object_id is not None
            actual = self._active_item(by_id, object_id, resource_type)
            self._confirm_batch_item(actual, supplied, resource_type)
            targets.append(actual)
        notebook_ids = {self._resource_notebook_id(item) for item in targets}
        if None in notebook_ids or len(notebook_ids) != 1:
            raise MutationPreflightFailure(
                "All batch targets must belong to one active Notebook.",
                mutation_stage="preflight",
                mutation_attempted=False,
            )
        notebook_id = str(next(iter(notebook_ids)))
        budget = CopyBudget.current()
        notebook_items = self._notebook_items(snapshot, notebook_id)
        if len(notebook_items) > budget.max_resources:
            raise MutationPreflightFailure(
                "Batch preflight exceeds the configured hierarchy resource budget.",
                mutation_stage="preflight",
                mutation_attempted=False,
            )
        if sum(item.get("resource_type") == "page" for item in notebook_items) > budget.max_pages:
            raise MutationPreflightFailure(
                "Batch preflight exceeds the configured Page budget.",
                mutation_stage="preflight",
                mutation_attempted=False,
            )
        return snapshot, targets, by_id

    @staticmethod
    def _has_selected_ancestor(
        target: dict[str, Any], selected_ids: set[str], by_id: dict[str, dict[str, Any]]
    ) -> bool:
        parent_id = target.get("parent_page_id") or target.get("parent_id")
        seen: set[str] = set()
        while parent_id and parent_id not in seen:
            if parent_id in selected_ids:
                return True
            seen.add(str(parent_id))
            parent = by_id.get(str(parent_id))
            parent_id = None if parent is None else parent.get("parent_page_id") or parent.get("parent_id")
        return False

    def _execute_batch(
        self,
        operation: str,
        items: list[dict[str, Any]],
        execute,
    ) -> dict[str, Any]:
        outcomes: list[dict[str, Any]] = []
        for index, supplied in enumerate(items):
            reference = self._batch_item_id(supplied)
            try:
                result = execute(supplied)
            except Exception as exc:
                outcomes.append(
                    {
                        "input_index": index,
                        "object_id": reference,
                        "status": "failed",
                        "error_type": type(exc).__name__,
                    }
                )
                outcomes.extend(
                    {
                        "input_index": pending,
                        "object_id": self._batch_item_id(items[pending]),
                        "status": "not_attempted",
                    }
                    for pending in range(index + 1, len(items))
                )
                raise PartialFailure(
                    "Batch execution stopped at the first failed or uncertain item; no rollback was attempted.",
                    partial=True,
                    operation=operation,
                    applied_count=index,
                    failed_index=index,
                    items=outcomes,
                    completed_steps=[
                        {"operation": operation, "status": "applied"}
                        for _ in range(index)
                    ],
                    failed_step=f"{operation}[{index}]",
                    manual_recovery_required=True,
                    retryability="inspect_live_state_before_new_call",
                    rollback_attempted=False,
                    mutation_replayed=False,
                ) from exc
            outcomes.append(
                {
                    "input_index": index,
                    "object_id": reference,
                    "status": "applied",
                    "result": result,
                }
            )
        return {
            "operation": operation,
            "mode": "batch",
            "partial": False,
            "applied_count": len(outcomes),
            "items": outcomes,
            "max_items": MAX_BATCH_ITEMS,
        }

    def _final_reparent_hierarchy(
        self,
        resource_type: str,
        destination_parent_id: str,
        items: list[dict[str, Any]],
        batch_result: dict[str, Any],
    ) -> dict[str, Any]:
        """Re-read every applied target after the complete batch, in input order."""

        try:
            snapshot = self.hierarchy.resources(include_recycle_bin=True)
            by_id = {
                str(value["id"]): value for value in snapshot if value.get("id")
            }
            final_items: list[dict[str, Any]] = []
            current_ids: set[str] = set()
            for index, (supplied, outcome) in enumerate(
                zip(items, batch_result["items"])
            ):
                original_id = self._batch_item_id(supplied)
                result = outcome.get("result", {})
                id_map = result.get("id_map", {})
                current_id = str(id_map.get(original_id, original_id))
                actual = by_id.get(current_id)
                actual_parent = (
                    None
                    if actual is None
                    else actual.get("section_id")
                    if resource_type == "page"
                    else actual.get("parent_id")
                )
                if (
                    original_id is None
                    or actual is None
                    or actual.get("resource_type") != resource_type
                    or actual.get("is_in_recycle_bin") is True
                    or actual_parent != destination_parent_id
                    or current_id in current_ids
                ):
                    raise ValueError(
                        "A final batch target is missing, duplicated, mistyped, recycled, or outside the destination."
                    )
                current_ids.add(current_id)
                projected = {
                    "input_index": index,
                    "original_id": original_id,
                    "current_id": current_id,
                    "resource_type": resource_type,
                    "parent_id": actual_parent,
                }
                if resource_type == "page":
                    projected.update(
                        order=actual.get("order"),
                        page_level=actual.get("page_level"),
                        parent_page_id=actual.get("parent_page_id"),
                    )
                final_items.append(projected)
        except Exception as exc:
            raise PartialFailure(
                "All item calls returned, but the complete batch Reparent final hierarchy could not be verified; inspect live state before any new call.",
                partial=True,
                operation=f"reparent_{resource_type}",
                applied_count=batch_result["applied_count"],
                failed_step="batch_final_hierarchy",
                items=batch_result["items"],
                completed_steps=[
                    {
                        "operation": f"reparent_{resource_type}",
                        "status": "applied",
                    }
                    for _ in batch_result["items"]
                ],
                manual_recovery_required=True,
                retryability="inspect_live_state_before_new_call",
                rollback_attempted=False,
                mutation_replayed=False,
            ) from exc
        return {
            "destination_parent_id": destination_parent_id,
            "item_count": len(final_items),
            "items": final_items,
            "verification_scope": {"page_content": "not_read"},
        }

    def batch_reparent(
        self,
        resource_type: str,
        destination_parent_id: str,
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        MutationPolicy.current().require_organize()
        snapshot, targets, by_id = self._preflight_batch_targets(items, resource_type)
        destination_type = "section" if resource_type == "page" else None
        destination = self._active_item(by_id, destination_parent_id, destination_type or str(by_id.get(destination_parent_id, {}).get("resource_type", "")))
        if resource_type != "page" and destination.get("resource_type") not in {"notebook", "section_group"}:
            raise MutationPreflightFailure(
                "Container batch destination must be an active Notebook or SectionGroup.",
                mutation_stage="preflight",
                mutation_attempted=False,
            )
        notebook_id = self._resource_notebook_id(targets[0])
        if self._resource_notebook_id(destination) != notebook_id:
            raise MutationPreflightFailure(
                "Batch Reparent cannot cross Notebook boundaries.",
                mutation_stage="preflight",
                mutation_attempted=False,
            )
        selected_ids = {str(item["id"]) for item in targets}
        if any(self._has_selected_ancestor(item, selected_ids, by_id) for item in targets):
            raise MutationPreflightFailure(
                "Batch Reparent targets must not contain overlapping ancestor/descendant scopes.",
                mutation_stage="preflight",
                mutation_attempted=False,
            )
        destination_cursor: dict[str, Any] | None = destination
        while destination_cursor is not None:
            if destination_cursor.get("id") in selected_ids:
                raise MutationPreflightFailure(
                    "Batch destination cannot be a selected target or its descendant.",
                    mutation_stage="preflight",
                    mutation_attempted=False,
                )
            parent_id = destination_cursor.get("parent_id")
            destination_cursor = by_id.get(str(parent_id)) if parent_id else None

        if resource_type == "page":
            result = self._execute_batch(
                "reparent_page",
                items,
                lambda item: self.reparent_page(
                    item["page_id"], destination_parent_id, item["expected_title"],
                    item["expected_section_id"], item.get("expected_modified"),
                    item.get("page_scope") == "indentation_subtree",
                ),
            )
        else:
            method = self.reparent_section if resource_type == "section" else self.reparent_section_group
            result = self._execute_batch(
                f"reparent_{resource_type}",
                items,
                lambda item: method(
                    self._batch_item_id(item), destination_parent_id, item["expected_name"],
                    item["expected_parent_id"], item.get("expected_modified"),
                ),
            )
        result["final_hierarchy"] = self._final_reparent_hierarchy(
            resource_type, destination_parent_id, items, result
        )
        return result

    def batch_delete(
        self, resource_type: str, items: list[dict[str, Any]]
    ) -> dict[str, Any]:
        MutationPolicy.current().require_delete(permanently=False)
        _snapshot, targets, by_id = self._preflight_batch_targets(items, resource_type)
        selected_ids = {str(item["id"]) for item in targets}
        if any(self._has_selected_ancestor(item, selected_ids, by_id) for item in targets):
            raise MutationPreflightFailure(
                "Batch Delete targets must not contain overlapping ancestor/descendant scopes.",
                mutation_stage="preflight",
                mutation_attempted=False,
            )
        if resource_type == "page":
            return self._execute_batch(
                "delete_page",
                items,
                lambda item: self.delete_page(
                    item["page_id"], item["expected_title"], item["expected_section_id"],
                    item.get("expected_modified"), False,
                ),
            )
        return self._execute_batch(
            f"delete_{resource_type}",
            items,
            lambda item: self.delete_resource(
                self._batch_item_id(item), resource_type, item["expected_name"],
                item["expected_parent_id"], item.get("expected_modified"), False,
            ),
        )

    def batch_rename(
        self, resource_type: str, items: list[dict[str, Any]]
    ) -> dict[str, Any]:
        MutationPolicy.current().require_write()
        snapshot, targets, _by_id = self._preflight_batch_targets(items, resource_type)
        selected_ids = {str(item["id"]) for item in targets}
        planned: list[tuple[str, str, str]] = []
        for supplied, target in zip(items, targets):
            raw_name = supplied["new_title"] if resource_type == "page" else supplied["new_name"]
            normalized = self.safe_leaf_name(raw_name)
            planned.append((str(target["id"]), str(target.get("parent_id")), normalized))
            if normalized == display_name(target):
                raise MutationPreflightFailure(
                    "Batch Rename rejects no-op name mappings.",
                    mutation_stage="preflight",
                    mutation_attempted=False,
                )
        for parent_id in {entry[1] for entry in planned}:
            siblings = [
                item for item in snapshot
                if item.get("resource_type") == resource_type
                and str(item.get("parent_id")) == parent_id
                and item.get("is_in_recycle_bin") is not True
            ]
            current_by_name = {display_name(item).casefold(): str(item["id"]) for item in siblings}
            parent_plans = [entry for entry in planned if entry[1] == parent_id]
            new_names = [entry[2].casefold() for entry in parent_plans]
            if len(new_names) != len(set(new_names)):
                raise MutationPreflightFailure(
                    "Batch Rename produces duplicate sibling names.",
                    mutation_stage="preflight",
                    mutation_attempted=False,
                )
            for object_id, _parent, new_name in parent_plans:
                occupant = current_by_name.get(new_name.casefold())
                if occupant is not None and occupant != object_id:
                    reason = "name exchange/cycle" if occupant in selected_ids else "existing sibling collision"
                    raise MutationPreflightFailure(
                        f"Batch Rename rejects {reason}.",
                        mutation_stage="preflight",
                        mutation_attempted=False,
                    )
        if resource_type == "page":
            return self._execute_batch(
                "rename_page",
                items,
                lambda item: self.update_page_title(
                    item["page_id"], item["new_title"], item["expected_title"],
                    item["expected_section_id"], item.get("expected_modified"),
                ),
            )
        return self._execute_batch(
            f"rename_{resource_type}",
            items,
            lambda item: self.rename_resource(
                self._batch_item_id(item), resource_type, item["new_name"],
                item["expected_name"], item["expected_parent_id"], item.get("expected_modified"),
            ),
        )

    def batch_create(
        self,
        resource_type: str,
        parent_id: str,
        expected_parent_name: str,
        expected_parent_modified: str | None,
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        policy = MutationPolicy.current()
        policy.require_create()
        if resource_type == "page":
            policy.require_write()
        self._validate_batch_size(items)
        snapshot, by_id = self._batch_snapshot()
        expected_parent_type = "section" if resource_type == "page" else None
        parent = by_id.get(parent_id)
        if parent is None or parent.get("is_in_recycle_bin") is True:
            raise MutationPreflightFailure(
                "Batch Create parent must be active and exact.",
                mutation_stage="preflight",
                mutation_attempted=False,
            )
        if expected_parent_type and parent.get("resource_type") != expected_parent_type:
            raise MutationPreflightFailure(
                "Page batch parent must be a Section.", mutation_stage="preflight", mutation_attempted=False
            )
        if not expected_parent_type and parent.get("resource_type") not in {"notebook", "section_group"}:
            raise MutationPreflightFailure(
                "Container batch parent must be a Notebook or SectionGroup.", mutation_stage="preflight", mutation_attempted=False
            )
        if display_name(parent) != expected_parent_name or (
            expected_parent_modified is not None
            and parent.get("modified") != expected_parent_modified
        ):
            raise MutationPreflightFailure(
                "Batch Create parent confirmation changed.", mutation_stage="preflight", mutation_attempted=False
            )
        budget = CopyBudget.current()
        notebook_id = self._resource_notebook_id(parent)
        notebook_items = self._notebook_items(snapshot, str(notebook_id))
        if len(notebook_items) + len(items) > budget.max_resources:
            raise MutationPreflightFailure(
                "Batch Create exceeds the configured hierarchy resource budget.", mutation_stage="preflight", mutation_attempted=False
            )
        if resource_type == "page" and (
            sum(item.get("resource_type") == "page" for item in notebook_items) + len(items)
            > budget.max_pages
        ):
            raise MutationPreflightFailure(
                "Batch Create exceeds the configured Page budget.", mutation_stage="preflight", mutation_attempted=False
            )
        if resource_type == "page" and sum(len(str(item.get("content", ""))) for item in items) > 500_000:
            raise MutationPreflightFailure(
                "Batch Create Page content exceeds the 500000-character request budget.", mutation_stage="preflight", mutation_attempted=False
            )
        name_key = "title" if resource_type == "page" else "name"
        normalized_names = []
        for item in items:
            name = self.safe_leaf_name(item[name_key])
            if resource_type == "section" and name.casefold().endswith(".one"):
                name = name[:-4]
            normalized_names.append(name.casefold())
            if resource_type == "page" and item.get("content_format", "plain") not in {"plain", "html", "markdown"}:
                raise MutationPreflightFailure(
                    "Page content_format must be plain, html, or markdown.", mutation_stage="preflight", mutation_attempted=False
                )
        if resource_type != "page" and len(normalized_names) != len(set(normalized_names)):
            raise MutationPreflightFailure(
                "Batch Create item names must be unique after normalization.", mutation_stage="preflight", mutation_attempted=False
            )
        existing_names = {
            display_name(item).casefold()
            for item in snapshot
            if item.get("resource_type") == resource_type
            and item.get("parent_id") == parent_id
            and item.get("is_in_recycle_bin") is not True
        }
        if resource_type != "page" and existing_names.intersection(normalized_names):
            raise MutationPreflightFailure(
                "Batch Create name collides with an active direct child.", mutation_stage="preflight", mutation_attempted=False
            )
        if resource_type == "page":
            return self._execute_batch(
                "create_page",
                items,
                lambda item: self.create_page(
                    parent_id, item["title"], item.get("content", ""), item.get("content_format", "plain"), "blank_with_title"
                ),
            )
        method = self.create_section if resource_type == "section" else self.create_section_group
        return self._execute_batch(
            f"create_{resource_type}", items, lambda item: method(parent_id, item["name"])
        )

    @staticmethod
    def _sort_value(item: dict[str, Any], key: str) -> Any:
        if key == "name":
            return display_name(item).casefold()
        raw = item.get(key)
        if not raw:
            raise MutationPreflightFailure(
                f"Sort key '{key}' is missing for exact ID '{item.get('id')}'.",
                mutation_stage="preflight",
                mutation_attempted=False,
            )
        try:
            return datetime.fromisoformat(str(raw)[:-1] + "+00:00" if str(raw)[-1:] in {"Z", "z"} else str(raw))
        except ValueError as exc:
            raise MutationPreflightFailure(
                f"Sort key '{key}' is not a comparable timestamp for exact ID '{item.get('id')}'.",
                mutation_stage="preflight",
                mutation_attempted=False,
            ) from exc

    def _ordered_for_sort(
        self, items: list[dict[str, Any]], key: str, direction: str
    ) -> list[dict[str, Any]]:
        try:
            return sorted(
                items,
                key=lambda item: self._sort_value(item, key),
                reverse=direction == "descending",
            )
        except TypeError as exc:
            raise MutationPreflightFailure(
                f"Sort key '{key}' values are not mutually comparable.",
                mutation_stage="preflight",
                mutation_attempted=False,
            ) from exc

    def _confirm_sort_parent(
        self,
        by_id: dict[str, dict[str, Any]],
        parent_id: str,
        expected_name: str,
        expected_modified: str | None,
        allowed_types: set[str],
    ) -> dict[str, Any]:
        parent = by_id.get(parent_id)
        if parent is None or parent.get("resource_type") not in allowed_types or parent.get("is_in_recycle_bin") is True:
            raise MutationPreflightFailure(
                "Sort parent has an invalid type, is missing, or is in the recycle bin.", mutation_stage="preflight", mutation_attempted=False
            )
        if display_name(parent) != expected_name or (
            expected_modified is not None and parent.get("modified") != expected_modified
        ):
            raise MutationPreflightFailure(
                "Sort parent confirmation changed.", mutation_stage="preflight", mutation_attempted=False
            )
        return parent

    @staticmethod
    def _validate_expected_child_ids(actual: list[dict[str, Any]], expected: list[str]) -> None:
        if not 1 <= len(expected) <= MAX_SORT_CHILDREN or len(expected) != len(set(expected)):
            raise MutationPreflightFailure(
                f"expected_child_ids must contain 1 to {MAX_SORT_CHILDREN} unique IDs.", mutation_stage="preflight", mutation_attempted=False
            )
        if [str(item["id"]) for item in actual] != expected:
            raise MutationPreflightFailure(
                "The complete ordered direct-child confirmation no longer matches live hierarchy.", mutation_stage="preflight", mutation_attempted=False
            )

    def sort_sections(
        self,
        parent_id: str,
        expected_parent_name: str,
        expected_parent_modified: str | None,
        expected_child_ids: list[str],
        key: str,
        direction: str,
    ) -> dict[str, Any]:
        MutationPolicy.current().require_write()
        if key not in {"name", "created", "modified"} or direction not in {"ascending", "descending"}:
            raise MutationPreflightFailure("Invalid Sort key or direction.", mutation_stage="preflight", mutation_attempted=False)
        snapshot, by_id = self._batch_snapshot()
        parent = self._confirm_sort_parent(by_id, parent_id, expected_parent_name, expected_parent_modified, {"notebook", "section_group"})
        active = self.hierarchy.without_recycle_bin(snapshot)
        budget = CopyBudget.current()
        notebook_items = self._notebook_items(active, str(self._resource_notebook_id(parent)))
        if len(notebook_items) > budget.max_resources or sum(item.get("resource_type") == "page" for item in notebook_items) > budget.max_pages:
            raise MutationPreflightFailure(
                "Section Sort verification exceeds the configured hierarchy budget.", mutation_stage="preflight", mutation_attempted=False
            )
        direct = [item for item in active if item.get("parent_id") == parent_id and item.get("resource_type") in {"section", "section_group"}]
        sections = [item for item in direct if item.get("resource_type") == "section"]
        self._validate_expected_child_ids(sections, expected_child_ids)
        ordered_sections = self._ordered_for_sort(sections, key, direction)
        expected_ids = [str(item["id"]) for item in ordered_sections]
        before_ids = [str(item["id"]) for item in sections]
        if expected_ids == before_ids:
            return {"changed": False, "parent": parent, "key": key, "direction": direction, "child_ids": before_ids, "verification_scope": {"page_content": "not_read"}}
        iterator = iter(ordered_sections)
        ordered_direct = [next(iterator) if item.get("resource_type") == "section" else item for item in direct]
        before_direct_set = {str(item["id"]) for item in direct}
        subtree_signature = self._container_subtree_signature(
            [candidate for section in sections for candidate in self._container_subtree(active, str(section["id"]))]
        )
        update_xml = self.hierarchy.container_order_xml(parent, ordered_direct, catalog=active)

        def observe():
            values = self.hierarchy.resources(include_recycle_bin=False)
            refreshed_direct = [item for item in values if item.get("parent_id") == parent_id and item.get("resource_type") in {"section", "section_group"}]
            refreshed_sections = [item for item in refreshed_direct if item.get("resource_type") == "section"]
            refreshed_signature = self._container_subtree_signature(
                [candidate for section in refreshed_sections for candidate in self._container_subtree(values, str(section["id"]))]
            )
            return refreshed_direct, refreshed_sections, refreshed_signature

        before_state = tuple(str(item["id"]) for item in direct)
        expected_state = tuple(str(item["id"]) for item in ordered_direct)
        signature = lambda value: tuple(str(item["id"]) for item in value[0])
        reconciliation = self._execute_mutation_attempt(
            operation="reorder_section",
            execute=lambda: self.call("update_hierarchy", xml=update_xml, schema=XML_SCHEMA_2013),
            observe=observe,
            is_pre_state=lambda value: signature(value) == before_state,
            is_post_state=lambda value: signature(value) == expected_state and value[2] == subtree_signature,
            is_partial_state=lambda value: {
                str(item["id"]) for item in value[0]
            } != before_direct_set,
        )
        stable = self._converge(
            operation="sort_sections", observe=observe,
            accept=lambda value: signature(value) == expected_state and value[2] == subtree_signature,
            project_identity=lambda value: signature(value),
            failure_message="Sort was accepted, but Section order or topology did not converge.",
        )
        return {"changed": True, "parent": parent, "key": key, "direction": direction, "child_ids": expected_ids, "children": stable.value[1], "verification_scope": {"page_content": "not_read"}, "convergence": stable.summary(), "reconciliation": reconciliation.summary()}

    def sort_children(
        self,
        child_type: str | None,
        parent_id: str,
        expected_parent_name: str,
        expected_parent_modified: str | None,
        expected_child_ids: list[str],
        key: str,
        direction: str,
    ) -> dict[str, Any]:
        MutationPolicy.current().require_write()
        parent = self.hierarchy.resource(parent_id)
        parent_type = str(parent.get("resource_type", ""))
        inferred = (
            "section"
            if parent_type in {"notebook", "section_group"}
            else "page"
            if parent_type in {"section", "page"}
            else None
        )
        if inferred is None:
            raise MutationPreflightFailure(
                "Sort parent must be a Notebook, SectionGroup, Section, or Page.",
                mutation_stage="preflight",
                mutation_attempted=False,
            )
        if child_type is not None and child_type != inferred:
            raise MutationPreflightFailure(
                f"child_type '{child_type}' conflicts with parent type '{parent_type}', which requires '{inferred}'.",
                mutation_stage="preflight",
                mutation_attempted=False,
            )
        if inferred == "section":
            return self.sort_sections(
                parent_id, expected_parent_name, expected_parent_modified,
                expected_child_ids, key, direction,
            )
        if inferred == "page":
            return self.sort_pages(
                parent_id, expected_parent_name, expected_parent_modified,
                expected_child_ids, key, direction,
            )
        raise AssertionError("unreachable sort child inference")

    def sort_pages(
        self,
        parent_id: str,
        expected_parent_name: str,
        expected_parent_modified: str | None,
        expected_child_ids: list[str],
        key: str,
        direction: str,
    ) -> dict[str, Any]:
        MutationPolicy.current().require_write()
        if key not in {"name", "created", "modified"} or direction not in {"ascending", "descending"}:
            raise MutationPreflightFailure("Invalid Sort key or direction.", mutation_stage="preflight", mutation_attempted=False)
        snapshot, by_id = self._batch_snapshot()
        parent = self._confirm_sort_parent(by_id, parent_id, expected_parent_name, expected_parent_modified, {"section", "page"})
        section_id = parent_id if parent.get("resource_type") == "section" else str(parent.get("section_id"))
        section = self._active_item(by_id, section_id, "section")
        pages = sorted(
            [item for item in self.hierarchy.without_recycle_bin(snapshot) if item.get("resource_type") == "page" and item.get("section_id") == section_id],
            key=lambda item: int(item.get("order", 0)),
        )
        if len(pages) > CopyBudget.current().max_pages:
            raise MutationPreflightFailure(
                "Page Sort verification exceeds the configured Page budget.", mutation_stage="preflight", mutation_attempted=False
            )
        direct_children = [
            item for item in pages
            if (item.get("parent_page_id") or section_id) == parent_id
        ]
        self._validate_expected_child_ids(direct_children, expected_child_ids)
        ordered_children = self._ordered_for_sort(direct_children, key, direction)
        before_child_ids = [str(item["id"]) for item in direct_children]
        expected_ids = [str(item["id"]) for item in ordered_children]
        if expected_ids == before_child_ids:
            return {"changed": False, "parent": parent, "key": key, "direction": direction, "child_ids": before_child_ids, "pages": pages, "verification_scope": {"page_content": "not_read"}}
        index_by_id = {str(item["id"]): index for index, item in enumerate(pages)}
        starts = [index_by_id[str(item["id"])] for item in direct_children]
        last_start = starts[-1]
        child_level = int(direct_children[-1].get("page_level", 1))
        end = len(pages)
        for index in range(last_start + 1, len(pages)):
            if int(pages[index].get("page_level", 1)) <= child_level:
                end = index
                break
        blocks: dict[str, list[dict[str, Any]]] = {}
        for position, child in enumerate(direct_children):
            start = starts[position]
            stop = starts[position + 1] if position + 1 < len(starts) else end
            blocks[str(child["id"])] = pages[start:stop]
        start = starts[0]
        ordered_pages = [
            *pages[:start],
            *(page for child in ordered_children for page in blocks[str(child["id"])]),
            *pages[end:],
        ]
        expected_signature = tuple((str(item["id"]), order, int(item.get("page_level", 1))) for order, item in enumerate(ordered_pages))
        before_signature = tuple((str(item["id"]), int(item.get("order", 0)), int(item.get("page_level", 1))) for item in pages)

        def observe():
            return sorted(
                [item for item in self.hierarchy.resources(include_recycle_bin=False) if item.get("resource_type") == "page" and item.get("section_id") == section_id],
                key=lambda item: int(item.get("order", 0)),
            )

        signature = lambda values: tuple((str(item["id"]), int(item.get("order", 0)), int(item.get("page_level", 1))) for item in values)
        reconciliation = self._execute_mutation_attempt(
            operation="reorder_page",
            execute=lambda: self.call("update_hierarchy", xml=self.hierarchy.page_order_xml(section, ordered_pages), schema=XML_SCHEMA_2013),
            observe=observe,
            is_pre_state=lambda values: signature(values) == before_signature,
            is_post_state=lambda values: signature(values) == expected_signature,
            is_partial_state=lambda values: {item[0] for item in signature(values)} != {item[0] for item in before_signature},
        )
        stable = self._converge(
            operation="sort_pages", observe=observe,
            accept=lambda values: signature(values) == expected_signature,
            project_identity=signature,
            failure_message="Sort was accepted, but Page block order or indentation did not converge.",
        )
        refreshed_by_id = {str(item["id"]): item for item in stable.value}
        return {"changed": True, "parent": parent, "key": key, "direction": direction, "child_ids": expected_ids, "children": [refreshed_by_id[value] for value in expected_ids], "pages": stable.value, "verification_scope": {"page_content": "not_read"}, "convergence": stable.summary(), "reconciliation": reconciliation.summary()}

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
        before_object_ids = frozenset(
            str(item["object_id"])
            for item in objects
            if item.get("object_id")
        )
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

        deleted_object_ids = {
            str(item["object_id"])
            for item in objects
            if item.get("object_id")
            and (
                item.get("object_id") == object_id
                or item.get("delete_object_id") == object_id
            )
        }
        while True:
            descendants = {
                str(item["object_id"])
                for item in objects
                if item.get("object_id")
                and item.get("parent_object_id") in deleted_object_ids
            }
            expanded = deleted_object_ids | descendants
            if expanded == deleted_object_ids:
                break
            deleted_object_ids = expanded
        expected_object_ids = before_object_ids - deleted_object_ids

        def observe():
            try:
                page = self.hierarchy.resource(page_id, "page")
            except ValueError:
                return {"page": None, "object_ids": frozenset()}
            current_objects = collect_page_objects(self.pages.xml(page_id, "all"))
            return {
                "page": page,
                "object_ids": frozenset(
                    str(item["object_id"])
                    for item in current_objects
                    if item.get("object_id")
                ),
            }

        def identity_matches(value):
            page = value["page"]
            return (
                page is not None
                and display_name(page) == expected_title
                and page.get("section_id") == expected_section_id
            )

        reconciliation = self._execute_mutation_attempt(
            operation="delete_page_content",
            execute=lambda: self.call(
                "delete_page_content", page_id=page_id, object_id=object_id, force=False
            ),
            observe=observe,
            is_pre_state=lambda value: identity_matches(value)
            and value["object_ids"] == before_object_ids,
            is_post_state=lambda value: identity_matches(value)
            and value["object_ids"] == expected_object_ids,
            is_partial_state=lambda value: not identity_matches(value)
            or value["object_ids"] not in {before_object_ids, expected_object_ids},
        )
        stable = self._converge(
            operation="delete_page_content",
            observe=observe,
            accept=lambda value: identity_matches(value)
            and value["object_ids"] == expected_object_ids,
            project_identity=lambda value: (
                None if value["page"] is None else str(value["page"]["id"]),
                tuple(sorted(value["object_ids"])),
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
        reconciliation = self._execute_mutation_attempt(
            operation="delete_hierarchy",
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
        )
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
