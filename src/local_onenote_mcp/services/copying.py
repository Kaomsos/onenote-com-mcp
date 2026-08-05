"""Experimental four-layer Copy and reconstructive Page move orchestration."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import time
from typing import Any

from ..bridge import OneNoteBridge
from ..constants import SPECIAL_LOCATIONS, XML_SCHEMA_2013
from ..hierarchy import display_name
from ..page import collect_page_objects, page_equivalence, transform_page_for_copy
from ..policy import CopyBudget, MutationPolicy
from .base import BaseService
from .errors import PartialFailure
from .hierarchy import HierarchyService
from .mutations import MutationService
from .pages import PageService


COPY_EXECUTE_TOOLS = {
    "page": "copy_page",
    "section": "copy_section",
    "section_group": "copy_section_group",
    "notebook": "copy_notebook",
}


class CopyService(BaseService):
    def __init__(
        self,
        bridge: OneNoteBridge,
        hierarchy: HierarchyService,
        pages: PageService,
        mutations: MutationService,
    ) -> None:
        super().__init__(bridge)
        self.hierarchy = hierarchy
        self.pages = pages
        self.mutations = mutations

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
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        by_id = {item["id"]: item for item in items}
        source = by_id.get(source_id)
        if source is None:
            raise ValueError(f"No active object found for ID '{source_id}'.")
        if source["resource_type"] == "page":
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

    def _capture_source(self, source_id: str, budget: CopyBudget, started: float) -> dict[str, Any]:
        items = self.hierarchy.resources(include_recycle_bin=False)
        source, resources = self._source_resources(source_id, items)
        if len(resources) > budget.max_resources:
            raise ValueError(
                f"Copy plan contains {len(resources)} resources; limit is {budget.max_resources}."
            )
        pages = [item for item in resources if item["resource_type"] == "page"]
        if len(pages) > budget.max_pages:
            raise ValueError(f"Copy plan contains {len(pages)} pages; limit is {budget.max_pages}.")

        page_xml: dict[str, str] = {}
        page_hashes: dict[str, str] = {}
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
            page_hashes[page["id"]] = sha256(xml.encode("utf-8")).hexdigest()
            preview = transform_page_for_copy(xml, placeholder_map[page["id"]], placeholder_map)
            capabilities.update(preview["content_types"])
            preview_issues.extend({"source_page_id": page["id"], **issue} for issue in preview["issues"])

        resource_snapshot = [self._stable_resource(item) for item in resources]
        source_snapshot = {
            "resources": resource_snapshot,
            "page_hashes": page_hashes,
        }
        return {
            "source": source,
            "resources": resources,
            "page_xml": page_xml,
            "source_snapshot": source_snapshot,
            "source_digest": self._digest(source_snapshot),
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
        name = self.mutations.safe_leaf_name(destination_name or display_name(source))
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
    ) -> dict[str, Any]:
        started = time.monotonic()
        budget = CopyBudget.current()
        bundle = self._capture_source(source_id, budget, started)
        items = self.hierarchy.resources(include_recycle_bin=False)
        destination = self._destination(
            bundle["source"],
            items,
            destination_parent_id,
            destination_name,
            destination_base_folder,
        )
        if time.monotonic() - started > budget.max_plan_seconds:
            raise ValueError(f"Copy planning exceeded {budget.max_plan_seconds} seconds.")
        digest_payload = {
            "schema_version": 1,
            "operation": operation,
            "source_snapshot": bundle["source_snapshot"],
            "destination": destination,
            "copyability": {
                "content_capabilities": bundle["content_capabilities"],
                "issues": bundle["preview_issues"],
            },
        }
        plan_digest = self._digest(digest_payload)
        pages = bundle["estimated"]["pages"]
        steps = [
            {"operation": "create_resources", "count": bundle["estimated"]["resources"]},
            {"operation": "write_page_content", "count": pages},
            {"operation": "reorder_pages", "count": pages},
            {"operation": "verify_copy", "count": bundle["estimated"]["resources"]},
        ]
        if operation == "reconstructive_move_page":
            steps.append({"operation": "recycle_source_pages", "count": pages})
        return {
            **bundle,
            "operation": operation,
            "destination": destination,
            "plan_digest": plan_digest,
            "steps": steps,
            "lossless_candidate": not bundle["preview_issues"],
            "execute_tool": (
                "reconstructive_move_page"
                if operation == "reconstructive_move_page"
                else COPY_EXECUTE_TOOLS[bundle["source"]["resource_type"]]
            ),
        }

    @staticmethod
    def _public_plan(plan: dict[str, Any]) -> dict[str, Any]:
        warnings = sorted({issue["reason"] for issue in plan["preview_issues"]})
        if plan["operation"] == "reconstructive_move_page":
            warnings.append(
                "Reconstructive move creates new Page IDs; inbound links from outside the copied subtree are not scanned."
            )
        return {
            "operation": plan["operation"],
            "plan_digest": plan["plan_digest"],
            "source_snapshot_digest": plan["source_digest"],
            "source": CopyService._stable_resource(plan["source"]),
            "destination": plan["destination"],
            "snapshots": {
                "source": plan["source_snapshot"],
                "destination": plan["destination"],
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

    def plan_copy(
        self,
        source_id: str,
        destination_parent_id: str = "",
        destination_name: str = "",
        destination_base_folder: str = "",
    ) -> dict[str, Any]:
        return self._public_plan(
            self._build_plan(
                source_id,
                destination_parent_id,
                destination_name,
                destination_base_folder,
            )
        )

    def plan_reconstructive_move_page(
        self,
        page_id: str,
        destination_section_id: str,
        destination_title: str = "",
    ) -> dict[str, Any]:
        plan = self._build_plan(
            page_id,
            destination_section_id,
            destination_title,
            operation="reconstructive_move_page",
        )
        if plan["source"]["resource_type"] != "page":
            raise ValueError("page_id must identify a Page.")
        return self._public_plan(plan)

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
        for key in ("item", "section_group", "section", "page"):
            value = result.get(key)
            if isinstance(value, dict):
                return value
        raise RuntimeError("Create operation returned no typed item.")

    def _execute_copy(self, plan: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        budget = CopyBudget.current()
        resources = plan["resources"]
        source = plan["source"]
        destination = plan["destination"]
        id_map: dict[str, str] = {}
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
                    result = self.mutations.create_page(section_id, target_name)
                target = self._created_item(result)
                id_map[item["id"]] = target["id"]
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
                transformed = transform_page_for_copy(
                    plan["page_xml"][item["id"]],
                    target["id"],
                    id_map,
                    title=(target_title if target_title != item["title"] else None),
                )
                page_issues = [
                    {"source_page_id": item["id"], "target_page_id": target["id"], **issue}
                    for issue in transformed["issues"]
                ]
                issues.extend(page_issues)
                self.call(
                    "update_page_content",
                    xml=transformed["xml"],
                    schema=XML_SCHEMA_2013,
                    force=False,
                )
                actual_xml = self.pages.xml(target["id"], "all")
                equivalence = page_equivalence(transformed["xml"], actual_xml)
                page_results.append(
                    {
                        "source_page_id": item["id"],
                        "target_page_id": target["id"],
                        "lossless": transformed["lossless_candidate"] and equivalence["equivalent"],
                        "equivalence": equivalence,
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
            refreshed = self.hierarchy.resources(include_recycle_bin=False)
            refreshed_by_id = {item["id"]: item for item in refreshed}
            topology_verified = True
            for item in resources:
                target = refreshed_by_id.get(id_map[item["id"]])
                if target is None or target["resource_type"] != item["resource_type"]:
                    topology_verified = False
                    break
                expected_name = destination["name"] if item["id"] == source["id"] else display_name(item)
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
            pages_verified = all(result["equivalence"]["equivalent"] for result in page_results)
            lossless = topology_verified and all(result["lossless"] for result in page_results)
            target_root = refreshed_by_id.get(id_map[source["id"]], created_items[source["id"]])
            warnings = sorted({issue["reason"] for issue in issues})
            copy_report = {
                "id_map": id_map,
                "copied_counts": {
                    "resources": len(created),
                    "pages": len(page_results),
                },
                "skipped_content": [issue for issue in issues if issue.get("action") == "omitted"],
                "issues": issues,
                "lossless": lossless,
                "verified": topology_verified and pages_verified,
                "page_results": page_results,
            }
            if source["resource_type"] == "notebook":
                copy_report["destination_path"] = notebook_destination_path
            if not copy_report["verified"]:
                raise PartialFailure(
                    "Copy created the target, but content or topology read-back verification failed.",
                    partial=True,
                    outcome="copy_unverified",
                    source_untouched=True,
                    source_deleted=False,
                    destination=target_root,
                    copy_report=copy_report,
                    created_ids=[item["target_id"] for item in created],
                    completed_steps=completed_steps,
                    failed_step=failed_step,
                )
            return {
                "item": target_root,
                "copy_report": copy_report,
                "created_ids": [item["target_id"] for item in created],
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
            combined_ids = [item["target_id"] for item in created]
            combined_ids.extend(str(value) for value in details.get("created_ids", []))
            details["created_ids"] = list(dict.fromkeys(combined_ids))
            nested_steps = details.get("completed_steps", [])
            details["completed_steps"] = [*completed_steps, *nested_steps]
            details.setdefault("id_map", dict(id_map))
            details.setdefault("failed_step", failed_step)
            details.setdefault("source_untouched", True)
            details.setdefault("partial", True)
            raise PartialFailure(str(exc), **details) from exc
        except Exception as exc:
            if created:
                raise PartialFailure(
                    str(exc),
                    partial=True,
                    source_untouched=True,
                    created_ids=[item["target_id"] for item in created],
                    id_map=id_map,
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
        plan_digest: str,
    ) -> dict[str, Any]:
        MutationPolicy.current().require_experimental_copy()
        self._confirm_source(
            source_id,
            resource_type,
            expected_name,
            expected_parent_id,
            expected_modified,
        )
        plan = self._build_plan(
            source_id,
            destination_parent_id,
            destination_name,
            destination_base_folder,
        )
        if plan["source"]["resource_type"] != resource_type:
            raise ValueError(f"source_id must identify a {resource_type}.")
        if not plan_digest or plan_digest != plan["plan_digest"]:
            raise ValueError("Copy plan is missing or stale. Run plan_copy again before mutation.")
        return self._execute_copy(plan)

    def reconstructive_move_page(
        self,
        page_id: str,
        destination_section_id: str,
        expected_title: str,
        expected_section_id: str,
        plan_digest: str,
        expected_modified: str | None = None,
        destination_title: str = "",
    ) -> dict[str, Any]:
        MutationPolicy.current().require_reconstructive_move_page()
        self._confirm_source(
            page_id,
            "page",
            expected_title,
            expected_section_id,
            expected_modified,
        )
        plan = self._build_plan(
            page_id,
            destination_section_id,
            destination_title,
            operation="reconstructive_move_page",
        )
        if not plan_digest or plan_digest != plan["plan_digest"]:
            raise ValueError(
                "Reconstructive Move plan is missing or stale. Run plan_reconstructive_move_page again."
            )
        execute_started = time.monotonic()
        execute_budget = CopyBudget.current()

        def check_move_deadline() -> None:
            if time.monotonic() - execute_started > execute_budget.max_execute_seconds:
                raise RuntimeError(
                    f"Reconstructive Move execution exceeded {execute_budget.max_execute_seconds} seconds."
                )

        try:
            copied = self._execute_copy(plan)
        except PartialFailure as exc:
            details = dict(exc.details)
            if details.get("outcome") == "copy_unverified":
                details["outcome"] = "copy_only"
                details["source_deleted"] = False
                raise PartialFailure(
                    "The Page subtree target was created, but Copy read-back verification failed; "
                    "source deletion was blocked.",
                    **details,
                ) from exc
            details.setdefault("source_deleted", False)
            raise PartialFailure(str(exc), **details) from exc
        report = copied["copy_report"]
        if not report["lossless"] or not report["verified"]:
            raise PartialFailure(
                "The Page subtree was copied, but source deletion was blocked because fidelity was not verified.",
                partial=True,
                outcome="copy_only",
                source_deleted=False,
                destination=copied.get("item"),
                copy_report=report,
                created_ids=copied["created_ids"],
                warnings=copied.get("warnings", []),
            )

        try:
            check_move_deadline()
            current = self._capture_source(page_id, CopyBudget.current(), time.monotonic())
        except Exception as exc:
            raise PartialFailure(
                "The Page subtree was copied, but the source snapshot could not be revalidated; "
                "source deletion was blocked.",
                partial=True,
                outcome="copy_only",
                source_deleted=False,
                destination=copied.get("item"),
                copy_report=report,
                created_ids=copied["created_ids"],
                source_revalidation_error=str(exc),
            ) from exc
        if current["source_digest"] != plan["source_digest"]:
            raise PartialFailure(
                "The source Page subtree changed after Copy verification; source deletion was blocked.",
                partial=True,
                outcome="copy_only",
                source_deleted=False,
                destination=copied.get("item"),
                copy_report=report,
                created_ids=copied["created_ids"],
            )

        attempted: list[str] = []
        recycled: list[str] = []
        unverified: list[str] = []
        source_pages = [item for item in plan["resources"] if item["resource_type"] == "page"]
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
                final_state = deletion.get("final_state")
                if not isinstance(final_state, dict) or final_state.get("is_in_recycle_bin") is not True:
                    unverified.append(page["id"])
                    raise RuntimeError(
                        f"Source Page '{page['id']}' was removed from the active tree, but recycle-bin "
                        "state could not be verified."
                    )
                recycled.append(page["id"])
        except Exception as exc:
            remaining = [
                page["id"]
                for page in source_pages
                if page["id"] not in recycled and page["id"] not in unverified
            ]
            if recycled:
                outcome = "source_partially_recycled"
            elif unverified:
                outcome = "source_recycle_unverified"
            else:
                outcome = "source_delete_failed"
            raise PartialFailure(
                str(exc),
                partial=True,
                outcome=outcome,
                source_deleted=False,
                attempted_source_ids=attempted,
                recycled_source_ids=recycled,
                deleted_source_ids=recycled,
                unverified_source_ids=unverified,
                remaining_source_ids=remaining,
                destination=copied.get("item"),
                copy_report=report,
                created_ids=copied["created_ids"],
            ) from exc
        copied.update(
            {
                "outcome": "moved",
                "source_deleted": True,
                "source_deleted_to_recycle_bin": True,
                "attempted_source_ids": attempted,
                "recycled_source_ids": recycled,
                "deleted_source_ids": recycled,
                "warnings": [
                    *copied.get("warnings", []),
                    "Reconstructive move created new Page IDs; inbound links outside the copied subtree were not scanned.",
                ],
            }
        )
        return copied
