"""Canonical operation registry and application Handler composition."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import sys
from typing import Any

from .desktop import require_onenote_desktop
from .policy import CopyBudget, MutationPolicy, SearchBudget
from .services import (
    DEFAULT_METADATA_QUERY_PAGE_SIZE,
    DEFAULT_SEARCH_PAGE_SIZE,
    HIERARCHY_BROWSING_TOOLS,
    IDENTIFIER_RESOLUTION_ORDER,
    MAX_HIERARCHY_TREE_ITEMS,
    MAX_METADATA_QUERY_PAGE_SIZE,
    MAX_SEARCH_PAGE_SIZE,
    METADATA_QUERY_KIND,
    METADATA_QUERY_PAGINATION_CONSISTENCY,
    METADATA_QUERY_SCOPE_MODES,
    METADATA_QUERY_TOOLS,
    PAGINATION_CONSISTENCY,
    RESOURCE_TYPES,
    SEARCH_BACKEND,
    SEARCH_SCOPE_MODES,
    ServiceContainer,
)
from .services.operation_runtime import (
    BackendCategory,
    CoordinationMode,
    MutationOperationPolicy,
    OperationKind,
    OperationRegistry,
    OperationSpec,
    STRATEGIES,
)
from .settings import MCP_NAME


COPY_MOVE_OPERATIONS = (
    "copy_page",
    "copy_section",
    "copy_section_group",
    "copy_notebook",
    "move_page",
    "move_section",
    "move_section_group",
)

AUTHORIZATION_POLICIES = {
    **{
        name: "write"
        for name in (
            "create_notebook",
            "create_section_group",
            "create_section",
            "create_page",
            "update_page_title",
            "rename_section_group",
            "rename_section",
            "reorder_page",
            "append_to_page",
            "add_image_to_page",
            "close_notebook",
        )
    },
    "reorder_section": "experimental_reorder_section",
    "reparent_page": "experimental_reparent",
    "reparent_section": "experimental_reparent",
    "reparent_section_group": "experimental_reparent",
    "replace_page_body": "write_delete",
    "delete_page_content": "delete",
    "delete_section_group": "delete",
    "delete_section": "delete",
    "delete_page": "delete",
    "copy_page": "experimental_copy",
    "copy_section": "experimental_copy",
    "copy_section_group": "experimental_copy",
    "copy_notebook": "experimental_copy",
    "move_page": "move_page",
    "move_section": "move_containers",
    "move_section_group": "move_containers",
}


def _authorizer(policy_id: str):
    def authorize(arguments: Mapping[str, Any]) -> None:
        if policy_id == "none":
            return
        policy = MutationPolicy.current()
        if policy_id == "write":
            policy.require_write()
        elif policy_id == "delete":
            policy.require_delete(permanently=bool(arguments.get("permanently", False)))
        elif policy_id == "write_delete":
            policy.require_write()
            policy.require_delete()
        elif policy_id == "experimental_reorder_section":
            policy.require_experimental_reorder("section")
        elif policy_id == "experimental_reparent":
            policy.require_experimental_reparent()
        elif policy_id == "experimental_copy":
            policy.require_experimental_copy()
        elif policy_id == "move_page":
            policy.require_move_page()
        elif policy_id == "move_containers":
            policy.require_move_containers()
        else:
            raise RuntimeError(f"Unknown operation authorization policy: {policy_id}")

    return authorize


def _positional(
    target: Any,
    method: str,
    keys: tuple[str, ...],
    *,
    prefix: tuple[Any, ...] = (),
    suffix: tuple[Any, ...] = (),
):
    def handler(arguments: Mapping[str, Any]) -> dict[str, Any]:
        return getattr(target, method)(
            *prefix,
            *(arguments[key] for key in keys),
            *suffix,
        )

    return handler


def _mutation_policy(
    operation: str,
    *,
    attempt_policy_id: str | None = None,
    saga: bool = False,
) -> MutationOperationPolicy:
    return MutationOperationPolicy(
        attempt_policy_id=attempt_policy_id or f"{operation}_operation",
        replay="never",
        identity=f"{operation}_typed_identity",
        observer=f"{operation}_live_observer",
        partial_boundary=(
            "completed_steps_and_manual_recovery" if saga else "operation_specific_fail_closed"
        ),
        recovery=f"{operation}_recovery",
        saga=saga,
    )


def build_operation_registry(services: ServiceContainer) -> OperationRegistry:
    registry = OperationRegistry()

    def add(
        name: str,
        *,
        kind: OperationKind,
        backend: BackendCategory,
        coordination: CoordinationMode,
        handler: Any,
        handler_id: str,
        capability: str | None = None,
        strategy: str | None = None,
        budget: str = "bounded_by_backend_timeout",
        cache: str = "live",
        retry: str = "never",
        authorization: str | None = None,
        exposures: frozenset[str] = frozenset({"default"}),
        mutation: MutationOperationPolicy | None = None,
        attempt_policy_id: str | None = None,
    ) -> None:
        strategy_id = strategy or kind.value
        authorization_id = authorization or AUTHORIZATION_POLICIES.get(name, "none")
        if kind is OperationKind.MUTATION and authorization_id == "none":
            raise RuntimeError(
                f"Mutation operation {name!r} requires an explicit authorization policy."
            )
        spec = OperationSpec(
            name=name,
            kind=kind,
            capability=capability or name,
            coordination=coordination,
            backend=backend,
            strategy=strategy_id,
            handler=handler_id,
            budget_policy=budget,
            cache_policy=cache,
            retry_policy=retry,
            authorization_policy=authorization_id,
            exposures=exposures,
            mutation=mutation,
            attempt_policy_id=(
                mutation.attempt_policy_id if mutation is not None else attempt_policy_id
            ),
        )
        registry.register(
            spec, STRATEGIES[strategy_id], handler, _authorizer(authorization_id)
        )

    read = dict(
        kind=OperationKind.READ,
        backend=BackendCategory.ONENOTE_COM,
        coordination=CoordinationMode.SHARED,
    )
    mutation = dict(
        kind=OperationKind.MUTATION,
        backend=BackendCategory.ONENOTE_COM,
        coordination=CoordinationMode.EXCLUSIVE,
    )

    # System and typed hierarchy reads.
    add(
        "health_check",
        **read,
        handler=lambda _a: _health_snapshot(services, registry),
        handler_id="system.health_snapshot",
        cache="live_bypass",
    )
    add(
        "resolve_identifier",
        **read,
        handler=lambda a: _resolve_identifier(services, a),
        handler_id="hierarchy.resolve_identifier",
        cache="live_bypass",
    )
    add(
        "get_special_locations",
        **read,
        handler=_positional(services.operations, "special_locations", ()),
        handler_id="operations.special_locations",
    )
    add(
        "list_notebooks",
        **read,
        handler=_positional(services.hierarchy, "list_notebooks", ()),
        handler_id="hierarchy.list_notebooks",
    )
    for name, key, resource_type in (
        ("get_notebook", "notebook_id", "notebook"),
        ("get_section_group", "section_group_id", "section_group"),
        ("get_section", "section_id", "section"),
    ):
        add(
            name,
            **read,
            handler=lambda a, key=key, resource_type=resource_type: {
                "item": services.hierarchy.resource(a[key], resource_type)
            },
            handler_id=f"hierarchy.resource:{resource_type}",
        )

    def metadata_handler(resource_type: str, *, page: bool = False, root: bool = False):
        def handler(a: Mapping[str, Any]) -> dict[str, Any]:
            kwargs = {
                "name_equals": a["title_equals"] if page else a["name_equals"],
                "name_contains": a["title_contains"] if page else a["name_contains"],
                "modified_after": a["modified_after"],
                "modified_before": a["modified_before"],
                "offset": a["offset"],
                "page_size": a["page_size"],
            }
            if not root:
                kwargs["include_recycle_bin"] = a["include_recycle_bin"]
            if page:
                kwargs["section_id"] = a["section_id"]
                kwargs["parent_page_id"] = a["parent_page_id"]
            elif not root:
                kwargs["parent_id"] = a["parent_id"]
            scope = None if root else a["scope"]
            return services.hierarchy.metadata_query(resource_type, scope, **kwargs)

        return handler

    add(
        "query_notebook",
        **read,
        handler=metadata_handler("notebook", root=True),
        handler_id="hierarchy.metadata_query:notebook",
        budget="metadata_query_budget",
        cache="hierarchy_snapshot_eligible",
    )
    add(
        "query_section_group",
        **read,
        handler=metadata_handler("section_group"),
        handler_id="hierarchy.metadata_query:section_group",
        budget="metadata_query_budget",
        cache="hierarchy_snapshot_eligible",
    )
    add(
        "query_section",
        **read,
        handler=metadata_handler("section"),
        handler_id="hierarchy.metadata_query:section",
        budget="metadata_query_budget",
        cache="hierarchy_snapshot_eligible",
    )
    add(
        "query_page",
        **read,
        handler=metadata_handler("page", page=True),
        handler_id="hierarchy.metadata_query:page",
        budget="metadata_query_budget",
        cache="hierarchy_snapshot_eligible",
    )
    add(
        "get_path",
        **read,
        handler=_positional(services.hierarchy, "path", ("object_id",)),
        handler_id="hierarchy.path",
    )
    for name, resource_type in (
        ("expand_notebook", "notebook"),
        ("expand_section_group", "section_group"),
        ("expand_section", "section"),
        ("expand_page", "page"),
    ):
        add(
            name,
            **read,
            handler=_positional(
                services.hierarchy, "expand_typed", ("id",), suffix=(resource_type,)
            ),
            handler_id=f"hierarchy.expand_typed:{resource_type}",
            budget="hierarchy_tree_budget",
        )
    add(
        "expand_hierarchy",
        **read,
        handler=_positional(
            services.hierarchy,
            "expand_hierarchy",
            ("root_id", "max_depth", "include_recycle_bin"),
        ),
        handler_id="hierarchy.expand_hierarchy",
        budget="hierarchy_tree_budget",
    )

    # Page reads and search.
    for name, method, keys in (
        ("get_page", "get", ("page_id",)),
        ("get_page_xml", "get_xml", ("page_id", "page_info")),
        ("get_page_text", "get_text", ("page_id", "max_chars")),
        ("get_page_objects", "get_objects", ("page_id",)),
        ("get_binary_content", "get_binary", ("page_id", "callback_id")),
    ):
        add(
            name,
            **read,
            handler=_positional(services.pages, method, keys),
            handler_id=f"pages.{method}",
            budget="page_read_budget",
            cache="live_bypass" if name in {"get_page_xml", "get_binary_content"} else "live",
        )
    add(
        "search_pages",
        **read,
        handler=_positional(
            services.search,
            "search",
            (
                "query",
                "scope",
                "offset",
                "page_size",
                "include_snippets",
                "include_recycle_bin",
            ),
        ),
        handler_id="search.search",
        budget="search_budget",
        cache="search_snapshot_eligible",
    )

    # Typed OneNote mutations.  attempt_policy_id values matching 029 are the
    # canonical handoff; remaining operations declare their operation policy here.
    mutation_handlers = {
        "create_notebook": (
            services.mutations,
            "create_notebook",
            ("name_or_path", "base_folder"),
            None,
            False,
        ),
        "create_section": (services.mutations, "create_section", ("parent_id", "section_name"), None, False),
        "create_section_group": (services.mutations, "create_section_group", ("parent_id", "group_name"), None, False),
        "create_page": (
            services.mutations,
            "create_page",
            ("section_id", "title", "content", "content_format", "new_page_style"),
            None,
            False,
        ),
        "update_page_title": (
            services.mutations,
            "update_page_title",
            ("page_id", "title", "expected_title", "expected_section_id", "expected_modified"),
            "update_page_title",
            False,
        ),
        "rename_section_group": (services.mutations, "rename_resource", ("section_group_id", "new_name", "expected_name", "expected_parent_id", "expected_modified"), "rename_resource", False),
        "rename_section": (services.mutations, "rename_resource", ("section_id", "new_name", "expected_name", "expected_parent_id", "expected_modified"), "rename_resource", False),
        "reorder_page": (services.mutations, "reorder_page", ("page_id", "expected_title", "expected_section_id", "after_page_id", "page_level", "expected_modified"), "reorder_page", False),
        "reorder_section": (services.mutations, "reorder_section", ("section_id", "expected_name", "expected_parent_id", "after_section_id", "expected_modified"), "reorder_section", False),
        "reparent_page": (services.mutations, "reparent_page", ("page_id", "destination_section_id", "expected_title", "expected_section_id", "expected_modified", "include_descendants"), "reparent_page", True),
        "reparent_section": (services.mutations, "reparent_section", ("section_id", "destination_parent_id", "expected_name", "expected_parent_id", "expected_modified"), "reparent_section", False),
        "reparent_section_group": (services.mutations, "reparent_section_group", ("section_group_id", "destination_parent_id", "expected_name", "expected_parent_id", "expected_modified"), "reparent_section_group", False),
        "append_to_page": (services.mutations, "append_to_page", ("page_id", "content", "expected_title", "expected_section_id", "expected_modified", "content_format", "x", "y"), "append_to_page", False),
        "add_image_to_page": (services.mutations, "add_image_to_page", ("page_id", "image_path", "expected_title", "expected_section_id", "expected_modified", "image_format", "x", "y", "width", "height"), "add_image_to_page", False),
        "replace_page_body": (services.mutations, "replace_page_body", ("page_id", "content", "expected_title", "expected_section_id", "expected_modified", "title", "content_format"), None, True),
        "delete_page_content": (services.mutations, "delete_page_content", ("page_id", "object_id", "expected_title", "expected_section_id", "expected_modified"), "delete_page_content", False),
    }
    for name, (target, method, keys, attempt_id, saga) in mutation_handlers.items():
        if name == "rename_section_group":
            handler = lambda a: services.mutations.rename_resource(
                a["section_group_id"], "section_group", a["new_name"], a["expected_name"], a["expected_parent_id"], a["expected_modified"]
            )
        elif name == "rename_section":
            handler = lambda a: services.mutations.rename_resource(
                a["section_id"], "section", a["new_name"], a["expected_name"], a["expected_parent_id"], a["expected_modified"]
            )
        else:
            handler = _positional(target, method, keys)
        add(
            name,
            **mutation,
            handler=handler,
            handler_id=f"mutations.{method}:{name}",
            mutation=_mutation_policy(name, attempt_policy_id=attempt_id, saga=saga),
        )
    for name, resource_type, id_key, attempt_id in (
        ("delete_section_group", "section_group", "section_group_id", "delete_hierarchy"),
        ("delete_section", "section", "section_id", "delete_hierarchy"),
    ):
        add(
            name,
            **mutation,
            handler=lambda a, resource_type=resource_type, id_key=id_key: services.mutations.delete_resource(
                a[id_key], resource_type, a["expected_name"], a["expected_parent_id"], a["expected_modified"], a["permanently"]
            ),
            handler_id=f"mutations.delete_resource:{resource_type}",
            mutation=_mutation_policy(name, attempt_policy_id=attempt_id),
        )
    add(
        "delete_page",
        **mutation,
        handler=_positional(services.mutations, "delete_page", ("page_id", "expected_title", "expected_section_id", "expected_modified", "permanently")),
        handler_id="mutations.delete_page",
        mutation=_mutation_policy("delete_page", attempt_policy_id="delete_hierarchy"),
    )

    # Copy/Move are operation-wide sagas.  Internal planning is rebuilt live in
    # the same exclusive Runtime call and is never supplied by the MCP client.
    copy_specs = {
        "copy_page": ("page_id", "page", "destination_section_id", "destination_title", "", "expected_title", "expected_section_id", "expected_modified", "include_descendants"),
        "copy_section": ("section_id", "section", "destination_parent_id", "destination_name", "", "expected_name", "expected_parent_id", "expected_modified", None),
        "copy_section_group": ("section_group_id", "section_group", "destination_parent_id", "destination_name", "", "expected_name", "expected_parent_id", "expected_modified", None),
        "copy_notebook": ("notebook_id", "notebook", None, "destination_name", "destination_base_folder", "expected_name", None, "expected_modified", None),
    }
    for name, values in copy_specs.items():
        id_key, resource_type, parent_key, name_key, folder_key, expected_key, expected_parent_key, modified_key, scope_key = values

        def copy_handler(
            a: Mapping[str, Any],
            *,
            id_key=id_key,
            resource_type=resource_type,
            parent_key=parent_key,
            name_key=name_key,
            folder_key=folder_key,
            expected_key=expected_key,
            expected_parent_key=expected_parent_key,
            modified_key=modified_key,
            scope_key=scope_key,
        ) -> dict[str, Any]:
            return services.copying.copy_resource(
                a[id_key],
                resource_type,
                a[parent_key] if parent_key else "",
                a[name_key],
                a[folder_key] if folder_key else "",
                a[expected_key],
                a[expected_parent_key] if expected_parent_key else None,
                a[modified_key],
                a[scope_key] if scope_key else False,
            )

        add(
            name,
            **mutation,
            handler=copy_handler,
            handler_id=f"copying.copy_resource:{resource_type}",
            budget="copy_budget",
            mutation=_mutation_policy(name, attempt_policy_id="copy_saga", saga=True),
        )
    for name, method, keys in (
        ("move_page", "move_page", ("page_id", "destination_section_id", "expected_title", "expected_section_id", "expected_modified", "destination_title", "include_descendants")),
        ("move_section", "move_section", ("section_id", "destination_parent_id", "expected_name", "expected_parent_id", "expected_modified", "destination_name")),
        ("move_section_group", "move_section_group", ("section_group_id", "destination_parent_id", "expected_name", "expected_parent_id", "expected_modified", "destination_name")),
    ):
        add(
            name,
            **mutation,
            handler=_positional(services.copying, method, keys),
            handler_id=f"copying.{method}",
            budget="copy_budget",
            mutation=_mutation_policy(name, attempt_policy_id="move_saga", saga=True),
        )

    # Reads, filesystem effects, UI actions, and lifecycle operations.
    for name, method, keys in (
        ("get_hyperlink", "hyperlink", ("object_id", "page_content_object_id", "web")),
        ("get_parent", "parent", ("object_id",)),
    ):
        add(name, **read, handler=_positional(services.operations, method, keys), handler_id=f"operations.{method}")
    add(
        "publish_object",
        kind=OperationKind.FILESYSTEM_EFFECT,
        backend=BackendCategory.FILESYSTEM,
        coordination=CoordinationMode.SHARED,
        handler=_positional(services.operations, "publish", ("object_id", "target_path", "format", "overwrite")),
        handler_id="operations.publish",
        budget="publish_budget",
        cache="live_bypass",
    )
    for name, method, keys in (
        ("navigate_to", "navigate", ("object_id", "page_content_object_id", "new_window")),
        ("navigate_to_url", "navigate_url", ("url", "new_window")),
    ):
        add(
            name,
            kind=OperationKind.UI_EFFECT,
            backend=BackendCategory.WINDOWS_UI,
            coordination=CoordinationMode.SHARED,
            handler=_positional(services.operations, method, keys),
            handler_id=f"operations.{method}",
            cache="live_bypass",
        )
    add(
        "sync_notebook",
        kind=OperationKind.LIFECYCLE,
        backend=BackendCategory.ONENOTE_COM,
        coordination=CoordinationMode.EXCLUSIVE,
        handler=_positional(services.operations, "sync_notebook", ("notebook_id",)),
        handler_id="operations.sync_notebook",
        retry="never_accepted_completion_unobservable",
        cache="invalidate_before_execute",
    )
    add(
        "close_notebook",
        kind=OperationKind.LIFECYCLE,
        backend=BackendCategory.ONENOTE_COM,
        coordination=CoordinationMode.EXCLUSIVE,
        handler=_positional(services.operations, "close_notebook", ("notebook_id", "expected_name", "expected_modified")),
        handler_id="operations.close_notebook",
        retry="029_close_notebook_execute_once",
        cache="invalidate_before_execute",
        attempt_policy_id="close_notebook",
    )

    return registry


def _resolve_identifier(
    services: ServiceContainer, arguments: Mapping[str, Any]
) -> dict[str, Any]:
    identifier = str(arguments["identifier"])
    if not identifier:
        raise ValueError("identifier is required.")
    normalized_type = str(arguments["item_type"]).strip().casefold() or None
    if normalized_type and normalized_type not in RESOURCE_TYPES:
        allowed = ", ".join(sorted(RESOURCE_TYPES))
        raise ValueError(f"item_type must be empty or one of: {allowed}")
    return {
        "item": services.hierarchy.resolve(identifier, normalized_type),
        "identifier_resolution_order": IDENTIFIER_RESOLUTION_ORDER,
    }


def _health_snapshot(
    services: ServiceContainer, registry: OperationRegistry
) -> dict[str, Any]:
    desktop = require_onenote_desktop()
    items = services.hierarchy.resources(include_recycle_bin=False)
    policy = MutationPolicy.current()
    budget = SearchBudget.current()
    copy_budget = CopyBudget.current()
    return {
        "server": MCP_NAME,
        "transport": "stdio",
        "python_executable": sys.executable,
        "module_path": str((Path(__file__).parent / "tools" / "system.py").resolve()),
        "process_cwd": str(Path.cwd()),
        "onenote_desktop": desktop.as_dict(),
        "timeout_seconds": services.hierarchy.bridge.timeout_seconds,
        "max_text_chars": services.pages.max_text_chars,
        "identifier_resolution_order": IDENTIFIER_RESOLUTION_ORDER,
        "search_backend": SEARCH_BACKEND,
        "search_scope_modes": list(SEARCH_SCOPE_MODES),
        "search_pagination": {
            "default_page_size": DEFAULT_SEARCH_PAGE_SIZE,
            "max_page_size": MAX_SEARCH_PAGE_SIZE,
            "consistency": PAGINATION_CONSISTENCY,
        },
        "metadata_query": {
            "tools": list(METADATA_QUERY_TOOLS),
            "scope_modes": list(METADATA_QUERY_SCOPE_MODES),
            "query_kind": METADATA_QUERY_KIND,
            "pagination": {
                "default_page_size": DEFAULT_METADATA_QUERY_PAGE_SIZE,
                "max_page_size": MAX_METADATA_QUERY_PAGE_SIZE,
                "consistency": METADATA_QUERY_PAGINATION_CONSISTENCY,
            },
        },
        "hierarchy_browsing": {
            "tools": list(HIERARCHY_BROWSING_TOOLS),
            "tree_schema": "tree={item,children[]}",
            "max_tree_items": MAX_HIERARCHY_TREE_ITEMS,
            "page_body_reads": False,
        },
        "content_formats": ["plain", "html", "markdown"],
        "operation_runtime": {
            "enabled": True,
            "registered_operations": len(registry.bindings),
            "default_operations": len(registry.names_for_profile("default")),
            "advanced_operations": len(registry.names_for_profile("advanced")),
            "content_free_audit": True,
        },
        "copy_move": {
            "tools": list(COPY_MOVE_OPERATIONS),
            "single_call": True,
            "public_planning_tools": False,
            "agent_managed_plan_state": False,
            "preview": {
                "available": False,
                "reason": "No public Preview capability is delivered in this release.",
            },
        },
        "mutation_policy": {
            "writes_enabled": policy.writes_enabled,
            "deletes_enabled": policy.deletes_enabled,
            "permanent_deletes_enabled": policy.permanent_deletes_enabled,
            "experimental_reparent_enabled": policy.experimental_reparent_enabled,
            "experimental_reorder_section_enabled": policy.experimental_reorder_section_enabled,
            "experimental_reorder_section_group_enabled": policy.experimental_reorder_section_group_enabled,
            "experimental_copy_enabled": policy.experimental_copy_enabled,
            "move_page_enabled": policy.move_page_enabled,
            "move_containers_enabled": policy.move_containers_enabled,
            "raw_xml_enabled": policy.raw_xml_enabled,
        },
        "search_budget": {
            "max_pages": budget.max_pages,
            "max_page_chars": budget.max_page_chars,
            "max_total_chars": budget.max_total_chars,
            "max_seconds": budget.max_seconds,
            "snippet_chars": budget.snippet_chars,
        },
        "copy_budget": {
            "max_resources": copy_budget.max_resources,
            "max_pages": copy_budget.max_pages,
            "max_content_objects": copy_budget.max_content_objects,
            "max_page_xml_bytes": copy_budget.max_page_xml_bytes,
            "max_total_xml_bytes": copy_budget.max_total_xml_bytes,
            "max_plan_seconds": copy_budget.max_plan_seconds,
            "max_execute_seconds": copy_budget.max_execute_seconds,
        },
        "notebooks": sum(item["resource_type"] == "notebook" for item in items),
        "sections": sum(item["resource_type"] == "section" for item in items),
        "write_backend": "OneNote desktop COM API",
    }
