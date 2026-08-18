"""Experimental four-layer Copy and Page Move orchestration."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import time
from typing import Any

from ..bridge import OneNoteBridge
from ..constants import SPECIAL_LOCATIONS, XML_SCHEMA_2013
from ..hierarchy import display_name
from ..onenote_errors import transient_read_error
from ..page import (
    SEMANTIC_CONTENT_VERIFICATION,
    collect_page_objects,
    copy_verification_tier,
    page_equivalence,
    semantic_content_comparison,
    title_from_page_xml,
    transform_page_for_copy,
)
from ..policy import CopyBudget, MutationPolicy
from .base import BaseService
from .convergence import (
    DEFAULT_CONVERGENCE,
    DEFAULT_CONVERGENCE_RUNTIME,
    ConvergenceRuntime,
    converge,
)
from .errors import PartialFailure
from .hierarchy import HierarchyService
from .mutations import MutationService
from .operation_runtime import record_backend_call
from .pages import PageService, stable_page_content_digest
from .position import destination_position, unavailable_destination_position


COPY_EXECUTE_TOOLS = {
    "page": "copy_page",
    "section": "copy_section",
    "section_group": "copy_section_group",
    "notebook": "copy_notebook",
}
MOVE_RESOURCE_TYPES = {
    "move_page": "page",
    "move_section": "section",
    "move_section_group": "section_group",
}
MOVE_EXECUTE_TOOLS = {
    "move_page": "move_page",
    "move_section": "move_section",
    "move_section_group": "move_section_group",
}

class CopyService(BaseService):
    @staticmethod
    def _snapshot_destination_position(
        items: list[dict[str, Any]],
        target: dict[str, Any] | None,
        resource_type: str,
        unavailable_reason: str,
    ) -> dict[str, Any]:
        target_id = str((target or {}).get("id", ""))
        if target_id:
            try:
                return destination_position(items, target_id)
            except Exception:
                pass
        return unavailable_destination_position(resource_type, unavailable_reason)

    def _current_destination_position(
        self,
        target: dict[str, Any] | None,
        resource_type: str,
        unavailable_reason: str,
    ) -> dict[str, Any]:
        """Project a trustworthy current target position or explain why it is unavailable."""

        target_id = str((target or {}).get("id", ""))
        if target_id:
            try:
                return destination_position(
                    self.hierarchy.resources(include_recycle_bin=False),
                    target_id,
                )
            except Exception:
                pass
        return unavailable_destination_position(resource_type, unavailable_reason)

    def __init__(
        self,
        bridge: OneNoteBridge,
        hierarchy: HierarchyService,
        pages: PageService,
        mutations: MutationService,
        *,
        convergence_runtime: ConvergenceRuntime = DEFAULT_CONVERGENCE_RUNTIME,
    ) -> None:
        super().__init__(bridge)
        self.hierarchy = hierarchy
        self.pages = pages
        self.mutations = mutations
        self.convergence_runtime = convergence_runtime

    @staticmethod
    def _stable_resource(item: dict[str, Any]) -> dict[str, Any]:
        fields = (
            "resource_type",
            "id",
            "name",
            "title",
            "parent_id",
            "notebook_id",
            "section_id",
            "modified",
            "page_level",
            "order",
            "parent_page_id",
        )
        return {field: item.get(field) for field in fields if field in item}

    @staticmethod
    def _protected_resource(item: dict[str, Any]) -> dict[str, Any]:
        """Keep the authored identity, topology, and order a mutation must preserve.

        OneNote may advance ``modified`` while it finishes persisting an
        otherwise unchanged subtree.  The clock remains in observation
        evidence, but is excluded from mutation authorization; stable Page
        hashes provide the content half of that authorization.
        """

        fields = (
            "resource_type",
            "id",
            "name",
            "title",
            "parent_id",
            "notebook_id",
            "section_id",
            "page_level",
            "order",
            "parent_page_id",
        )
        return {field: item.get(field) for field in fields if field in item}

    @classmethod
    def _protected_destination(cls, destination: dict[str, Any]) -> dict[str, Any]:
        """Project an explicit Copy destination without volatile COM clocks."""

        return {
            "resource_type": destination.get("resource_type"),
            "parent": (
                cls._protected_resource(destination["parent"])
                if isinstance(destination.get("parent"), dict)
                else None
            ),
            "name": destination.get("name"),
            "base_folder": destination.get("base_folder"),
            "target_path": destination.get("target_path"),
            "existing_children": [
                cls._protected_resource(item)
                for item in destination.get("existing_children", [])
            ],
        }

    @staticmethod
    def _digest(value: Any) -> str:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _is_descendant(item: dict[str, Any], root_id: str, by_id: dict[str, dict[str, Any]]) -> bool:
        parent_id = item.get("parent_id")
        while parent_id:
            if parent_id == root_id:
                return True
            parent = by_id.get(parent_id)
            if parent is None:
                return False
            parent_id = parent.get("parent_id")
        return False

    def _source_resources(
        self,
        source_id: str,
        items: list[dict[str, Any]],
        include_descendants: bool = True,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        by_id = {item["id"]: item for item in items}
        source = by_id.get(source_id)
        if source is None:
            raise ValueError(f"No active object found for ID '{source_id}'.")
        if source["resource_type"] == "page":
            if not include_descendants:
                return source, [source]
            section_pages = sorted(
                (
                    item
                    for item in items
                    if item["resource_type"] == "page" and item.get("section_id") == source.get("section_id")
                ),
                key=lambda item: int(item.get("order", 0)),
            )
            start = next(index for index, item in enumerate(section_pages) if item["id"] == source_id)
            root_level = int(source.get("page_level", 1))
            selected = [source]
            for candidate in section_pages[start + 1 :]:
                if int(candidate.get("page_level", 1)) <= root_level:
                    break
                selected.append(candidate)
            return source, selected
        selected = [
            item
            for item in items
            if item["id"] == source_id or self._is_descendant(item, source_id, by_id)
        ]
        selected_ids = {item["id"] for item in selected}
        children_by_parent: dict[str, list[dict[str, Any]]] = {}
        for item in selected:
            parent_id = item.get("parent_id")
            if parent_id in selected_ids:
                children_by_parent.setdefault(parent_id, []).append(item)
        ordered: list[dict[str, Any]] = []
        stack = [source]
        while stack:
            item = stack.pop()
            ordered.append(item)
            stack.extend(reversed(children_by_parent.get(item["id"], [])))
        return source, ordered

    def _capture_source(
        self,
        source_id: str,
        budget: CopyBudget,
        started: float,
        include_descendants: bool = True,
    ) -> dict[str, Any]:
        items = self.hierarchy.resources(include_recycle_bin=False)
        source, resources = self._source_resources(source_id, items, include_descendants)
        if len(resources) > budget.max_resources:
            raise ValueError(
                f"Copy plan contains {len(resources)} resources; limit is {budget.max_resources}."
            )
        pages = [item for item in resources if item["resource_type"] == "page"]
        if len(pages) > budget.max_pages:
            raise ValueError(f"Copy plan contains {len(pages)} pages; limit is {budget.max_pages}.")

        page_xml: dict[str, str] = {}
        page_hashes: dict[str, str] = {}
        page_xml_hashes: dict[str, str] = {}
        total_xml_bytes = 0
        content_objects = 0
        capabilities: set[str] = set()
        preview_issues: list[dict[str, Any]] = []
        placeholder_map = {
            item["id"]: f"copy-target-{index}"
            for index, item in enumerate(resources)
        }
        for page in pages:
            if time.monotonic() - started > budget.max_plan_seconds:
                raise ValueError(f"Copy planning exceeded {budget.max_plan_seconds} seconds.")
            xml = self.pages.xml(page["id"], "all")
            xml_bytes = len(xml.encode("utf-8"))
            if xml_bytes > budget.max_page_xml_bytes:
                raise ValueError(
                    f"Page '{page['id']}' XML is {xml_bytes} bytes; per-page limit is "
                    f"{budget.max_page_xml_bytes}."
                )
            total_xml_bytes += xml_bytes
            if total_xml_bytes > budget.max_total_xml_bytes:
                raise ValueError(
                    f"Copy XML is {total_xml_bytes} bytes; total limit is {budget.max_total_xml_bytes}."
                )
            objects = collect_page_objects(xml)
            content_objects += len(objects)
            if content_objects > budget.max_content_objects:
                raise ValueError(
                    f"Copy plan contains {content_objects} content objects; limit is "
                    f"{budget.max_content_objects}."
                )
            page_xml[page["id"]] = xml
            # Bind plans to stable in-place content rather than the raw COM XML.
            # OneNote can change view/cache metadata between consecutive read-only
            # MediaFile snapshots even when the Page hierarchy clock and authored
            # content are unchanged.  Keep the raw digest as diagnostic evidence,
            # but do not let those OneNote-owned fields make every plan stale.
            page_hashes[page["id"]] = stable_page_content_digest(xml)
            page_xml_hashes[page["id"]] = sha256(xml.encode("utf-8")).hexdigest()
            preview = transform_page_for_copy(xml, placeholder_map[page["id"]], placeholder_map)
            capabilities.update(preview["content_types"])
            preview_issues.extend({"source_page_id": page["id"], **issue} for issue in preview["issues"])

        resource_snapshot = [self._stable_resource(item) for item in resources]
        source_snapshot = {
            "resources": resource_snapshot,
            "page_hashes": page_hashes,
            "page_xml_hashes": page_xml_hashes,
        }
        source_digest_snapshot = {
            "resources": resource_snapshot,
            "page_hashes": page_hashes,
        }
        protected_digest_snapshot = {
            "resources": [self._protected_resource(item) for item in resources],
            "page_hashes": page_hashes,
        }
        return {
            "source": source,
            "resources": resources,
            "page_xml": page_xml,
            "source_snapshot": source_snapshot,
            "source_digest": self._digest(source_digest_snapshot),
            "protected_digest": self._digest(protected_digest_snapshot),
            "estimated": {
                "resources": len(resources),
                "pages": len(pages),
                "content_objects": content_objects,
                "xml_bytes": total_xml_bytes,
            },
            "content_capabilities": sorted(capabilities),
            "preview_issues": preview_issues,
        }

    def _destination(
        self,
        source: dict[str, Any],
        items: list[dict[str, Any]],
        destination_parent_id: str,
        destination_name: str,
        destination_base_folder: str,
    ) -> dict[str, Any]:
        resource_type = source["resource_type"]
        requested_name = destination_name or display_name(source)
        name = (
            self.mutations.page_title(requested_name)
            if resource_type == "page"
            else self.mutations.safe_leaf_name(requested_name)
        )
        if resource_type == "section" and name.casefold().endswith(".one"):
            name = self.mutations.safe_leaf_name(name[:-4])
        if resource_type == "notebook":
            if destination_parent_id:
                raise ValueError("Notebook Copy does not accept destination_parent_id.")
            base = destination_base_folder or self.call(
                "get_special_location", location=SPECIAL_LOCATIONS["default_notebook_folder"]
            )["path"]
            base_path = Path(base).expanduser().resolve(strict=False)
            target_path = base_path / name
            record_backend_call("filesystem:copy_notebook_target_exists")
            if target_path.exists():
                raise ValueError(f"Notebook destination already exists: {target_path}")
            return {
                "resource_type": "notebook_root",
                "parent": None,
                "name": name,
                "base_folder": str(base_path),
                "target_path": str(target_path),
                "existing_children": [],
            }

        if destination_base_folder:
            raise ValueError("destination_base_folder is only valid for Notebook Copy.")

        by_id = {item["id"]: item for item in items}
        parent = by_id.get(destination_parent_id)
        allowed = {
            "page": {"section"},
            "section": {"notebook", "section_group"},
            "section_group": {"notebook", "section_group"},
        }[resource_type]
        if parent is None or parent["resource_type"] not in allowed:
            allowed_names = ", ".join(sorted(allowed))
            raise ValueError(
                f"destination_parent_id for {resource_type} must identify one of: {allowed_names}."
            )
        if resource_type == "section_group" and (
            parent["id"] == source["id"] or self._is_descendant(parent, source["id"], by_id)
        ):
            raise ValueError("A SectionGroup cannot be copied into itself or one of its descendants.")
        if resource_type == "page":
            children = [
                item
                for item in items
                if item["resource_type"] == "page"
                and item.get("section_id") == destination_parent_id
                and item.get("parent_page_id") is None
            ]
        else:
            children = [item for item in items if item.get("parent_id") == destination_parent_id]
        if any(display_name(item).casefold() == name.casefold() for item in children):
            raise ValueError(
                f"Destination already has a direct {resource_type} child named '{name}'. "
                "Copy never overwrites, merges, or automatically renames objects."
            )
        return {
            "resource_type": parent["resource_type"],
            "parent": self._stable_resource(parent),
            "name": name,
            "base_folder": "",
            "target_path": "",
            "existing_children": [self._stable_resource(item) for item in children],
        }

    def _build_plan(
        self,
        source_id: str,
        destination_parent_id: str = "",
        destination_name: str = "",
        destination_base_folder: str = "",
        *,
        operation: str = "copy",
        include_descendants: bool = False,
    ) -> dict[str, Any]:
        if operation not in {"copy", *MOVE_RESOURCE_TYPES}:
            raise ValueError(f"Unsupported Copy/Move operation '{operation}'.")
        started = time.monotonic()
        budget = CopyBudget.current()
        bundle = self._capture_source(source_id, budget, started, include_descendants)
        effective_include_descendants = (
            bool(include_descendants)
            if bundle["source"]["resource_type"] == "page"
            else True
        )
        move_source_bundle = None
        if operation == "move_page":
            # A root-only Move still needs to bind every excluded descendant before
            # the source root is deleted.  Those Pages are promoted one level first,
            # so OneNote cannot implicitly recycle them with their former parent.
            move_source_bundle = (
                bundle
                if effective_include_descendants
                else self._capture_source(source_id, budget, started, True)
            )
        items = self.hierarchy.resources(include_recycle_bin=False)
        move_notebooks = None
        if operation in {"move_section", "move_section_group"}:
            expected_type = MOVE_RESOURCE_TYPES[operation]
            if bundle["source"]["resource_type"] != expected_type:
                raise ValueError(
                    f"{operation} source must identify a {expected_type}."
                )
            destination_parent = next(
                (item for item in items if item.get("id") == destination_parent_id),
                None,
            )
            if destination_parent is not None and destination_parent.get(
                "resource_type"
            ) in {"notebook", "section_group"}:
                source_notebook_id = str(bundle["source"].get("notebook_id", ""))
                destination_notebook_id = str(
                    destination_parent.get("id")
                    if destination_parent.get("resource_type") == "notebook"
                    else destination_parent.get("notebook_id", "")
                )
                if source_notebook_id and source_notebook_id == destination_notebook_id:
                    suggested = (
                        "reparent_section"
                        if expected_type == "section"
                        else "reparent_section_group"
                    )
                    raise ValueError(
                        f"{operation} only supports cross-Notebook reconstruction. "
                        f"Use {suggested} for same-Notebook parent changes."
                    )
        destination = self._destination(
            bundle["source"],
            items,
            destination_parent_id,
            destination_name,
            destination_base_folder,
        )
        if operation in {"move_section", "move_section_group"}:
            source_notebook_id = str(bundle["source"].get("notebook_id", ""))
            destination_parent = destination.get("parent") or {}
            destination_notebook_id = str(
                destination_parent.get("id")
                if destination_parent.get("resource_type") == "notebook"
                else destination_parent.get("notebook_id", "")
            )
            if not source_notebook_id or not destination_notebook_id:
                raise ValueError("Move requires exact source and destination Notebook IDs.")
            move_notebooks = {
                "source_notebook_id": source_notebook_id,
                "destination_notebook_id": destination_notebook_id,
                "cross_notebook": True,
            }
        if time.monotonic() - started > budget.max_plan_seconds:
            raise ValueError(f"Copy planning exceeded {budget.max_plan_seconds} seconds.")
        page_title_override_requested = (
            bundle["source"]["resource_type"] == "page" and bool(destination_name)
        )
        digest_payload = {
            "schema_version": 5,
            "operation": operation,
            "options": {
                "include_descendants": effective_include_descendants,
                "page_title_override_requested": page_title_override_requested,
            },
            "source_snapshot": {
                "resources": [
                    self._protected_resource(item) for item in bundle["resources"]
                ],
                "page_hashes": bundle["source_snapshot"]["page_hashes"],
            },
            "destination": self._protected_destination(destination),
            "copyability": {
                "content_capabilities": bundle["content_capabilities"],
                "issues": bundle["preview_issues"],
            },
        }
        if move_source_bundle is not None:
            digest_payload["move_source_snapshot"] = {
                "resources": [
                    self._protected_resource(item)
                    for item in move_source_bundle["resources"]
                ],
                "page_hashes": move_source_bundle["source_snapshot"]["page_hashes"],
            }
        if move_notebooks is not None:
            digest_payload["move_notebooks"] = move_notebooks
        plan_digest = self._digest(digest_payload)
        pages = bundle["estimated"]["pages"]
        steps = [
            {"operation": "create_resources", "count": bundle["estimated"]["resources"]},
            {"operation": "write_page_content", "count": pages},
            {"operation": "reorder_pages", "count": pages},
            {"operation": "verify_copy", "count": bundle["estimated"]["resources"]},
        ]
        if operation == "move_page":
            preserved_count = max(
                0,
                len(move_source_bundle["resources"]) - len(bundle["resources"]),
            )
            if preserved_count:
                steps.append(
                    {
                        "operation": "promote_preserved_descendants",
                        "count": preserved_count,
                    }
                )
            steps.append({"operation": "recycle_source_pages", "count": pages})
        elif operation in {"move_section", "move_section_group"}:
            steps.extend(
                [
                    {"operation": "revalidate_source", "count": bundle["estimated"]["resources"]},
                    {"operation": "delete_source_root_nonpermanently", "count": 1},
                    {"operation": "verify_source_subtree_inactive", "count": bundle["estimated"]["resources"]},
                    {"operation": "revalidate_destination", "count": bundle["estimated"]["resources"]},
                ]
            )
        return {
            **bundle,
            "operation": operation,
            "include_descendants": effective_include_descendants,
            "destination": destination,
            "move_source_bundle": move_source_bundle,
            "move_notebooks": move_notebooks,
            "page_title_override_requested": page_title_override_requested,
            "plan_digest": plan_digest,
            "steps": steps,
            "lossless_candidate": not bundle["preview_issues"],
            "execute_tool": (
                MOVE_EXECUTE_TOOLS[operation]
                if operation in MOVE_EXECUTE_TOOLS
                else COPY_EXECUTE_TOOLS[bundle["source"]["resource_type"]]
            ),
        }

    @staticmethod
    def _inspection_plan(plan: dict[str, Any]) -> dict[str, Any]:
        """Project an internal plan for tests and diagnostics, never MCP exposure."""
        warnings = sorted({issue["reason"] for issue in plan["preview_issues"]})
        if plan["operation"] in MOVE_RESOURCE_TYPES:
            warnings.append(
                "Move creates new IDs; inbound links from outside the copied subtree are not scanned."
            )
        return {
            "operation": plan["operation"],
            "include_descendants": plan["include_descendants"],
            "plan_digest": plan["plan_digest"],
            "source_snapshot_digest": plan["protected_digest"],
            "source": CopyService._stable_resource(plan["source"]),
            "destination": plan["destination"],
            **(
                {"move_notebooks": plan["move_notebooks"]}
                if plan.get("move_notebooks") is not None
                else {}
            ),
            "snapshots": {
                "source": plan["source_snapshot"],
                "destination": plan["destination"],
                **(
                    {
                        "move_source": plan["move_source_bundle"]["source_snapshot"],
                    }
                    if plan.get("move_source_bundle") is not None
                    else {}
                ),
            },
            "estimated": plan["estimated"],
            "content_capabilities": plan["content_capabilities"],
            "copyability": {
                "lossless_candidate": plan["lossless_candidate"],
                "issues": plan["preview_issues"],
            },
            "steps": plan["steps"],
            "execute_tool": plan["execute_tool"],
            "warnings": warnings,
        }

    def _inspect_copy_plan(
        self,
        source_id: str,
        destination_parent_id: str = "",
        destination_name: str = "",
        destination_base_folder: str = "",
        include_descendants: bool = False,
    ) -> dict[str, Any]:
        return self._inspection_plan(
            self._build_plan(
                source_id,
                destination_parent_id,
                destination_name,
                destination_base_folder,
                include_descendants=include_descendants,
            )
        )

    def _inspect_move_page_plan(
        self,
        page_id: str,
        destination_section_id: str,
        destination_title: str = "",
        include_descendants: bool = False,
    ) -> dict[str, Any]:
        plan = self._build_plan(
            page_id,
            destination_section_id,
            destination_title,
            operation="move_page",
            include_descendants=include_descendants,
        )
        if plan["source"]["resource_type"] != "page":
            raise ValueError("page_id must identify a Page.")
        return self._inspection_plan(plan)

    def _plan_move_container(
        self,
        source_id: str,
        resource_type: str,
        destination_parent_id: str,
        destination_name: str = "",
    ) -> dict[str, Any]:
        operation = f"move_{resource_type}"
        plan = self._build_plan(
            source_id,
            destination_parent_id,
            destination_name,
            operation=operation,
            include_descendants=True,
        )
        if plan["source"]["resource_type"] != resource_type:
            raise ValueError(f"source ID must identify a {resource_type}.")
        return self._inspection_plan(plan)

    def _inspect_move_section_plan(
        self,
        section_id: str,
        destination_parent_id: str,
        destination_name: str = "",
    ) -> dict[str, Any]:
        return self._plan_move_container(
            section_id, "section", destination_parent_id, destination_name
        )

    def _inspect_move_section_group_plan(
        self,
        section_group_id: str,
        destination_parent_id: str,
        destination_name: str = "",
    ) -> dict[str, Any]:
        return self._plan_move_container(
            section_group_id,
            "section_group",
            destination_parent_id,
            destination_name,
        )

    def _confirm_source(
        self,
        source_id: str,
        resource_type: str,
        expected_name: str,
        expected_parent_id: str | None,
        expected_modified: str | None,
    ) -> None:
        if resource_type == "page":
            self.pages.confirm(
                source_id,
                expected_title=expected_name,
                expected_section_id=expected_parent_id or "",
                expected_modified=expected_modified,
            )
            return
        self.mutations.confirm_resource(
            source_id,
            resource_type,
            expected_name=expected_name,
            expected_parent_id=expected_parent_id,
            expected_modified=expected_modified,
        )

    @staticmethod
    def _created_item(result: dict[str, Any]) -> dict[str, Any]:
        # create_page returns both the new Page and its parent Section.  The
        # created resource must win over contextual parent objects.
        for key in ("item", "page", "section", "section_group"):
            value = result.get(key)
            if isinstance(value, dict):
                return value
        raise RuntimeError("Create operation returned no typed item.")

    @staticmethod
    def _validate_created_target(
        target: dict[str, Any],
        *,
        resource_type: str,
        expected_parent_id: str | None,
        expected_name: str,
        source_ids: set[str],
        resolved_target_ids: set[str],
    ) -> str:
        """Validate one created target before any Copy content/topology mutation."""

        target_id = target.get("id")
        if (
            not isinstance(target_id, str)
            or not target_id
            or target.get("resource_type") != resource_type
            or target.get("is_in_recycle_bin") is True
            or display_name(target) != expected_name
        ):
            raise RuntimeError(
                "Create operation returned an untyped, recycled, or mismatched target resource."
            )
        if target_id in source_ids:
            raise RuntimeError("Create operation resolved to an existing Copy source ID.")
        if target_id in resolved_target_ids:
            raise RuntimeError(
                "Create operation resolved two Copy resources to the same target ID."
            )
        if resource_type == "page" and target.get("section_id") != expected_parent_id:
            raise RuntimeError(
                "Created Page read-back differs from the planned destination Section."
            )
        if resource_type in {"section", "section_group"} and target.get("parent_id") != expected_parent_id:
            raise RuntimeError(
                "Created hierarchy item read-back differs from its planned parent."
            )
        if resource_type == "notebook" and target.get("parent_id") is not None:
            raise RuntimeError("Created Notebook read-back unexpectedly has a hierarchy parent.")
        return target_id

    def _execute_copy(self, plan: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        budget = CopyBudget.current()
        resources = plan["resources"]
        source = plan["source"]
        destination = plan["destination"]
        source_ids = {item["id"] for item in resources}
        id_map: dict[str, str] = {}
        allocated_ids: list[str] = []
        resolved_target_ids: list[str] = []
        created: list[dict[str, Any]] = []
        created_items: dict[str, dict[str, Any]] = {}
        completed_steps: list[dict[str, Any]] = []
        page_targets: dict[str, dict[str, Any]] = {}
        notebook_destination_path = ""
        failed_step = "create_resources"

        def check_deadline() -> None:
            if time.monotonic() - started > budget.max_execute_seconds:
                raise RuntimeError(f"Copy execution exceeded {budget.max_execute_seconds} seconds.")

        try:
            for item in resources:
                check_deadline()
                kind = item["resource_type"]
                is_root = item["id"] == source["id"]
                target_name = destination["name"] if is_root else display_name(item)
                if kind == "notebook":
                    result = self.mutations.create_notebook(target_name, destination["base_folder"])
                elif kind == "section_group":
                    parent_id = destination["parent"]["id"] if is_root else id_map[item["parent_id"]]
                    result = self.mutations.create_section_group(parent_id, target_name)
                elif kind == "section":
                    parent_id = destination["parent"]["id"] if is_root else id_map[item["parent_id"]]
                    result = self.mutations.create_section(parent_id, target_name)
                else:
                    if source["resource_type"] == "page":
                        section_id = destination["parent"]["id"]
                    else:
                        section_id = id_map[item["section_id"]]
                    result = self.mutations.create_page(
                        section_id,
                        target_name,
                        forbidden_ids=source_ids | set(resolved_target_ids),
                    )
                allocated_id = str(
                    result.get("allocated_id")
                    or result.get("page_id")
                    or result.get("section_id")
                    or result.get("section_group_id")
                    or result.get("notebook_id")
                    or ""
                )
                if allocated_id:
                    allocated_ids.append(allocated_id)
                target = self._created_item(result)
                if not allocated_id and target.get("id"):
                    allocated_id = str(target["id"])
                    allocated_ids.append(allocated_id)
                expected_parent_id = (
                    section_id
                    if kind == "page"
                    else parent_id
                    if kind in {"section", "section_group"}
                    else None
                )
                target_id = self._validate_created_target(
                    target,
                    resource_type=kind,
                    expected_parent_id=expected_parent_id,
                    expected_name=target_name,
                    source_ids=source_ids,
                    resolved_target_ids=set(resolved_target_ids),
                )
                resolved_target_ids.append(target_id)
                id_map[item["id"]] = target_id
                created_items[item["id"]] = target
                created.append(
                    {
                        "source_id": item["id"],
                        "target_id": target["id"],
                        "resource_type": kind,
                    }
                )
                if kind == "page":
                    page_targets[item["id"]] = target
                completed_steps.append(
                    {"operation": "create", "source_id": item["id"], "target_id": target["id"]}
                )
                if kind == "notebook":
                    reported_path = str(result.get("path", ""))
                    notebook_destination_path = reported_path
                    expected_path = str(Path(destination["target_path"]).resolve(strict=False))
                    actual_path = str(Path(reported_path).resolve(strict=False)) if reported_path else ""
                    if not actual_path or actual_path.casefold() != expected_path.casefold():
                        raise RuntimeError(
                            "Notebook creation returned a destination path different from the Copy plan."
                        )

            failed_step = "write_page_content"
            page_results: list[dict[str, Any]] = []
            issues: list[dict[str, Any]] = []
            for item in (resource for resource in resources if resource["resource_type"] == "page"):
                check_deadline()
                target = page_targets[item["id"]]
                target_title = destination["name"] if item["id"] == source["id"] else item["title"]
                title_override_requested = (
                    item["id"] == source["id"]
                    and plan["page_title_override_requested"] is True
                )
                source_xml = plan["page_xml"][item["id"]]
                transformed = transform_page_for_copy(
                    source_xml,
                    target["id"],
                    id_map,
                    title=(target_title if target_title != item["title"] else None),
                )
                page_issues = [
                    {"source_page_id": item["id"], "target_page_id": target["id"], **issue}
                    for issue in transformed["issues"]
                ]
                issues.extend(page_issues)
                verification_tier = copy_verification_tier(
                    transformed["content_types"],
                    page_xml=transformed["xml"],
                )
                source_page_title = title_from_page_xml(source_xml)
                transformed_page_title = title_from_page_xml(transformed["xml"])
                source_title_checks = {
                    "title": (
                        source_page_title is not None
                        and transformed_page_title is not None
                        and source_page_title == item["title"]
                        and transformed_page_title == target_title
                        and (
                            title_override_requested
                            or source_page_title == transformed_page_title
                        )
                    ),
                    "source_matches_metadata": source_page_title == item["title"],
                    "transformed_matches_expected": (
                        transformed_page_title == target_title
                    ),
                    "default_title_preserved": (
                        title_override_requested
                        or source_page_title == transformed_page_title
                    ),
                }
                title_readback_stages = {
                    "schema_version": 1,
                    "title_override_requested": title_override_requested,
                    "source_to_transformed": {
                        "checks": source_title_checks,
                        "passed": all(source_title_checks.values()),
                        "content_exposed": False,
                    },
                    "content_exposed": False,
                }
                semantic_content_stages = None
                if verification_tier == SEMANTIC_CONTENT_VERIFICATION:
                    semantic_content_stages = {
                        "schema_version": 1,
                        "title_override_requested": title_override_requested,
                        "source_to_transformed": semantic_content_comparison(
                            source_xml,
                            transformed["xml"],
                        ),
                        "content_exposed": False,
                    }
                before_target_digest = self.pages.digest(
                    self.pages.xml(target["id"], "all")
                )

                def observe_page():
                    actual_xml = self.pages.xml(target["id"], "all")
                    actual_page_title = title_from_page_xml(actual_xml)
                    target_title_checks = {
                        "title": (
                            actual_page_title is not None
                            and transformed_page_title is not None
                            and transformed_page_title == target_title
                            and actual_page_title == transformed_page_title
                        ),
                        "target_title_available": actual_page_title is not None,
                        "transformed_matches_expected": (
                            transformed_page_title == target_title
                        ),
                        "target_matches_transformed": (
                            actual_page_title == transformed_page_title
                        ),
                    }
                    return {
                        "digest": self.pages.digest(actual_xml),
                        "equivalence": page_equivalence(
                            transformed["xml"],
                            actual_xml,
                            verification_tier=verification_tier,
                        ),
                        "title_readback": {
                            "checks": target_title_checks,
                            "passed": all(target_title_checks.values()),
                            "content_exposed": False,
                        },
                    }

                reconciliation = self.mutations._reconciled_idempotent_execute(
                    operation="copy_page_content",
                    execute=lambda: self.call(
                        "update_page_content",
                        xml=transformed["xml"],
                        schema=XML_SCHEMA_2013,
                        force=False,
                    ),
                    observe=observe_page,
                    is_pre_state=lambda value: value["digest"] == before_target_digest,
                    is_post_state=lambda value: value["equivalence"]["equivalent"],
                )
                stable_page = converge(
                    observe_page,
                    lambda value: value["equivalence"]["equivalent"],
                    lambda value: value["digest"],
                    config=DEFAULT_CONVERGENCE,
                    clock=self.convergence_runtime.clock,
                    sleeper=self.convergence_runtime.sleeper,
                    transient=transient_read_error,
                )
                assert stable_page.value is not None
                equivalence = stable_page.value["equivalence"]
                title_readback_stages["transformed_to_target"] = stable_page.value[
                    "title_readback"
                ]
                if semantic_content_stages is not None:
                    semantic_content_stages["transformed_to_target"] = equivalence.get(
                        "semantic_content_comparison"
                    )
                page_results.append(
                    {
                        "source_page_id": item["id"],
                        "target_page_id": target["id"],
                        "lossless": (
                            transformed["lossless_candidate"]
                            and equivalence["equivalent"]
                            and title_readback_stages["source_to_transformed"]["passed"]
                            and title_readback_stages["transformed_to_target"]["passed"]
                        ),
                        "content_types": transformed["content_types"],
                        "normalizations": transformed["normalizations"],
                        "equivalence": equivalence,
                        "title_readback_stages": title_readback_stages,
                        **(
                            {"semantic_content_stages": semantic_content_stages}
                            if semantic_content_stages is not None
                            else {}
                        ),
                        "convergence": stable_page.summary(),
                        "reconciliation": reconciliation.summary(),
                    }
                )
                completed_steps.append(
                    {"operation": "write_page_content", "source_id": item["id"], "target_id": target["id"]}
                )

            failed_step = "reorder_pages"
            source_root_level = int(source.get("page_level", 1))
            target_sections: dict[str, list[dict[str, Any]]] = {}
            expected_page_positions: dict[str, dict[str, Any]] = {}
            for item in (resource for resource in resources if resource["resource_type"] == "page"):
                target_section_id = (
                    destination["parent"]["id"]
                    if source["resource_type"] == "page"
                    else id_map[item["section_id"]]
                )
                level = int(item.get("page_level", 1))
                if source["resource_type"] == "page":
                    level = level - source_root_level + 1
                target_sections.setdefault(target_section_id, []).append(
                    {
                        **page_targets[item["id"]],
                        "page_level": level,
                        "source_order": int(item.get("order", 0)),
                    }
                )
            for section_id, new_pages in target_sections.items():
                check_deadline()
                section = self.hierarchy.resource(section_id, "section")
                target_ids = {page["id"] for page in new_pages}
                existing = [
                    item
                    for item in self.hierarchy.resources(include_recycle_bin=False)
                    if item["resource_type"] == "page"
                    and item.get("section_id") == section_id
                    and item["id"] not in target_ids
                ]
                existing.sort(key=lambda item: int(item.get("order", 0)))
                new_pages.sort(key=lambda item: item["source_order"])
                ordered = [*existing, *new_pages]
                for order, page in enumerate(ordered):
                    if page["id"] in target_ids:
                        expected_page_positions[page["id"]] = {
                            "section_id": section_id,
                            "order": order,
                            "page_level": int(page["page_level"]),
                        }
                self.call(
                    "update_hierarchy",
                    xml=self.hierarchy.page_order_xml(section, ordered),
                    schema=XML_SCHEMA_2013,
                )
                completed_steps.append(
                    {"operation": "reorder_pages", "section_id": section_id, "page_ids": sorted(target_ids)}
                )

            failed_step = "verify_copy"
            check_deadline()
            def observe_topology():
                refreshed = self.hierarchy.resources(include_recycle_bin=False)
                refreshed_by_id = {item["id"]: item for item in refreshed}
                topology_verified = True
                for item in resources:
                    target = refreshed_by_id.get(id_map[item["id"]])
                    if target is None or target["resource_type"] != item["resource_type"]:
                        topology_verified = False
                        break
                    expected_name = (
                        destination["name"]
                        if item["id"] == source["id"]
                        else display_name(item)
                    )
                    if display_name(target) != expected_name:
                        topology_verified = False
                        break
                    if item["resource_type"] == "page":
                        expected_section = (
                            destination["parent"]["id"]
                            if source["resource_type"] == "page"
                            else id_map[item["section_id"]]
                        )
                        if target.get("section_id") != expected_section:
                            topology_verified = False
                            break
                        expected_position = expected_page_positions.get(target["id"])
                        if expected_position is None or any(
                            target.get(field) != expected_position[field]
                            for field in ("section_id", "order", "page_level")
                        ):
                            topology_verified = False
                            break
                    elif item["resource_type"] in {"section", "section_group"}:
                        expected_parent = (
                            destination["parent"]["id"]
                            if item["id"] == source["id"]
                            else id_map[item["parent_id"]]
                        )
                        if target.get("parent_id") != expected_parent:
                            topology_verified = False
                            break
                identity = tuple(
                    (
                        target_id,
                        refreshed_by_id.get(target_id, {}).get("parent_id"),
                        refreshed_by_id.get(target_id, {}).get("section_id"),
                        refreshed_by_id.get(target_id, {}).get("order"),
                        refreshed_by_id.get(target_id, {}).get("page_level"),
                    )
                    for target_id in resolved_target_ids
                )
                return {
                    "items": refreshed,
                    "by_id": refreshed_by_id,
                    "verified": topology_verified,
                    "identity": identity,
                }

            topology_convergence = self.mutations._converge(
                operation="copy_topology",
                observe=observe_topology,
                accept=lambda value: value["verified"],
                project_identity=lambda value: value["identity"],
                failure_message="Copy targets were created, but topology did not converge.",
                identity_remap={
                    allocated: resolved
                    for allocated, resolved in zip(allocated_ids, resolved_target_ids)
                    if allocated != resolved
                },
            )
            assert topology_convergence.value is not None
            refreshed = topology_convergence.value["items"]
            refreshed_by_id = topology_convergence.value["by_id"]
            topology_verified = topology_convergence.value["verified"]
            pages_verified = all(result["equivalence"]["equivalent"] for result in page_results)
            lossless = topology_verified and all(result["lossless"] for result in page_results)
            blocking_copy_issues = [
                issue
                for issue in issues
                if issue.get("action") == "omitted"
                or issue.get("code") == "content_type_unverified"
            ]
            copy_contract_satisfied = (
                topology_verified
                and pages_verified
                and not blocking_copy_issues
            )
            fidelity = "lossless" if lossless else "unverified"
            target_root = refreshed_by_id.get(id_map[source["id"]], created_items[source["id"]])
            warnings = sorted({issue["reason"] for issue in issues})
            copy_report = {
                "planning": {
                    "internal": True,
                    "operation": plan["operation"],
                    "include_descendants": plan["include_descendants"],
                    "estimated": dict(plan["estimated"]),
                    "content_capabilities": list(plan["content_capabilities"]),
                    "lossless_candidate": bool(plan["lossless_candidate"]),
                },
                "id_map": id_map,
                "allocated_ids": list(allocated_ids),
                "resolved_target_ids": list(resolved_target_ids),
                "copied_counts": {
                    "resources": len(created),
                    "pages": len(page_results),
                },
                "skipped_content": [issue for issue in issues if issue.get("action") == "omitted"],
                "issues": issues,
                "lossless": lossless,
                "verified": topology_verified and pages_verified,
                "fidelity": fidelity,
                "copy_contract_satisfied": copy_contract_satisfied,
                "page_results": page_results,
                "convergence": topology_convergence.summary(),
            }
            if source["resource_type"] == "notebook":
                copy_report["destination_path"] = notebook_destination_path
            if not copy_report["verified"]:
                raise PartialFailure(
                    "Copy created the target, but content or topology read-back verification failed.",
                    partial=True,
                    outcome="copy_unverified",
                    source_untouched=True,
                    source_touched=False,
                    topology_touched=True,
                    manual_recovery_required=True,
                    source_deleted=False,
                    destination=target_root,
                    destination_position=self._snapshot_destination_position(
                        refreshed,
                        target_root,
                        str(source["resource_type"]),
                        "destination_target_not_uniquely_observed",
                    ),
                    copy_report=copy_report,
                    created_ids=[item["target_id"] for item in created],
                    allocated_ids=list(allocated_ids),
                    resolved_target_ids=list(resolved_target_ids),
                    completed_steps=completed_steps,
                    failed_step=failed_step,
                )
            return {
                "item": target_root,
                "destination_position": destination_position(
                    refreshed,
                    str(target_root["id"]),
                ),
                "copy_report": copy_report,
                "created_ids": [item["target_id"] for item in created],
                "allocated_ids": list(allocated_ids),
                "resolved_target_ids": list(resolved_target_ids),
                "partial": False,
                "warnings": warnings,
                **(
                    {"destination_path": notebook_destination_path}
                    if notebook_destination_path
                    else {}
                ),
            }
        except PartialFailure as exc:
            details = dict(exc.details)
            if "copy_report" in details:
                raise
            nested_allocated = [str(value) for value in details.get("allocated_ids", [])]
            nested_resolved = [str(value) for value in details.get("resolved_target_ids", [])]
            details["allocated_ids"] = list(dict.fromkeys([*allocated_ids, *nested_allocated]))
            details["resolved_target_ids"] = list(
                dict.fromkeys([*resolved_target_ids, *nested_resolved])
            )
            combined_ids = [item["target_id"] for item in created]
            combined_ids.extend(str(value) for value in details.get("created_ids", []))
            details["created_ids"] = list(dict.fromkeys(combined_ids))
            if details["created_ids"]:
                details.setdefault("outcome", "copy_unverified")
                details.setdefault("source_deleted", False)
            nested_steps = details.get("completed_steps", [])
            details["completed_steps"] = [*completed_steps, *nested_steps]
            details.setdefault("id_map", dict(id_map))
            details.setdefault("failed_step", failed_step)
            details["source_touched"] = bool(details.get("source_touched", False))
            details["source_untouched"] = not details["source_touched"]
            details.setdefault("topology_touched", bool(details["allocated_ids"]))
            details.setdefault("manual_recovery_required", bool(details["allocated_ids"]))
            details.setdefault(
                "possibly_untracked_allocated_ids",
                [
                    value
                    for value in details["allocated_ids"]
                    if value not in details["resolved_target_ids"]
                ],
            )
            details.setdefault("partial", True)
            details.setdefault(
                "destination_position",
                self._current_destination_position(
                    details.get("destination"),
                    str(source["resource_type"]),
                    "destination_target_not_uniquely_observed",
                ),
            )
            raise PartialFailure(str(exc), **details) from exc
        except Exception as exc:
            if created or allocated_ids:
                raise PartialFailure(
                    str(exc),
                    partial=True,
                    outcome="copy_unverified",
                    source_untouched=True,
                    source_touched=False,
                    topology_touched=bool(created),
                    manual_recovery_required=any(
                        value not in source_ids for value in allocated_ids
                    ),
                    source_deleted=False,
                    created_ids=[item["target_id"] for item in created],
                    allocated_ids=list(allocated_ids),
                    resolved_target_ids=list(resolved_target_ids),
                    possibly_untracked_allocated_ids=[
                        value for value in allocated_ids if value not in resolved_target_ids
                    ],
                    id_map=id_map,
                    destination_position=self._current_destination_position(
                        created_items.get(source["id"]),
                        str(source["resource_type"]),
                        "destination_target_not_uniquely_observed",
                    ),
                    completed_steps=completed_steps,
                    failed_step=failed_step,
                ) from exc
            raise

    def copy_resource(
        self,
        source_id: str,
        resource_type: str,
        destination_parent_id: str,
        destination_name: str,
        destination_base_folder: str,
        expected_name: str,
        expected_parent_id: str | None,
        expected_modified: str | None,
        include_descendants: bool = False,
    ) -> dict[str, Any]:
        MutationPolicy.current().require_copy()
        self._confirm_source(
            source_id,
            resource_type,
            expected_name,
            expected_parent_id,
            None,
        )
        plan = self._build_plan(
            source_id,
            destination_parent_id,
            destination_name,
            destination_base_folder,
            include_descendants=include_descendants,
        )
        if plan["source"]["resource_type"] != resource_type:
            raise ValueError(f"source_id must identify a {resource_type}.")
        copied = self._execute_copy(plan)
        if expected_modified is not None and plan["source"].get("modified") != expected_modified:
            copied["warnings"] = [
                *copied.get("warnings", []),
                "OneNote advanced source modified timestamps after planning; "
                "typed topology and stable Page content remained unchanged.",
            ]
        return copied

    def _promote_preserved_move_descendants(
        self,
        plan: dict[str, Any],
    ) -> dict[str, Any]:
        """Detach descendants excluded by a root-only Move before deleting its root."""

        move_bundle = plan.get("move_source_bundle")
        if not isinstance(move_bundle, dict):
            return {"promoted": False, "preserved_descendant_ids": [], "pages": {}}
        selected_ids = {str(item["id"]) for item in plan["resources"]}
        preserved = [
            item
            for item in move_bundle["resources"]
            if str(item["id"]) not in selected_ids
        ]
        if not preserved:
            return {"promoted": False, "preserved_descendant_ids": [], "pages": {}}

        source = plan["source"]
        section_id = str(source["section_id"])
        catalog = self.hierarchy.resources(include_recycle_bin=False)
        section = next(
            (
                item
                for item in catalog
                if item.get("id") == section_id and item.get("resource_type") == "section"
            ),
            None,
        )
        if section is None:
            raise RuntimeError(
                "Move source Section is no longer active before descendant promotion."
            )
        pages = sorted(
            (
                dict(item)
                for item in catalog
                if item.get("resource_type") == "page"
                and str(item.get("section_id")) == section_id
            ),
            key=lambda item: int(item.get("order", 0)),
        )
        page_ids = [str(item["id"]) for item in pages]
        preserved_ids = [str(item["id"]) for item in preserved]
        if str(source["id"]) not in page_ids or any(
            value not in page_ids for value in preserved_ids
        ):
            raise RuntimeError(
                "A root-only Move descendant is no longer active before promotion."
            )

        expected_levels = {
            str(item["id"]): int(item.get("page_level", 1)) - 1
            for item in preserved
        }
        if any(level < 1 for level in expected_levels.values()):
            raise RuntimeError("A preserved Move descendant cannot be promoted above page level 1.")
        adjusted = [
            {
                **item,
                "page_level": expected_levels.get(
                    str(item["id"]), int(item.get("page_level", 1))
                ),
            }
            for item in pages
        ]
        self.call(
            "update_hierarchy",
            xml=self.hierarchy.page_order_xml(section, adjusted),
            schema=XML_SCHEMA_2013,
        )

        refreshed = sorted(
            (
                dict(item)
                for item in self.hierarchy.resources(include_recycle_bin=False)
                if item.get("resource_type") == "page"
                and str(item.get("section_id")) == section_id
            ),
            key=lambda item: int(item.get("order", 0)),
        )
        if [str(item["id"]) for item in refreshed] != page_ids:
            raise RuntimeError("Descendant promotion changed the source Section Page identity/order.")
        expected_parent: dict[str, str | None] = {}
        stack: list[dict[str, Any]] = []
        for page in adjusted:
            level = int(page["page_level"])
            while stack and int(stack[-1]["page_level"]) >= level:
                stack.pop()
            expected_parent[str(page["id"])] = (
                str(stack[-1]["id"]) if stack else None
            )
            stack.append(page)
        refreshed_by_id = {str(item["id"]): item for item in refreshed}
        page_evidence: dict[str, dict[str, Any]] = {}
        # Promotion intentionally changes Page hierarchy metadata such as
        # pageLevel and OneNote-owned modification clocks.  Keep the raw XML
        # hashes in the plan for pre-mutation stale-plan detection, but compare
        # stable in-place Page content across this intentional topology change.
        planned_content_hashes = {
            page_id: stable_page_content_digest(move_bundle["page_xml"][page_id])
            for page_id in preserved_ids
        }
        for page_id in preserved_ids:
            current = refreshed_by_id[page_id]
            if (
                int(current.get("page_level", 0)) != expected_levels[page_id]
                or current.get("parent_page_id") != expected_parent[page_id]
            ):
                raise RuntimeError("A preserved Move descendant has unexpected promoted topology.")
            current_hash = stable_page_content_digest(
                self.pages.xml(page_id, "all")
            )
            if current_hash != planned_content_hashes.get(page_id):
                raise RuntimeError("A preserved Move descendant changed content during promotion.")
            page_evidence[page_id] = {
                "section_id": section_id,
                "page_level": expected_levels[page_id],
                "parent_page_id": expected_parent[page_id],
                "page_hash": current_hash,
            }
        return {
            "promoted": True,
            "preserved_descendant_ids": preserved_ids,
            "pages": page_evidence,
        }

    def _verify_preserved_move_descendants(
        self,
        evidence: dict[str, Any],
    ) -> None:
        if not evidence.get("promoted"):
            return
        catalog = {
            str(item["id"]): item
            for item in self.hierarchy.resources(include_recycle_bin=False)
            if item.get("resource_type") == "page"
        }
        for page_id in evidence["preserved_descendant_ids"]:
            expected = evidence["pages"][page_id]
            current = catalog.get(page_id)
            if current is None or any(
                current.get(field) != expected[field]
                for field in ("section_id", "page_level", "parent_page_id")
            ):
                raise RuntimeError("A preserved root-only Move descendant is missing or misplaced.")
            current_hash = stable_page_content_digest(
                self.pages.xml(page_id, "all")
            )
            if current_hash != expected["page_hash"]:
                raise RuntimeError(
                    "A preserved root-only Move descendant changed after source deletion."
                )

    def move_page(
        self,
        page_id: str,
        destination_section_id: str,
        expected_title: str,
        expected_section_id: str,
        expected_modified: str | None = None,
        destination_title: str = "",
        include_descendants: bool = False,
    ) -> dict[str, Any]:
        MutationPolicy.current().require_move()
        self._confirm_source(
            page_id,
            "page",
            expected_title,
            expected_section_id,
            None,
        )
        plan = self._build_plan(
            page_id,
            destination_section_id,
            destination_title,
            operation="move_page",
            include_descendants=include_descendants,
        )
        source_clock_drifted = (
            expected_modified is not None
            and plan["source"].get("modified") != expected_modified
        )
        execute_started = time.monotonic()
        execute_budget = CopyBudget.current()

        def check_move_deadline() -> None:
            if time.monotonic() - execute_started > execute_budget.max_execute_seconds:
                raise RuntimeError(
                    f"Move execution exceeded {execute_budget.max_execute_seconds} seconds."
                )

        try:
            copied = self._execute_copy(plan)
        except PartialFailure as exc:
            details = dict(exc.details)
            if details.get("outcome") == "copy_unverified":
                details["outcome"] = "copy_only"
                details["source_deleted"] = False
                raise PartialFailure(
                    "The selected Page target was created, but Copy read-back verification failed; "
                    "source deletion was blocked.",
                    **details,
                ) from exc
            details.setdefault("source_deleted", False)
            raise PartialFailure(str(exc), **details) from exc
        partial_position = lambda reason: self._current_destination_position(
            copied.get("item"),
            "page",
            reason,
        )
        report = copied["copy_report"]
        if report.get("copy_contract_satisfied") is not True:
            raise PartialFailure(
                "The selected Page scope was copied, but source deletion was blocked because "
                "the shared Copy contract was not satisfied.",
                partial=True,
                outcome="copy_only",
                source_deleted=False,
                destination=copied.get("item"),
                destination_position=partial_position(
                    "destination_target_not_uniquely_observed"
                ),
                copy_report=report,
                created_ids=copied["created_ids"],
                warnings=copied.get("warnings", []),
            )

        try:
            check_move_deadline()
            current = self._capture_source(
                page_id,
                CopyBudget.current(),
                time.monotonic(),
                True,
            )
        except Exception as exc:
            raise PartialFailure(
                "The selected Page scope was copied, but the source snapshot could not be revalidated; "
                "source deletion was blocked.",
                partial=True,
                outcome="copy_only",
                source_deleted=False,
                destination=copied.get("item"),
                destination_position=partial_position(
                    "destination_target_not_uniquely_observed"
                ),
                copy_report=report,
                created_ids=copied["created_ids"],
                source_revalidation_error=str(exc),
            ) from exc
        expected_move_source = plan.get("move_source_bundle") or plan
        if current["protected_digest"] != expected_move_source["protected_digest"]:
            raise PartialFailure(
                "The source Page scope or its preserved descendants changed after Copy verification; "
                "source deletion was blocked.",
                partial=True,
                outcome="copy_only",
                source_deleted=False,
                destination=copied.get("item"),
                destination_position=partial_position(
                    "destination_target_not_uniquely_observed"
                ),
                copy_report=report,
                created_ids=copied["created_ids"],
            )
        source_clock_drifted = (
            source_clock_drifted
            or current["source_digest"] != expected_move_source["source_digest"]
        )

        try:
            preservation = self._promote_preserved_move_descendants(plan)
        except Exception as exc:
            raise PartialFailure(
                "The Page target was copied, but excluded descendants could not be safely detached; "
                "source deletion was blocked.",
                partial=True,
                outcome="copy_only",
                source_deleted=False,
                source_topology_may_have_changed=True,
                destination=copied.get("item"),
                destination_position=partial_position(
                    "destination_target_not_uniquely_observed"
                ),
                copy_report=report,
                created_ids=copied["created_ids"],
                preservation_error=str(exc),
            ) from exc

        attempted: list[str] = []
        removed: list[str] = []
        recycled: list[str] = []
        recycle_unverified: list[str] = []
        current_by_id = {str(item["id"]): item for item in current["resources"]}
        source_pages = [
            current_by_id.get(str(item["id"]), item)
            for item in plan["resources"]
            if item["resource_type"] == "page"
        ]
        if preservation.get("promoted"):
            current_by_id = {
                str(item["id"]): item
                for item in self.hierarchy.resources(include_recycle_bin=False)
            }
            source_pages = [
                current_by_id.get(str(item["id"]), item) for item in source_pages
            ]
        try:
            for page in reversed(source_pages):
                check_move_deadline()
                attempted.append(page["id"])
                deletion = self.mutations.delete_page(
                    page["id"],
                    page["title"],
                    page["section_id"],
                    page.get("modified"),
                    False,
                )
                removed.append(page["id"])
                final_state = deletion.get("final_state")
                if isinstance(final_state, dict) and final_state.get("is_in_recycle_bin") is True:
                    recycled.append(page["id"])
                else:
                    recycle_unverified.append(page["id"])
        except Exception as exc:
            remaining = [
                page["id"]
                for page in source_pages
                if page["id"] not in removed
            ]
            outcome = "source_partially_removed" if removed else "source_delete_failed"
            raise PartialFailure(
                str(exc),
                partial=True,
                outcome=outcome,
                source_deleted=False,
                attempted_source_ids=attempted,
                recycled_source_ids=recycled,
                recycle_unverified_source_ids=recycle_unverified,
                deleted_source_ids=removed,
                remaining_source_ids=remaining,
                destination=copied.get("item"),
                destination_position=partial_position(
                    "destination_target_not_uniquely_observed"
                ),
                copy_report=report,
                created_ids=copied["created_ids"],
                preserved_descendants=preservation,
            ) from exc
        try:
            self._verify_preserved_move_descendants(preservation)
        except Exception as exc:
            raise PartialFailure(
                "The selected source Page was removed, but an excluded descendant could not be "
                "verified in the active hierarchy.",
                partial=True,
                outcome="source_removed_preserved_descendants_unverified",
                source_deleted=True,
                attempted_source_ids=attempted,
                recycled_source_ids=recycled,
                recycle_unverified_source_ids=recycle_unverified,
                deleted_source_ids=removed,
                destination=copied.get("item"),
                destination_position=partial_position(
                    "destination_target_not_uniquely_observed"
                ),
                copy_report=report,
                created_ids=copied["created_ids"],
                preserved_descendants=preservation,
                preservation_error=str(exc),
            ) from exc
        copied.update(
            {
                "outcome": "moved",
                "source_deleted": True,
                "source_deleted_nonpermanently": True,
                "source_deleted_to_recycle_bin": (
                    True if len(recycled) == len(source_pages) else None
                ),
                "recycle_bin_verification": (
                    "verified"
                    if len(recycled) == len(source_pages)
                    else "not_required_com_unavailable"
                ),
                "attempted_source_ids": attempted,
                "recycled_source_ids": recycled,
                "recycle_unverified_source_ids": recycle_unverified,
                "deleted_source_ids": removed,
                "include_descendants": bool(include_descendants),
                "preserved_descendants": preservation,
                "warnings": [
                    *copied.get("warnings", []),
                    *(
                        [
                            "OneNote advanced source modified timestamps after planning; "
                            "typed topology and stable Page content remained unchanged."
                        ]
                        if source_clock_drifted
                        else []
                    ),
                    "Move created new Page IDs; inbound links outside the copied subtree were not scanned.",
                    *(
                        [
                            "OneNote removed one or more source Pages from the active hierarchy after "
                            "non-permanent DeleteHierarchy, but COM did not expose their recycle-bin metadata."
                        ]
                        if recycle_unverified
                        else []
                    ),
                ],
            }
        )
        final_items = self.hierarchy.resources(include_recycle_bin=False)
        copied["destination_position"] = destination_position(
            final_items,
            str(copied["item"]["id"]),
        )
        return copied

    def _move_container(
        self,
        source_id: str,
        resource_type: str,
        destination_parent_id: str,
        expected_name: str,
        expected_parent_id: str,
        expected_modified: str | None = None,
        destination_name: str = "",
    ) -> dict[str, Any]:
        """Execute the fixed Copy→verify→one root Delete container Move pipeline."""

        MutationPolicy.current().require_move()
        self._confirm_source(
            source_id,
            resource_type,
            expected_name,
            expected_parent_id,
            None,
        )
        operation = f"move_{resource_type}"
        plan = self._build_plan(
            source_id,
            destination_parent_id,
            destination_name,
            operation=operation,
            include_descendants=True,
        )
        source_clock_drifted = (
            expected_modified is not None
            and plan["source"].get("modified") != expected_modified
        )

        execute_started = time.monotonic()
        execute_budget = CopyBudget.current()

        def check_move_deadline() -> None:
            if time.monotonic() - execute_started > execute_budget.max_execute_seconds:
                raise RuntimeError(
                    f"Move execution exceeded {execute_budget.max_execute_seconds} seconds."
                )

        try:
            copied = self._execute_copy(plan)
        except PartialFailure as exc:
            details = dict(exc.details)
            if details.get("outcome") == "copy_unverified":
                details["outcome"] = "copy_only"
            details.setdefault("source_deleted", False)
            raise PartialFailure(
                "The container target was created, but Copy verification did not authorize source deletion.",
                **details,
            ) from exc

        partial_position = lambda reason: self._current_destination_position(
            copied.get("item"),
            resource_type,
            reason,
        )

        report = copied["copy_report"]
        planned_source_ids = [str(item["id"]) for item in plan["resources"]]
        id_map = report.get("id_map")
        mapped_target_ids = list(id_map.values()) if isinstance(id_map, dict) else []
        copy_gate_passed = (
            report.get("copy_contract_satisfied") is True
            and isinstance(id_map, dict)
            and list(id_map) == planned_source_ids
            and len(mapped_target_ids) == len(set(mapped_target_ids))
        )
        if not copy_gate_passed:
            raise PartialFailure(
                "The container subtree was copied, but the complete verified Copy gate did not pass; "
                "source deletion was blocked.",
                partial=True,
                outcome="copy_only",
                source_deleted=False,
                destination=copied.get("item"),
                destination_position=partial_position(
                    "destination_target_not_uniquely_observed"
                ),
                copy_report=report,
                created_ids=copied["created_ids"],
            )
        try:
            check_move_deadline()
            current_source = self._capture_source(
                source_id,
                CopyBudget.current(),
                time.monotonic(),
                True,
            )
            if current_source["protected_digest"] != plan["protected_digest"]:
                raise RuntimeError("The source container subtree changed after Copy verification.")
            source_clock_drifted = (
                source_clock_drifted
                or current_source["source_digest"] != plan["source_digest"]
            )
            target_root_id = str(id_map[source_id])
            target_before_delete = self._capture_source(
                target_root_id,
                CopyBudget.current(),
                time.monotonic(),
                True,
            )
        except Exception as exc:
            raise PartialFailure(
                "The container subtree was copied, but source/destination revalidation failed; "
                "source deletion was blocked.",
                partial=True,
                outcome="copy_only",
                source_deleted=False,
                destination=copied.get("item"),
                destination_position=partial_position(
                    "destination_target_not_uniquely_observed"
                ),
                copy_report=report,
                created_ids=copied["created_ids"],
                source_revalidation_error=str(exc),
            ) from exc

        deletion: dict[str, Any] | None = None
        deletion_error: Exception | None = None
        try:
            check_move_deadline()
            deletion = self.mutations.delete_resource(
                source_id,
                resource_type,
                expected_name,
                expected_parent_id,
                current_source.get("source", {}).get("modified", expected_modified),
                False,
            )
        except Exception as exc:
            deletion_error = exc

        def observe_remaining():
            check_move_deadline()
            active_items = self.hierarchy.resources(include_recycle_bin=False)
            active_ids = {str(item["id"]) for item in active_items}
            return [
                value for value in planned_source_ids if value in active_ids
            ]

        source_convergence = converge(
            observe_remaining,
            # Stabilize any exact source-ID state so that full, partial, and
            # absent outcomes can be classified without conflating a stable
            # partial mutation with an unreadable/indeterminate state.
            lambda values: True,
            lambda values: tuple(values),
            config=DEFAULT_CONVERGENCE,
            clock=self.convergence_runtime.clock,
            sleeper=self.convergence_runtime.sleeper,
            transient=transient_read_error,
        )
        remaining_source_ids = (
            list(source_convergence.value)
            if source_convergence.value is not None
            else list(planned_source_ids)
        )
        inactive_source_ids = [
            value for value in planned_source_ids if value not in remaining_source_ids
        ]
        if deletion_error is not None or remaining_source_ids or not source_convergence.converged:
            root_inactive = source_id not in remaining_source_ids
            outcome = (
                "source_partially_removed"
                if inactive_source_ids
                else "source_delete_failed"
            )
            if not source_convergence.converged:
                outcome = "source_delete_state_indeterminate"
            raise PartialFailure(
                str(
                    deletion_error
                    or (
                        "Source deletion state did not reach two stable live observations."
                        if not source_convergence.converged
                        else "Root deletion returned but part of the source subtree remains active."
                    )
                ),
                partial=True,
                outcome=outcome,
                source_deleted=(
                    source_convergence.converged
                    and root_inactive
                    and not remaining_source_ids
                ),
                source_deleted_nonpermanently=(
                    source_convergence.converged and root_inactive
                ),
                attempted_source_ids=[source_id],
                deleted_source_ids=(
                    [source_id]
                    if source_convergence.converged and root_inactive
                    else []
                ),
                inactive_source_ids=inactive_source_ids,
                remaining_source_ids=remaining_source_ids,
                destination=copied.get("item"),
                destination_position=partial_position(
                    "destination_target_not_uniquely_observed"
                ),
                convergence=source_convergence.summary(),
                copy_report=report,
                created_ids=copied["created_ids"],
            ) from deletion_error

        try:
            check_move_deadline()
            target_after_delete = self._capture_source(
                target_root_id,
                CopyBudget.current(),
                time.monotonic(),
                True,
            )
            if target_after_delete["protected_digest"] != target_before_delete["protected_digest"]:
                raise RuntimeError(
                    "The verified destination subtree's protected topology or content changed "
                    "during source deletion."
                )
            destination_clock_drifted = (
                target_after_delete["source_digest"] != target_before_delete["source_digest"]
            )
        except Exception as exc:
            raise PartialFailure(
                "The source container subtree is inactive, but the destination could not be revalidated.",
                partial=True,
                outcome="source_removed_destination_revalidation_failed",
                source_deleted=True,
                source_deleted_nonpermanently=True,
                attempted_source_ids=[source_id],
                deleted_source_ids=[source_id],
                inactive_source_ids=inactive_source_ids,
                remaining_source_ids=[],
                destination=copied.get("item"),
                destination_position=partial_position(
                    "destination_target_not_uniquely_observed"
                ),
                copy_report=report,
                created_ids=copied["created_ids"],
                destination_revalidation_error=str(exc),
            ) from exc

        final_state = deletion.get("final_state") if deletion else None
        recycle_verified = (
            isinstance(final_state, dict)
            and final_state.get("is_in_recycle_bin") is True
        )
        copied.update(
            {
                "outcome": "moved",
                "source_deleted": True,
                "source_deleted_nonpermanently": True,
                "source_deleted_to_recycle_bin": True if recycle_verified else None,
                "recycle_bin_verification": (
                    "verified" if recycle_verified else "not_required_com_unavailable"
                ),
                "attempted_source_ids": [source_id],
                "deleted_source_ids": [source_id],
                "inactive_source_ids": inactive_source_ids,
                "remaining_source_ids": [],
                "move_notebooks": plan["move_notebooks"],
                "warnings": [
                    *copied.get("warnings", []),
                    *(
                        [
                            "OneNote advanced source modified timestamps after planning; "
                            "typed topology and stable Page content remained unchanged."
                        ]
                        if source_clock_drifted
                        else []
                    ),
                    *(
                        [
                            "OneNote advanced destination modified timestamps while persisting the "
                            "copied subtree; protected topology and Page content remained stable."
                        ]
                        if destination_clock_drifted
                        else []
                    ),
                    "Move created new IDs; inbound links outside the copied subtree were not scanned.",
                    *(
                        [
                            "OneNote removed the source subtree from the active hierarchy after "
                            "non-permanent DeleteHierarchy, but COM did not expose recycle-bin metadata."
                        ]
                        if not recycle_verified
                        else []
                    ),
                ],
            }
        )
        final_items = self.hierarchy.resources(include_recycle_bin=False)
        copied["destination_position"] = destination_position(
            final_items,
            str(copied["item"]["id"]),
        )
        return copied

    def move_section(
        self,
        section_id: str,
        destination_parent_id: str,
        expected_name: str,
        expected_parent_id: str,
        expected_modified: str | None = None,
        destination_name: str = "",
    ) -> dict[str, Any]:
        return self._move_container(
            section_id,
            "section",
            destination_parent_id,
            expected_name,
            expected_parent_id,
            expected_modified,
            destination_name,
        )

    def move_section_group(
        self,
        section_group_id: str,
        destination_parent_id: str,
        expected_name: str,
        expected_parent_id: str,
        expected_modified: str | None = None,
        destination_name: str = "",
    ) -> dict[str, Any]:
        return self._move_container(
            section_group_id,
            "section_group",
            destination_parent_id,
            expected_name,
            expected_parent_id,
            expected_modified,
            destination_name,
        )
