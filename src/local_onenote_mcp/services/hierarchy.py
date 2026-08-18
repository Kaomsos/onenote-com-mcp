"""Hierarchy snapshot, query, relationship, and update-XML service."""

from __future__ import annotations

from datetime import datetime, timezone
import re
import time
from typing import Any, Callable
import xml.etree.ElementTree as ET

from ..bridge import OneNoteBridge
from ..constants import HIERARCHY_SCOPES, ONE_NS, XML_SCHEMA_2013
from ..hierarchy import (
    derive_page_relationships,
    display_name,
    filter_resources,
    find_resource_by_id,
    find_resource_by_path,
    find_resources_by_path,
    find_unique_resource_by_path,
    parse_hierarchy,
    resolve_resource,
)
from ..onenote_errors import transient_read_error
from .base import BaseService
from .convergence import (
    DEFAULT_CONVERGENCE_RUNTIME,
    ConvergenceConfig,
    ConvergenceResult,
    ConvergenceRuntime,
    converge,
)


IDENTIFIER_RESOLUTION_ORDER = ["id", "exact_path", "unique_name"]
RESOURCE_TYPES = {"notebook", "section_group", "section", "page"}
METADATA_QUERY_TOOLS = (
    "query_notebook",
    "query_section_group",
    "query_section",
    "query_page",
)
METADATA_QUERY_SCOPE_MODES = ("root", "start_node")
METADATA_QUERY_KIND = "hierarchy_metadata"
METADATA_QUERY_PAGINATION_CONSISTENCY = "live_hierarchy"
DEFAULT_METADATA_QUERY_PAGE_SIZE = 200
MAX_METADATA_QUERY_PAGE_SIZE = 200
HIERARCHY_BROWSING_TOOLS = (
    "list_notebooks",
    "get_hierarchy_path",
    "expand_notebook",
    "expand_section_group",
    "expand_section",
    "expand_page",
    "expand_hierarchy",
)
MAX_HIERARCHY_TREE_ITEMS = 10_000
_QUERY_START_TYPES = {
    "section_group": {"notebook", "section_group"},
    "section": {"notebook", "section_group"},
    "page": {"notebook", "section_group", "section"},
}
_QUERY_SCOPES = {
    "notebook": "notebooks",
    "section_group": "sections",
    "section": "sections",
    "page": "pages",
}
_RFC3339 = re.compile(
    r"^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})$"
)


def _parse_rfc3339(value: str, field: str) -> datetime:
    if not _RFC3339.fullmatch(value):
        raise ValueError(f"{field} must be an RFC 3339 timestamp with an explicit offset or Z.")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value[-1] in "Zz" else value)
    except ValueError as exc:
        raise ValueError(
            f"{field} must be an RFC 3339 timestamp with an explicit offset or Z."
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include an explicit offset or Z.")
    return parsed.astimezone(timezone.utc)


class HierarchyService(BaseService):
    def __init__(
        self,
        bridge: OneNoteBridge,
        *,
        convergence_runtime: ConvergenceRuntime = DEFAULT_CONVERGENCE_RUNTIME,
    ) -> None:
        super().__init__(bridge)
        ET.register_namespace("one", ONE_NS)
        self._last_convergence: ConvergenceResult[Any] | None = None
        self.convergence_runtime = convergence_runtime

    def last_convergence_summary(self) -> dict[str, Any]:
        if self._last_convergence is None:
            return {
                "converged": False,
                "attempts": 0,
                "elapsed_seconds": 0.0,
                "stable_observations": 0,
                "identity_remap": {},
                "transient_errors": [],
            }
        return self._last_convergence.summary()

    @staticmethod
    def _wait_config(retries: int, delay_seconds: float) -> ConvergenceConfig:
        bounded_retries = max(1, int(retries))
        bounded_delay = max(0.0, float(delay_seconds))
        return ConvergenceConfig(
            deadline_seconds=max(0.001, bounded_delay * max(1, bounded_retries - 1) + 0.001),
            interval_seconds=bounded_delay,
            required_stable_observations=2 if bounded_retries >= 2 else 1,
            max_observations=bounded_retries,
        )

    def hierarchy_xml(self, start_id: str = "", scope: str = "pages") -> str:
        return self.call(
            "get_hierarchy",
            start_id=start_id,
            scope=self.enum("scope", scope, HIERARCHY_SCOPES),
            schema=XML_SCHEMA_2013,
        )["xml"]

    def resources(self, include_recycle_bin: bool = False) -> list[dict[str, Any]]:
        items = parse_hierarchy(self.hierarchy_xml("", "pages"))
        return items if include_recycle_bin else self.without_recycle_bin(items)

    def resource(self, object_id: str, resource_type: str | None = None) -> dict[str, Any]:
        if not object_id:
            raise ValueError("An object ID is required.")
        item = find_resource_by_id(self.resources(include_recycle_bin=True), object_id, resource_type)
        if item is None:
            label = resource_type or "object"
            raise ValueError(f"No {label} found for ID '{object_id}'.")
        return item

    def resolve(self, identifier: str, resource_type: str | None = None) -> dict[str, Any]:
        return resolve_resource(self.resources(include_recycle_bin=True), identifier, resource_type)

    def find_path(self, path: str, resource_type: str | None = None) -> dict[str, Any] | None:
        """Compatibility selector for read-only callers; returns the first exact match."""

        return find_resource_by_path(self.resources(include_recycle_bin=True), path, resource_type)

    def find_unique_path(
        self,
        path: str,
        resource_type: str | None = None,
    ) -> dict[str, Any] | None:
        """Return a unique exact-path match and reject duplicate friendly paths."""

        return find_unique_resource_by_path(
            self.resources(include_recycle_bin=True), path, resource_type
        )

    def wait_for(
        self,
        object_id: str,
        resource_type: str | None,
        *,
        predicate: Callable[[dict[str, Any]], bool] | None = None,
        retries: int = 8,
        delay_seconds: float = 0.5,
    ) -> dict[str, Any] | None:
        def observe() -> dict[str, Any] | None:
            try:
                return self.resource(object_id, resource_type)
            except ValueError:
                return None

        result = converge(
            observe,
            lambda item: item is not None and (predicate is None or predicate(item)),
            lambda item: self._resource_identity(item),
            config=self._wait_config(retries, delay_seconds),
            clock=self.convergence_runtime.clock,
            sleeper=self.convergence_runtime.sleeper,
            transient=transient_read_error,
        )
        self._last_convergence = result
        return result.value if result.converged else None

    def wait_for_created(
        self,
        expected_path: str,
        resource_type: str,
        allocated_id: str,
        *,
        expected_parent_id: str | None = None,
        validate_parent: bool = False,
        before_ids: set[str] | None = None,
        expected_name: str | None = None,
        retries: int = 8,
        delay_seconds: float = 0.5,
    ) -> dict[str, Any] | None:
        """Verify a created target by allocated ID, or by one fresh identity remap.

        The COM-returned ID is authoritative when it resolves to the expected active
        type/name-or-path/parent. A fallback is accepted only when the allocated ID
        is absent and exactly one eligible fresh candidate exists. Page callers pass
        ``expected_name`` so logical titles never depend on parsing a display path.
        """

        def observe() -> dict[str, Any] | None:
            resources = self.resources(include_recycle_bin=True)
            allocated = find_resource_by_id(resources, allocated_id)

            def eligible(candidate: dict[str, Any]) -> bool:
                return (
                    (resource_type is None or candidate.get("resource_type") == resource_type)
                    and (
                        display_name(candidate) == expected_name
                        if expected_name is not None
                        else candidate.get("path", "").casefold()
                        == expected_path.casefold()
                    )
                    and candidate.get("is_in_recycle_bin") is not True
                    and (
                        not validate_parent
                        or (
                            candidate.get("section_id")
                            if resource_type == "page"
                            else candidate.get("parent_id")
                        )
                        == expected_parent_id
                    )
                )

            if allocated is not None:
                if eligible(allocated) and (
                    before_ids is None or allocated_id not in before_ids
                ):
                    return allocated
                # A visible allocated ID with the wrong type/name-or-path/parent/state
                # is not evidence for remapping another candidate.
            else:
                identity_matches = [
                    candidate
                    for candidate in resources
                    if eligible(candidate)
                    and (before_ids is None or candidate.get("id") not in before_ids)
                ]
                if len(identity_matches) == 1:
                    return identity_matches[0]
            return None

        result = converge(
            observe,
            lambda item: item is not None,
            lambda item: self._resource_identity(item),
            config=self._wait_config(retries, delay_seconds),
            clock=self.convergence_runtime.clock,
            sleeper=self.convergence_runtime.sleeper,
            identity_remap={},
            transient=transient_read_error,
        )
        if result.converged and result.value is not None and result.value.get("id") != allocated_id:
            result = ConvergenceResult(
                result.converged,
                result.value,
                result.attempts,
                result.elapsed_seconds,
                result.stable_observations,
                result.observation_history,
                result.transient_errors,
                {allocated_id: str(result.value["id"])},
            )
        self._last_convergence = result
        return result.value if result.converged else None

    @staticmethod
    def _resource_identity(item: dict[str, Any] | None) -> tuple[Any, ...] | None:
        if item is None:
            return None
        return (
            item.get("id"),
            item.get("resource_type"),
            item.get("parent_id"),
            item.get("section_id"),
            item.get("path"),
            item.get("is_in_recycle_bin"),
            item.get("order"),
            item.get("page_level"),
            display_name(item),
        )

    @staticmethod
    def friendly_child_path(parent_path: str, child_name: str) -> str:
        normalized = child_name.replace("\\", "/").strip("/")
        if normalized.lower().endswith(".one"):
            normalized = normalized[:-4]
        return f"{parent_path}/{normalized}" if normalized else parent_path

    @staticmethod
    def without_recycle_bin(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [item for item in items if item.get("is_in_recycle_bin") is not True]

    @staticmethod
    def _validate_hierarchy_snapshot(items: list[dict[str, Any]]) -> None:
        """Reject ambiguous or incomplete hierarchy relationship graphs."""

        object_ids = [str(item.get("id", "")) for item in items]
        if any(not object_id for object_id in object_ids):
            raise ValueError("Hierarchy snapshot contains an object without an ID.")
        if len(object_ids) != len(set(object_ids)):
            raise ValueError("Hierarchy snapshot contains duplicate object IDs.")

        by_id = {str(item["id"]): item for item in items}
        expected_parent_types = {
            "section_group": {"notebook", "section_group"},
            "section": {"notebook", "section_group"},
            "page": {"section"},
        }
        for item in items:
            resource_type = str(item.get("resource_type", ""))
            object_id = str(item["id"])
            if resource_type not in RESOURCE_TYPES:
                raise ValueError(f"Hierarchy snapshot contains an unknown resource type for '{object_id}'.")
            parent_id = item.get("parent_id")
            if resource_type == "notebook":
                if parent_id not in {None, ""}:
                    raise ValueError(f"Notebook '{object_id}' has an invalid hierarchy parent.")
                continue
            parent = by_id.get(str(parent_id or ""))
            if parent is None or parent.get("resource_type") not in expected_parent_types[resource_type]:
                raise ValueError(f"Hierarchy relationship is incomplete for '{object_id}'.")
            if resource_type == "page":
                section_id = str(item.get("section_id", ""))
                if section_id != str(parent_id):
                    raise ValueError(f"Page '{object_id}' is not bound to its container Section.")
                parent_page_id = item.get("parent_page_id")
                if parent_page_id:
                    parent_page = by_id.get(str(parent_page_id))
                    if (
                        parent_page is None
                        or parent_page.get("resource_type") != "page"
                        or str(parent_page.get("section_id", "")) != section_id
                    ):
                        raise ValueError(f"Page indentation relationship is incomplete for '{object_id}'.")

        for item in items:
            object_id = str(item["id"])
            seen: set[str] = set()
            current = item
            while current.get("resource_type") != "notebook":
                current_id = str(current["id"])
                if current_id in seen:
                    raise ValueError(f"Hierarchy relationship cycle detected at '{object_id}'.")
                seen.add(current_id)
                parent = by_id.get(str(current.get("parent_id") or ""))
                if parent is None:
                    raise ValueError(f"Hierarchy relationship is incomplete for '{object_id}'.")
                current = parent
            notebook_id = str(current["id"])
            declared_notebook_id = HierarchyService._notebook_id(item)
            if declared_notebook_id and declared_notebook_id != notebook_id:
                raise ValueError(f"Hierarchy Notebook relationship is inconsistent for '{object_id}'.")

        pages_by_section: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            if item.get("resource_type") == "page":
                pages_by_section.setdefault(str(item.get("section_id", "")), []).append(item)
        for section_id, pages in pages_by_section.items():
            relationships = derive_page_relationships(pages)
            for index, (page, expected_parent_page) in enumerate(relationships):
                level = int(page.get("page_level") or 0)
                if level < 1 or level > 3 or (index == 0 and level != 1):
                    raise ValueError(f"Section '{section_id}' has an invalid Page indentation root.")
                expected_parent = (
                    str(expected_parent_page["id"])
                    if expected_parent_page is not None
                    else None
                )
                if page.get("parent_page_id") != expected_parent:
                    raise ValueError(f"Page indentation relationship is inconsistent for '{page['id']}'.")

    def _browsing_snapshot(
        self,
        scope: str,
        *,
        include_recycle_bin: bool,
        root_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        raw_items = parse_hierarchy(self.hierarchy_xml("", scope))
        if root_id is not None:
            root_matches = [
                item for item in raw_items if str(item.get("id", "")) == root_id
            ]
            if not root_matches:
                raise ValueError(f"No object found for ID '{root_id}'.")
            if len(root_matches) != 1:
                raise ValueError(
                    f"Hierarchy snapshot contains duplicate matches for root ID '{root_id}'."
                )
            raw_root = root_matches[0]
            notebook_id = self._notebook_id(raw_root)
            raw_items = [
                item
                for item in raw_items
                if self._notebook_id(item) == notebook_id
            ]
        self._validate_hierarchy_snapshot(raw_items)
        open_notebook_ids = self._open_notebook_ids(raw_items)
        open_items = [
            item
            for item in raw_items
            if self._notebook_id(item) in open_notebook_ids
        ]
        eligible = (
            open_items
            if include_recycle_bin
            else self.without_recycle_bin(open_items)
        )
        self._validate_hierarchy_snapshot(eligible)
        return raw_items, eligible

    @staticmethod
    def _require_browsing_root(
        raw_items: list[dict[str, Any]],
        eligible_items: list[dict[str, Any]],
        root_id: str,
        *,
        expected_type: str | None,
        include_recycle_bin: bool,
    ) -> dict[str, Any]:
        if not root_id or not root_id.strip():
            raise ValueError("A non-empty exact OneNote COM ID is required.")
        raw_root = find_resource_by_id(raw_items, root_id)
        if raw_root is None:
            raise ValueError(f"No object found for ID '{root_id}'.")
        if expected_type is not None and raw_root.get("resource_type") != expected_type:
            raise ValueError(f"ID '{root_id}' does not identify a {expected_type}.")
        notebook_id = HierarchyService._notebook_id(raw_root)
        notebook = find_resource_by_id(raw_items, notebook_id, "notebook")
        if notebook is None or notebook.get("is_open") is False:
            raise ValueError(f"ID '{root_id}' belongs to a closed Notebook.")
        if raw_root.get("is_in_recycle_bin") is True and not include_recycle_bin:
            raise ValueError(f"ID '{root_id}' is in the recycle bin.")
        root = find_resource_by_id(eligible_items, root_id)
        if root is None:
            raise ValueError(f"ID '{root_id}' is not available in the requested hierarchy boundary.")
        return root

    @staticmethod
    def _relationship_children(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        children: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            if item.get("resource_type") == "notebook":
                continue
            parent_id = (
                item.get("parent_page_id") or item.get("section_id")
                if item.get("resource_type") == "page"
                else item.get("parent_id")
            )
            if not parent_id:
                raise ValueError(f"Hierarchy relationship is incomplete for '{item['id']}'.")
            children.setdefault(str(parent_id), []).append(item)
        return children

    @staticmethod
    def _build_tree(
        root: dict[str, Any],
        children: dict[str, list[dict[str, Any]]],
        *,
        max_depth: int | None,
        stop_at_types: frozenset[str] = frozenset(),
    ) -> dict[str, Any]:
        visited: set[str] = set()

        def build(item: dict[str, Any], depth: int) -> dict[str, Any]:
            object_id = str(item["id"])
            if object_id in visited:
                raise ValueError(f"Hierarchy object '{object_id}' appears more than once in the tree.")
            visited.add(object_id)
            if len(visited) > MAX_HIERARCHY_TREE_ITEMS:
                raise ValueError(
                    f"Complete hierarchy tree exceeds the public response boundary of "
                    f"{MAX_HIERARCHY_TREE_ITEMS} items."
                )
            node = {"item": item, "children": []}
            if item.get("resource_type") in stop_at_types:
                return node
            if max_depth is not None and depth >= max(0, max_depth):
                return node
            node["children"] = [
                build(child, depth + 1) for child in children.get(object_id, [])
            ]
            return node

        return build(root, 0)

    def list_notebooks(self) -> dict[str, Any]:
        _, items = self._browsing_snapshot("notebooks", include_recycle_bin=False)
        notebooks = filter_resources(items, "notebook")
        return {"items": notebooks, "count": len(notebooks)}

    def expand_typed(self, root_id: str, resource_type: str) -> dict[str, Any]:
        if resource_type not in RESOURCE_TYPES:
            raise ValueError(f"Unsupported typed hierarchy root '{resource_type}'.")
        scope = "sections" if resource_type in {"notebook", "section_group"} else "pages"
        raw_items, items = self._browsing_snapshot(
            scope,
            include_recycle_bin=False,
            root_id=root_id,
        )
        root = self._require_browsing_root(
            raw_items,
            items,
            root_id,
            expected_type=resource_type,
            include_recycle_bin=False,
        )
        children = self._relationship_children(items)
        stop_at_types = (
            frozenset({"section"})
            if resource_type in {"notebook", "section_group"}
            else frozenset()
        )
        return {
            "tree": self._build_tree(
                root,
                children,
                max_depth=None,
                stop_at_types=stop_at_types,
            )
        }

    def expand_hierarchy(
        self,
        root_id: str,
        max_depth: int = 8,
        include_recycle_bin: bool = False,
    ) -> dict[str, Any]:
        raw_items, items = self._browsing_snapshot(
            "pages",
            include_recycle_bin=include_recycle_bin,
            root_id=root_id,
        )
        root = self._require_browsing_root(
            raw_items,
            items,
            root_id,
            expected_type=None,
            include_recycle_bin=include_recycle_bin,
        )
        return {
            "tree": self._build_tree(
                root,
                self._relationship_children(items),
                max_depth=max_depth,
            )
        }

    @staticmethod
    def _open_notebook_ids(items: list[dict[str, Any]]) -> set[str]:
        return {
            str(item["id"])
            for item in items
            if item.get("resource_type") == "notebook"
            and item.get("is_open") is not False
            and item.get("is_in_recycle_bin") is not True
        }

    @staticmethod
    def _notebook_id(item: dict[str, Any]) -> str:
        return (
            str(item.get("id", ""))
            if item.get("resource_type") == "notebook"
            else str(item.get("notebook_id", ""))
        )

    @staticmethod
    def _is_descendant_or_self(
        object_id: str,
        start_node_id: str,
        by_id: dict[str, dict[str, Any]],
    ) -> bool:
        current_id = object_id
        visited: set[str] = set()
        while current_id and current_id not in visited:
            if current_id == start_node_id:
                return True
            visited.add(current_id)
            current = by_id.get(current_id)
            current_id = str(current.get("parent_id", "")) if current else ""
        return False

    @staticmethod
    def _scope_response(start_node: dict[str, Any] | None, notebook_count: int) -> dict[str, Any]:
        if start_node is None:
            return {"mode": "root", "notebook_count": notebook_count}
        resource_type = str(start_node["resource_type"])
        return {
            "mode": "start_node",
            "resource_type": resource_type,
            "id": str(start_node["id"]),
            "path": str(start_node.get("path", "")),
            "notebook_id": (
                str(start_node["id"])
                if resource_type == "notebook"
                else str(start_node.get("notebook_id", ""))
            ),
        }

    @staticmethod
    def _align_start_fragment(
        raw_items: list[dict[str, Any]],
        catalog: list[dict[str, Any]],
        start_node: dict[str, Any],
        resource_type: str,
        open_notebook_ids: set[str],
    ) -> list[dict[str, Any]]:
        """Align a native start-node fragment to the root container catalog.

        Container records must already exist in the hsSections catalog. Page
        records are accepted only when the fragment binds them to a catalogued
        Section below the exact start node; their stable container fields are
        then rebased to that catalog entry.
        """

        by_id = {str(item["id"]): item for item in catalog if item.get("id")}
        start_id = str(start_node["id"])
        raw_pages = {
            str(item["id"]): item
            for item in raw_items
            if item.get("resource_type") == "page" and item.get("id")
        }
        aligned: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in raw_items:
            object_id = str(raw.get("id", ""))
            if not object_id or object_id in seen or object_id == start_id:
                continue
            candidate_type = str(raw.get("resource_type", ""))
            if candidate_type != resource_type:
                continue
            if candidate_type != "page":
                catalog_item = by_id.get(object_id)
                if (
                    catalog_item is None
                    or catalog_item.get("resource_type") != candidate_type
                    or not HierarchyService._is_descendant_or_self(
                        object_id, start_id, by_id
                    )
                    or HierarchyService._notebook_id(catalog_item) not in open_notebook_ids
                ):
                    continue
                candidate = dict(catalog_item)
            else:
                section_id = str(raw.get("section_id", ""))
                section = by_id.get(section_id)
                if (
                    section is None
                    or section.get("resource_type") != "section"
                    or not HierarchyService._is_descendant_or_self(
                        section_id, start_id, by_id
                    )
                    or HierarchyService._notebook_id(section) not in open_notebook_ids
                ):
                    continue
                parent_page_id = raw.get("parent_page_id")
                if parent_page_id:
                    parent_page = raw_pages.get(str(parent_page_id))
                    if parent_page is None or str(parent_page.get("section_id", "")) != section_id:
                        continue
                candidate = dict(raw)
                candidate["section_id"] = section_id
                candidate["parent_id"] = section_id
                candidate["notebook_id"] = HierarchyService._notebook_id(section)
                candidate["path"] = f"{section.get('path', '')}/{display_name(candidate)}"
                candidate["depth"] = int(section.get("depth", 0)) + 1
                candidate["is_in_recycle_bin"] = (
                    candidate.get("is_in_recycle_bin") is True
                    or section.get("is_in_recycle_bin") is True
                )
            seen.add(object_id)
            aligned.append(candidate)
        return aligned

    def metadata_query(
        self,
        resource_type: str,
        scope_request: dict[str, Any] | None = None,
        *,
        name_equals: str = "",
        name_contains: str = "",
        parent_id: str = "",
        section_id: str = "",
        parent_page_id: str = "",
        modified_after: str = "",
        modified_before: str = "",
        include_recycle_bin: bool = False,
        offset: int = 0,
        page_size: int = DEFAULT_METADATA_QUERY_PAGE_SIZE,
    ) -> dict[str, Any]:
        """Query one fixed hierarchy resource type through its native COM scope."""

        if resource_type not in RESOURCE_TYPES:
            raise ValueError("Unsupported fixed metadata query resource type.")
        if offset < 0:
            raise ValueError("offset must be greater than or equal to 0.")
        if page_size < 1 or page_size > MAX_METADATA_QUERY_PAGE_SIZE:
            raise ValueError(
                f"page_size must be between 1 and {MAX_METADATA_QUERY_PAGE_SIZE}."
            )
        after = _parse_rfc3339(modified_after, "modified_after") if modified_after else None
        before = _parse_rfc3339(modified_before, "modified_before") if modified_before else None
        if after is not None and before is not None and after >= before:
            raise ValueError("modified_after must be earlier than modified_before.")

        if resource_type == "notebook":
            if scope_request is not None:
                raise ValueError("query_notebook has a fixed root scope and does not accept scope.")
            catalog = parse_hierarchy(self.hierarchy_xml("", _QUERY_SCOPES[resource_type]))
            open_notebook_ids = self._open_notebook_ids(catalog)
            start_node = None
            items = [
                item
                for item in catalog
                if item.get("resource_type") == "notebook"
                and str(item.get("id", "")) in open_notebook_ids
            ]
        else:
            if not isinstance(scope_request, dict):
                raise ValueError("scope is required and must be an object.")
            mode = str(scope_request.get("mode", "")).strip().casefold()
            if mode not in METADATA_QUERY_SCOPE_MODES:
                raise ValueError("scope.mode must be one of: root, start_node.")
            if mode == "root":
                if set(scope_request) != {"mode"}:
                    raise ValueError("root scope does not accept additional fields.")
                catalog = parse_hierarchy(self.hierarchy_xml("", _QUERY_SCOPES[resource_type]))
                open_notebook_ids = self._open_notebook_ids(catalog)
                start_node = None
                items = [
                    item
                    for item in catalog
                    if item.get("resource_type") == resource_type
                    and self._notebook_id(item) in open_notebook_ids
                ]
            else:
                if set(scope_request) != {"mode", "start_node_id"}:
                    raise ValueError(
                        "start_node scope requires only mode and start_node_id."
                    )
                start_node_id = str(scope_request.get("start_node_id", "")).strip()
                if not start_node_id:
                    raise ValueError("scope.start_node_id is required for start_node scope.")
                catalog = parse_hierarchy(self.hierarchy_xml("", "sections"))
                by_id = {str(item["id"]): item for item in catalog if item.get("id")}
                open_notebook_ids = self._open_notebook_ids(catalog)
                start_node = by_id.get(start_node_id)
                if start_node is None:
                    raise ValueError(f"No hierarchy object found for ID '{start_node_id}'.")
                if start_node.get("resource_type") not in _QUERY_START_TYPES[resource_type]:
                    allowed = ", ".join(sorted(_QUERY_START_TYPES[resource_type]))
                    raise ValueError(
                        f"scope.start_node_id for query_{resource_type} must identify: {allowed}."
                    )
                if self._notebook_id(start_node) not in open_notebook_ids:
                    raise ValueError("scope.start_node_id must belong to an open Notebook.")
                if start_node.get("is_in_recycle_bin") is True:
                    raise ValueError("scope.start_node_id cannot be in the recycle bin.")
                fragment = parse_hierarchy(
                    self.hierarchy_xml(start_node_id, _QUERY_SCOPES[resource_type])
                )
                items = self._align_start_fragment(
                    fragment,
                    catalog,
                    start_node,
                    resource_type,
                    open_notebook_ids,
                )

        notebook_count = len(open_notebook_ids)
        if not include_recycle_bin:
            items = self.without_recycle_bin(items)
        scope = self._scope_response(start_node, notebook_count)
        scope_by_id = {str(item["id"]): item for item in catalog if item.get("id")}
        relationship_items = list(items)

        if name_equals:
            target = name_equals.casefold()
            items = [item for item in items if display_name(item).casefold() == target]
        if name_contains:
            target = name_contains.casefold()
            items = [item for item in items if target in display_name(item).casefold()]

        if parent_id:
            parent = scope_by_id.get(parent_id)
            if (
                parent is None
                or parent.get("resource_type") not in {"notebook", "section_group"}
                or self._notebook_id(parent) not in open_notebook_ids
                or (not include_recycle_bin and parent.get("is_in_recycle_bin") is True)
                or (
                    start_node is not None
                    and not self._is_descendant_or_self(
                        parent_id, str(start_node["id"]), scope_by_id
                    )
                )
            ):
                raise ValueError(
                    "parent_id must identify a Notebook or SectionGroup within the verified scope."
                )
            items = [item for item in items if item.get("parent_id") == parent_id]

        if section_id:
            section = scope_by_id.get(section_id)
            if (
                section is None
                or section.get("resource_type") != "section"
                or self._notebook_id(section) not in open_notebook_ids
                or (not include_recycle_bin and section.get("is_in_recycle_bin") is True)
                or (
                    start_node is not None
                    and not self._is_descendant_or_self(
                        section_id, str(start_node["id"]), scope_by_id
                    )
                )
            ):
                raise ValueError("section_id must identify a Section within the verified scope.")
            items = [item for item in items if item.get("section_id") == section_id]

        if parent_page_id:
            page_by_id = {
                str(item["id"]): item
                for item in relationship_items
                if item.get("id")
            }
            parent_page = page_by_id.get(parent_page_id)
            if parent_page is None:
                raise ValueError(
                    "parent_page_id must identify a Page within the verified scope."
                )
            if section_id and parent_page.get("section_id") != section_id:
                raise ValueError("parent_page_id must belong to section_id when both are provided.")
            items = [item for item in items if item.get("parent_page_id") == parent_page_id]

        if after is not None or before is not None:
            filtered: list[dict[str, Any]] = []
            for item in items:
                modified = item.get("modified")
                if not modified:
                    continue
                instant = _parse_rfc3339(str(modified), "hierarchy modified time")
                if after is not None and instant <= after:
                    continue
                if before is not None and instant >= before:
                    continue
                filtered.append(item)
            items = filtered

        total_matches = len(items)
        page = items[offset : offset + page_size]
        has_more = offset + len(page) < total_matches
        return {
            "items": page,
            "count": len(page),
            "total_matches": total_matches,
            "offset": offset,
            "page_size": page_size,
            "has_more": has_more,
            "next_offset": offset + len(page) if has_more else None,
            "pagination_consistency": METADATA_QUERY_PAGINATION_CONSISTENCY,
            "resource_type": resource_type,
            "query_kind": METADATA_QUERY_KIND,
            "scope": scope,
        }

    def path(self, object_id: str) -> dict[str, Any]:
        items = self.resources(include_recycle_bin=True)
        by_id = {item["id"]: item for item in items}
        item = by_id.get(object_id)
        if item is None:
            raise ValueError(f"No object found for ID '{object_id}'.")
        ancestors = []
        seen = {object_id}
        parent_id = item.get("parent_id")
        while parent_id:
            if parent_id in seen:
                raise ValueError("Hierarchy path contains an ancestor cycle.")
            seen.add(parent_id)
            parent = by_id.get(parent_id)
            if parent is None:
                raise ValueError("Hierarchy path is incomplete for the requested object.")
            ancestors.append(parent)
            parent_id = parent.get("parent_id")
        ancestors.reverse()
        path_segments = [
            {
                "resource_type": str(segment["resource_type"]),
                "id": str(segment["id"]),
                "name": display_name(segment),
            }
            for segment in [*ancestors, item]
        ]
        return {
            "item": item,
            "path": item["path"],
            "path_segments": path_segments,
            "ancestors": ancestors,
        }

    def update_xml(
        self,
        item: dict[str, Any],
        *,
        catalog: list[dict[str, Any]] | None = None,
        **attributes: str,
    ) -> str:
        all_items = catalog if catalog is not None else self.resources(include_recycle_bin=True)
        by_id = {candidate["id"]: candidate for candidate in all_items}
        chain = [item]
        parent_id = item.get("parent_id")
        while parent_id:
            parent = by_id.get(parent_id)
            if parent is None:
                raise RuntimeError(f"Cannot build hierarchy update: missing ancestor {parent_id}.")
            chain.append(parent)
            parent_id = parent.get("parent_id")
        chain.reverse()
        root = ET.Element(f"{{{ONE_NS}}}Notebooks")
        current = root
        tags = {"notebook": "Notebook", "section_group": "SectionGroup", "section": "Section", "page": "Page"}
        for candidate in chain:
            attrs = {"ID": candidate["id"], "name": display_name(candidate)}
            if candidate is item:
                attrs.update(attributes)
            current = ET.SubElement(current, f"{{{ONE_NS}}}{tags[candidate['resource_type']]}", attrs)
        return ET.tostring(root, encoding="unicode")

    def reparent_xml(
        self,
        target: dict[str, Any],
        destination: dict[str, Any],
        *,
        catalog: list[dict[str, Any]],
    ) -> str:
        """Build an ancestor-complete typed reparent update for one active target."""

        target_type = target.get("resource_type")
        allowed_destinations = {
            "page": {"section"},
            "section": {"notebook", "section_group"},
            "section_group": {"notebook", "section_group"},
        }
        if target_type not in allowed_destinations:
            raise ValueError("Reparent target must be a page, section, or section_group.")
        if destination.get("resource_type") not in allowed_destinations[target_type]:
            raise ValueError(
                f"Invalid destination type for {target_type} reparent: "
                f"{destination.get('resource_type')}."
            )
        all_items = catalog
        by_id = {candidate["id"]: candidate for candidate in all_items}
        chain = [destination]
        parent_id = destination.get("parent_id")
        while parent_id:
            parent = by_id.get(parent_id)
            if parent is None:
                raise RuntimeError(f"Cannot build reparent update: missing ancestor {parent_id}.")
            chain.append(parent)
            parent_id = parent.get("parent_id")
        chain.reverse()
        root = ET.Element(f"{{{ONE_NS}}}Notebooks")
        current = root
        tags = {
            "notebook": "Notebook",
            "section_group": "SectionGroup",
            "section": "Section",
        }
        for candidate in chain:
            current = ET.SubElement(
                current,
                f"{{{ONE_NS}}}{tags[candidate['resource_type']]}",
                {"ID": candidate["id"], "name": display_name(candidate)},
            )
        target_attributes = {"ID": target["id"], "name": display_name(target)}
        if target_type == "page":
            target_attributes["pageLevel"] = str(max(1, int(target.get("page_level") or 1)))
        target_tags = {
            "page": "Page",
            "section": "Section",
            "section_group": "SectionGroup",
        }
        ET.SubElement(
            current,
            f"{{{ONE_NS}}}{target_tags[target_type]}",
            target_attributes,
        )
        return ET.tostring(root, encoding="unicode")

    def reparent_page_scope_xml(
        self,
        pages: list[dict[str, Any]],
        destination: dict[str, Any],
        *,
        catalog: list[dict[str, Any]],
    ) -> str:
        """Build one typed Page-scope reparent update normalized below a Section."""

        if not pages or any(page.get("resource_type") != "page" for page in pages):
            raise ValueError("Page reparent scope must contain one or more Pages.")
        if destination.get("resource_type") != "section":
            raise ValueError("Page reparent destination must be a Section.")
        source_section_ids = {page.get("section_id") for page in pages}
        if len(source_section_ids) != 1 or None in source_section_ids:
            raise ValueError("Page reparent scope must come from one Section.")

        by_id = {candidate["id"]: candidate for candidate in catalog}
        chain = [destination]
        parent_id = destination.get("parent_id")
        while parent_id:
            parent = by_id.get(parent_id)
            if parent is None:
                raise RuntimeError(f"Cannot build reparent update: missing ancestor {parent_id}.")
            chain.append(parent)
            parent_id = parent.get("parent_id")
        chain.reverse()

        root = ET.Element(f"{{{ONE_NS}}}Notebooks")
        current = root
        tags = {
            "notebook": "Notebook",
            "section_group": "SectionGroup",
            "section": "Section",
        }
        for candidate in chain:
            current = ET.SubElement(
                current,
                f"{{{ONE_NS}}}{tags[candidate['resource_type']]}",
                {"ID": candidate["id"], "name": display_name(candidate)},
            )

        root_level = int(pages[0].get("page_level") or 1)
        for page in pages:
            normalized_level = int(page.get("page_level") or 1) - root_level + 1
            if normalized_level < 1:
                raise ValueError("Page reparent scope has invalid relative indentation.")
            ET.SubElement(
                current,
                f"{{{ONE_NS}}}Page",
                {
                    "ID": page["id"],
                    "name": display_name(page),
                    "pageLevel": str(normalized_level),
                },
            )
        return ET.tostring(root, encoding="unicode")

    def page_order_xml(self, section: dict[str, Any], pages: list[dict[str, Any]]) -> str:
        root = ET.fromstring(self.update_xml(section))
        section_node = next(node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "Section")
        for page in pages:
            ET.SubElement(
                section_node,
                f"{{{ONE_NS}}}Page",
                {"ID": page["id"], "name": display_name(page), "pageLevel": str(page["page_level"])},
            )
        return ET.tostring(root, encoding="unicode")

    def container_order_xml(
        self,
        parent: dict[str, Any],
        ordered_children: list[dict[str, Any]],
        *,
        catalog: list[dict[str, Any]],
    ) -> str:
        """Build an ancestor-complete update containing every direct container child."""

        if parent["resource_type"] not in {"notebook", "section_group"}:
            raise ValueError("Container reorder parent must be a notebook or section_group.")
        if any(
            child.get("parent_id") != parent["id"]
            or child.get("resource_type") not in {"section_group", "section"}
            for child in ordered_children
        ):
            raise ValueError("Container reorder children must be direct section/section_group siblings.")
        child_ids = [child["id"] for child in ordered_children]
        if len(child_ids) != len(set(child_ids)):
            raise ValueError("Container reorder children must have unique IDs.")

        root = ET.fromstring(self.update_xml(parent, catalog=catalog))
        parent_node = next(
            node
            for node in root.iter()
            if node.attrib.get("ID") == parent["id"]
        )
        tags = {"section_group": "SectionGroup", "section": "Section"}
        for child in ordered_children:
            ET.SubElement(
                parent_node,
                f"{{{ONE_NS}}}{tags[child['resource_type']]}",
                {"ID": child["id"], "name": display_name(child)},
            )
        return ET.tostring(root, encoding="unicode")
