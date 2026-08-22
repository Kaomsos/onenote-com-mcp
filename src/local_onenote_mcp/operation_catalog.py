"""Canonical operation registry and application Handler composition."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import sys
from typing import Any

from .desktop import launch_onenote_gui as launch_desktop_gui
from .desktop import require_onenote_desktop
from .policy import BatchMutationBudget, CopyBudget, MutationPolicy, SearchBudget
from .services import (
    DEFAULT_METADATA_QUERY_PAGE_SIZE,
    DEFAULT_SEARCH_PAGE_SIZE,
    HIERARCHY_BROWSING_TOOLS,
    MAX_HIERARCHY_TREE_ITEMS,
    MAX_METADATA_QUERY_PAGE_SIZE,
    MAX_SEARCH_PAGE_SIZE,
    METADATA_QUERY_KIND,
    METADATA_QUERY_PAGINATION_CONSISTENCY,
    METADATA_QUERY_SCOPE_MODES,
    METADATA_QUERY_TOOLS,
    PAGINATION_CONSISTENCY,
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
from .debug_trace import disabled_trace_status
from .settings import MCP_NAME
from .tool_surface import (
    INTERNAL_CAPABILITIES,
    USER_TOOL_CATEGORIES,
    USER_TOOL_NAMES,
    category_for_tool,
)


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
            "rename_page",
            "rename_section_group",
            "rename_section",
            "reorder_page",
            "reorder_section",
            "sort_children",
            "append_page_content",
        )
    },
    "create_notebook": "create",
    "create_section_group": "create",
    "create_section": "create",
    "create_page": "create_write",
    "reparent_page": "organize",
    "reparent_section": "organize",
    "reparent_section_group": "organize",
    "replace_page_body": "write_delete",
    "delete_page_content_object": "delete",
    "delete_section_group": "delete",
    "delete_section": "delete",
    "delete_page": "delete",
    "copy_page": "create_write",
    "copy_section": "create_write",
    "copy_section_group": "create_write",
    "copy_notebook": "create_write",
    "move_page": "create_write_delete",
    "move_section": "create_write_delete",
    "move_section_group": "create_write_delete",
    "add_page_image_from_file": "write_local_file",
    "export_object_to_pdf": "local_file",
    "launch_onenote_gui": "ui_control",
    "navigate_to": "ui_control",
    "request_notebook_sync": "notebook_lifecycle",
    "close_notebook": "notebook_lifecycle",
}

# Platform prerequisites are a separate registry policy from authorization.
# Keep this inventory explicit so changing a gate never silently changes GUI
# readiness enforcement (or vice versa).
PLATFORM_PREFLIGHT_POLICIES = {
    name: "onenote_gui_ready"
    for name in (
        "create_notebook",
        "create_section_group",
        "create_section",
        "create_page",
        "rename_page",
        "rename_section_group",
        "rename_section",
        "reorder_page",
        "reorder_section",
        "sort_children",
        "append_page_content",
        "reparent_page",
        "reparent_section",
        "reparent_section_group",
        "replace_page_body",
        "delete_page_content_object",
        "delete_section_group",
        "delete_section",
        "delete_page",
        "copy_page",
        "copy_section",
        "copy_section_group",
        "copy_notebook",
        "move_page",
        "move_section",
        "move_section_group",
        "add_page_image_from_file",
        "export_object_to_pdf",
        "navigate_to",
        "request_notebook_sync",
        "close_notebook",
    )
}


def _authorizer(policy_id: str):
    def authorize(arguments: Mapping[str, Any]) -> None:
        if policy_id == "none":
            return
        policy = MutationPolicy.current()
        if policy_id == "create":
            policy.require_create()
        elif policy_id == "write":
            policy.require_write()
        elif policy_id == "create_write":
            policy.require_create()
            policy.require_write()
        elif policy_id == "delete":
            policy.require_delete(permanently=bool(arguments.get("permanently", False)))
        elif policy_id == "write_delete":
            policy.require_write()
            policy.require_delete()
        elif policy_id == "organize":
            policy.require_organize()
        elif policy_id == "create_write_delete":
            policy.require_create()
            policy.require_write()
            policy.require_delete()
        elif policy_id == "local_file":
            policy.require_local_file_io()
        elif policy_id == "write_local_file":
            policy.require_write()
            policy.require_local_file_io()
        elif policy_id == "ui_control":
            policy.require_ui_control()
        elif policy_id == "notebook_lifecycle":
            policy.require_notebook_lifecycle()
        else:
            raise RuntimeError(f"Unknown operation authorization policy: {policy_id}")

    return authorize


def _platform_preflight(policy_id: str, operation: str):
    def check(_arguments: Mapping[str, Any]) -> None:
        if policy_id == "none":
            return
        if policy_id == "onenote_gui_ready":
            policy = MutationPolicy.current()
            require_onenote_desktop(
                operation=operation,
                ui_control_enabled=policy.ui_control_enabled,
            )
            return
        raise RuntimeError(f"Unknown platform preflight policy: {policy_id}")

    return check


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


def _launch_onenote_gui(services: ServiceContainer) -> dict[str, Any]:
    result = launch_desktop_gui()
    refresh = services.hierarchy.bridge.refresh_com_client()
    result["com_client_refresh"] = refresh.content_free_projection()
    return result


def _uses_batch_mode(
    arguments: Mapping[str, Any],
    *,
    single_fields: tuple[str, ...],
    optional_single_fields: tuple[str, ...] = (),
    batch_confirmation_fields: tuple[str, ...] = (),
    required_common_fields: tuple[str, ...] = (),
    batch_default_only_fields: tuple[tuple[str, Any], ...] = (),
) -> bool:
    """Validate the mutually exclusive legacy-single and bounded-items modes."""

    missing_common = [
        field for field in required_common_fields if arguments.get(field) is None
    ]
    if missing_common:
        raise ValueError("Both modes require: " + ", ".join(missing_common) + ".")
    items = arguments.get("items")
    if items is None:
        missing = [field for field in single_fields if arguments.get(field) is None]
        if missing:
            raise ValueError(
                "Single-item mode requires: " + ", ".join(missing) + "."
            )
        return False
    supplied_single = [
        field
        for field in (*single_fields, *optional_single_fields)
        if arguments.get(field) is not None
    ]
    if supplied_single:
        raise ValueError(
            "items batch mode is mutually exclusive with single-item fields: "
            + ", ".join(supplied_single)
            + "."
        )
    nondefault_single = [
        field
        for field, default in batch_default_only_fields
        if arguments.get(field, default) != default
    ]
    if nondefault_single:
        raise ValueError(
            "items batch mode cannot use non-default single-item fields: "
            + ", ".join(nondefault_single)
            + "."
        )
    missing = [
        field for field in batch_confirmation_fields if arguments.get(field) is None
    ]
    if missing:
        raise ValueError(
            "items batch mode requires parent confirmation fields: "
            + ", ".join(missing)
            + "."
        )
    return True


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


def build_operation_registry(
    services: ServiceContainer,
    *,
    trace_status: Mapping[str, Any] | None = None,
) -> OperationRegistry:
    registry = OperationRegistry()
    resolved_trace_status = (
        dict(trace_status) if trace_status is not None else disabled_trace_status()
    )

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
        platform_preflight: str | None = None,
        exposures: frozenset[str] = frozenset({"default"}),
        mutation: MutationOperationPolicy | None = None,
        attempt_policy_id: str | None = None,
    ) -> None:
        strategy_id = strategy or kind.value
        authorization_id = authorization or AUTHORIZATION_POLICIES.get(name, "none")
        platform_preflight_id = (
            platform_preflight
            or PLATFORM_PREFLIGHT_POLICIES.get(name, "none")
        )
        if kind is OperationKind.MUTATION and authorization_id == "none":
            raise RuntimeError(
                f"Mutation operation {name!r} requires an explicit authorization policy."
            )
        spec = OperationSpec(
            name=name,
            category=category_for_tool(name),
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
            platform_preflight_policy=platform_preflight_id,
            exposures=exposures,
            mutation=mutation,
            attempt_policy_id=(
                mutation.attempt_policy_id if mutation is not None else attempt_policy_id
            ),
        )
        registry.register(
            spec,
            STRATEGIES[strategy_id],
            handler,
            _authorizer(authorization_id),
            _platform_preflight(platform_preflight_id, name),
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
        handler=lambda _a: _health_snapshot(services, registry, resolved_trace_status),
        handler_id="system.health_snapshot",
        cache="live_bypass",
    )
    add(
        "launch_onenote_gui",
        kind=OperationKind.UI_EFFECT,
        backend=BackendCategory.PROCESS,
        coordination=CoordinationMode.EXCLUSIVE,
        handler=lambda _a: _launch_onenote_gui(services),
        handler_id="desktop.launch_onenote_gui",
        cache="live_bypass",
    )
    add(
        "list_notebooks",
        **read,
        handler=_positional(services.hierarchy, "list_notebooks", ()),
        handler_id="hierarchy.list_notebooks",
    )
    for name, key, resource_type in (
        ("get_notebook_metadata", "notebook_id", "notebook"),
        ("get_section_group_metadata", "section_group_id", "section_group"),
        ("get_section_metadata", "section_id", "section"),
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
                "name_equals": (a["title_equals"] if page else a["name_equals"]) or "",
                "name_contains": (a["title_contains"] if page else a["name_contains"]) or "",
                "modified_after": a["modified_after"] or "",
                "modified_before": a["modified_before"] or "",
                "offset": a["offset"],
                "page_size": a["page_size"],
            }
            if not root:
                kwargs["include_recycle_bin"] = a["include_recycle_bin"]
            if page:
                kwargs["section_id"] = a["section_id"] or ""
                kwargs["parent_page_id"] = a["parent_page_id"] or ""
            elif not root:
                kwargs["parent_id"] = a["parent_id"] or ""
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
        "get_hierarchy_path",
        **read,
        handler=_positional(services.hierarchy, "path", ("object_id",)),
        handler_id="hierarchy.path",
    )
    for name, resource_type, id_key in (
        ("expand_notebook", "notebook", "notebook_id"),
        ("expand_section_group", "section_group", "section_group_id"),
        ("expand_section", "section", "section_id"),
        ("expand_page", "page", "page_id"),
    ):
        add(
            name,
            **read,
            handler=_positional(
                services.hierarchy, "expand_typed", (id_key,), suffix=(resource_type,)
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
        ("get_page_metadata", "get", ("page_id",)),
        ("get_page_text", "get_text", ("page_id", "max_chars", "mode")),
        ("get_page_content_objects", "get_content_objects", ("page_id",)),
        (
            "get_page_content_object_binary",
            "get_content_object_binary",
            ("page_id", "page_content_object_id"),
        ),
    ):
        add(
            name,
            **read,
            handler=_positional(services.pages, method, keys),
            handler_id=f"pages.{method}",
            budget="page_read_budget",
            cache="live_bypass" if name == "get_page_content_object_binary" else "live",
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
            lambda a: services.mutations.create_notebook(a["name"], a["base_folder"] or ""),
            "mutations.create_notebook",
            None,
            False,
        ),
        "create_section_group": (
            lambda a: services.mutations.batch_create(
                "section_group", a["parent_id"], a["expected_parent_name"],
                a["expected_parent_modified"], a["items"]
            ) if _uses_batch_mode(a, single_fields=("name",), batch_confirmation_fields=("expected_parent_name",)) else services.mutations.create_section_group(a["parent_id"], a["name"]),
            "mutations.create_section_group",
            None,
            True,
        ),
        "create_section": (
            lambda a: services.mutations.batch_create(
                "section", a["parent_id"], a["expected_parent_name"],
                a["expected_parent_modified"], a["items"]
            ) if _uses_batch_mode(a, single_fields=("name",), batch_confirmation_fields=("expected_parent_name",)) else services.mutations.create_section(a["parent_id"], a["name"]),
            "mutations.create_section",
            None,
            True,
        ),
        "create_page": (
            lambda a: services.mutations.batch_create(
                "page", a["section_id"], a["expected_section_name"],
                a["expected_section_modified"], a["items"]
            ) if _uses_batch_mode(
                a,
                single_fields=("title",),
                batch_confirmation_fields=("expected_section_name",),
                batch_default_only_fields=(("content", ""), ("content_format", "plain")),
            ) else services.mutations.create_page(
                a["section_id"], a["title"], a["content"], a["content_format"], "blank_with_title"
            ),
            "mutations.create_page",
            None,
            True,
        ),
        "rename_page": (
            lambda a: services.mutations.batch_rename("page", a["items"])
            if _uses_batch_mode(a, single_fields=("page_id", "title", "expected_title", "expected_section_id"), optional_single_fields=("expected_modified",))
            else services.mutations.update_page_title(a["page_id"], a["title"], a["expected_title"], a["expected_section_id"], a["expected_modified"]),
            "mutations.update_page_title",
            "update_page_title",
            True,
        ),
        "rename_section_group": (
            lambda a: services.mutations.batch_rename("section_group", a["items"])
            if _uses_batch_mode(a, single_fields=("section_group_id", "new_name", "expected_name", "expected_parent_id"), optional_single_fields=("expected_modified",))
            else services.mutations.rename_resource(
                a["section_group_id"], "section_group", a["new_name"], a["expected_name"], a["expected_parent_id"], a["expected_modified"]
            ),
            "mutations.rename_resource:section_group",
            "rename_resource",
            True,
        ),
        "rename_section": (
            lambda a: services.mutations.batch_rename("section", a["items"])
            if _uses_batch_mode(a, single_fields=("section_id", "new_name", "expected_name", "expected_parent_id"), optional_single_fields=("expected_modified",))
            else services.mutations.rename_resource(
                a["section_id"], "section", a["new_name"], a["expected_name"], a["expected_parent_id"], a["expected_modified"]
            ),
            "mutations.rename_resource:section",
            "rename_resource",
            True,
        ),
        "reorder_page": (
            lambda a: services.mutations.reorder_page(
                a["page_id"], a["expected_title"], a["expected_section_id"], a["after_page_id"] or "", a["page_level"], a["expected_modified"], a["include_subpages"]
            ),
            "mutations.reorder_page",
            "reorder_page",
            False,
        ),
        "reorder_section": (
            lambda a: services.mutations.reorder_section(
                a["section_id"], a["expected_name"], a["expected_parent_id"], a["after_section_id"] or "", a["expected_modified"]
            ),
            "mutations.reorder_section",
            "reorder_section",
            False,
        ),
        "sort_children": (
            lambda a: services.mutations.sort_children(
                a["child_type"], a["parent_id"], a["expected_parent_name"], a["expected_parent_modified"],
                a["expected_child_ids"], a["key"], a["direction"]
            ),
            "mutations.sort_children",
            None,
            False,
        ),
        "reparent_page": (
            lambda a: services.mutations.batch_reparent("page", a["destination_section_id"], a["items"])
            if _uses_batch_mode(
                a,
                single_fields=("page_id", "expected_title", "expected_section_id"),
                optional_single_fields=("expected_modified",),
                required_common_fields=("destination_section_id",),
                batch_default_only_fields=(("include_subpages", False),),
            )
            else services.mutations.reparent_page(
                a["page_id"], a["destination_section_id"], a["expected_title"], a["expected_section_id"], a["expected_modified"], a["include_subpages"]
            ),
            "mutations.reparent_page",
            "reparent_page",
            True,
        ),
        "reparent_section": (
            lambda a: services.mutations.batch_reparent("section", a["destination_parent_id"], a["items"])
            if _uses_batch_mode(
                a,
                single_fields=("section_id", "expected_name", "expected_parent_id"),
                optional_single_fields=("expected_modified",),
                required_common_fields=("destination_parent_id",),
            )
            else services.mutations.reparent_section(a["section_id"], a["destination_parent_id"], a["expected_name"], a["expected_parent_id"], a["expected_modified"]),
            "mutations.reparent_section",
            "reparent_section",
            True,
        ),
        "reparent_section_group": (
            lambda a: services.mutations.batch_reparent("section_group", a["destination_parent_id"], a["items"])
            if _uses_batch_mode(
                a,
                single_fields=("section_group_id", "expected_name", "expected_parent_id"),
                optional_single_fields=("expected_modified",),
                required_common_fields=("destination_parent_id",),
            )
            else services.mutations.reparent_section_group(a["section_group_id"], a["destination_parent_id"], a["expected_name"], a["expected_parent_id"], a["expected_modified"]),
            "mutations.reparent_section_group",
            "reparent_section_group",
            True,
        ),
        "append_page_content": (
            _positional(services.mutations, "append_to_page", ("page_id", "content", "expected_title", "expected_section_id", "expected_modified", "content_format", "x", "y")),
            "mutations.append_to_page",
            "append_to_page",
            False,
        ),
        "add_page_image_from_file": (
            lambda a: services.mutations.add_image_to_page(
                a["page_id"], a["image_path"], a["expected_title"], a["expected_section_id"], a["expected_modified"], "", a["x"], a["y"], a["width"], a["height"]
            ),
            "mutations.add_image_to_page",
            "add_image_to_page",
            False,
        ),
        "replace_page_body": (
            lambda a: services.mutations.replace_page_body(
                a["page_id"], a["content"], a["expected_title"], a["expected_section_id"], a["expected_modified"], None, a["content_format"]
            ),
            "mutations.replace_page_body",
            None,
            True,
        ),
        "delete_page_content_object": (
            lambda a: services.mutations.delete_page_content(
                a["page_id"], a["page_content_object_id"], a["expected_title"], a["expected_section_id"], a["expected_modified"]
            ),
            "mutations.delete_page_content",
            "delete_page_content",
            False,
        ),
    }
    for name, (handler, handler_id, attempt_id, saga) in mutation_handlers.items():
        add(
            name,
            **mutation,
            handler=handler,
            handler_id=handler_id,
            mutation=_mutation_policy(name, attempt_policy_id=attempt_id, saga=saga),
        )
    for name, resource_type, id_key, attempt_id in (
        ("delete_section_group", "section_group", "section_group_id", "delete_hierarchy"),
        ("delete_section", "section", "section_id", "delete_hierarchy"),
    ):
        add(
            name,
            **mutation,
            handler=lambda a, resource_type=resource_type, id_key=id_key: services.mutations.batch_delete(resource_type, a["items"])
            if _uses_batch_mode(a, single_fields=(id_key, "expected_name", "expected_parent_id"), optional_single_fields=("expected_modified",))
            else services.mutations.delete_resource(
                a[id_key], resource_type, a["expected_name"], a["expected_parent_id"], a["expected_modified"], False
            ),
            handler_id=f"mutations.delete_resource:{resource_type}",
            mutation=_mutation_policy(name, attempt_policy_id=attempt_id, saga=True),
        )
    add(
        "delete_page",
        **mutation,
        handler=lambda a: services.mutations.batch_delete("page", a["items"])
        if _uses_batch_mode(a, single_fields=("page_id", "expected_title", "expected_section_id"), optional_single_fields=("expected_modified",), batch_default_only_fields=(("include_subpages", False),))
        else services.mutations.delete_page(
            a["page_id"], a["expected_title"], a["expected_section_id"], a["expected_modified"], False, a["include_subpages"]
        ),
        handler_id="mutations.delete_page",
        mutation=_mutation_policy("delete_page", attempt_policy_id="delete_hierarchy", saga=True),
    )

    # Copy/Move are operation-wide sagas.  Internal planning is rebuilt live in
    # the same exclusive Runtime call and is never supplied by the MCP client.
    copy_specs = {
        "copy_page": ("page_id", "page", "destination_section_id", "destination_title", "", "expected_title", "expected_section_id", "expected_modified", "include_subpages"),
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
                (a[parent_key] or "") if parent_key else "",
                a[name_key] or "",
                (a[folder_key] or "") if folder_key else "",
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
    for name, handler, handler_id in (
        (
            "move_page",
            lambda a: services.copying.move_page(
                a["page_id"], a["destination_section_id"], a["expected_title"], a["expected_section_id"], a["expected_modified"], a["destination_title"] or "", a["include_subpages"]
            ),
            "copying.move_page",
        ),
        (
            "move_section",
            lambda a: services.copying.move_section(
                a["section_id"], a["destination_parent_id"], a["expected_name"], a["expected_parent_id"], a["expected_modified"], a["destination_name"] or ""
            ),
            "copying.move_section",
        ),
        (
            "move_section_group",
            lambda a: services.copying.move_section_group(
                a["section_group_id"], a["destination_parent_id"], a["expected_name"], a["expected_parent_id"], a["expected_modified"], a["destination_name"] or ""
            ),
            "copying.move_section_group",
        ),
    ):
        add(
            name,
            **mutation,
            handler=handler,
            handler_id=handler_id,
            budget="copy_budget",
            mutation=_mutation_policy(name, attempt_policy_id="move_saga", saga=True),
        )

    # Reads, filesystem effects, UI actions, and lifecycle operations.
    add(
        "get_hyperlink",
        **read,
        handler=lambda a: services.operations.hyperlink(
            a["object_id"], a["page_content_object_id"] or "", a["link_type"] == "web"
        ),
        handler_id="operations.hyperlink",
    )
    add(
        "export_object_to_pdf",
        kind=OperationKind.FILESYSTEM_EFFECT,
        backend=BackendCategory.FILESYSTEM,
        coordination=CoordinationMode.SHARED,
        handler=lambda a: services.operations.publish(
            a["object_id"], a["target_path"], "pdf", False
        ),
        handler_id="operations.publish",
        budget="publish_budget",
        cache="live_bypass",
    )
    add(
        "navigate_to",
        kind=OperationKind.UI_EFFECT,
        backend=BackendCategory.WINDOWS_UI,
        coordination=CoordinationMode.SHARED,
        handler=lambda a: services.operations.navigate(
            a["object_id"], a["page_content_object_id"] or "", a["new_window"]
        ),
        handler_id="operations.navigate",
        cache="live_bypass",
    )
    add(
        "request_notebook_sync",
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

    registry.freeze_order(USER_TOOL_NAMES)
    return registry


def _health_snapshot(
    services: ServiceContainer,
    registry: OperationRegistry,
    trace_status: Mapping[str, Any],
) -> dict[str, Any]:
    policy = MutationPolicy.current()
    desktop = require_onenote_desktop(
        ui_control_enabled=policy.ui_control_enabled,
    )
    items = services.hierarchy.resources(include_recycle_bin=False)
    budget = SearchBudget.current()
    copy_budget = CopyBudget.current()
    batch_mutation_budget = BatchMutationBudget.current()
    return {
        "server": MCP_NAME,
        "transport": "stdio",
        "python_executable": sys.executable,
        "module_path": str((Path(__file__).parent / "tools" / "system.py").resolve()),
        "process_cwd": str(Path.cwd()),
        "onenote_desktop": desktop.as_dict(),
        "timeout_seconds": services.hierarchy.bridge.timeout_seconds,
        "max_text_chars": services.pages.max_text_chars,
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
        "page_text_modes": {
            "modes": ["plain", "rich"],
            "default": "rich",
            "rich_format": "sanitized_html_v1",
            "raw_page_xml_exposed": False,
        },
        "operation_runtime": {
            "enabled": True,
            "registered_operations": len(registry.bindings),
            "default_operations": len(registry.names_for_profile("default")),
            "advanced_operations": len(registry.names_for_profile("advanced")),
            "content_free_audit": True,
        },
        "debug_trace": dict(trace_status),
        "tool_surface": {
            "profile": "user",
            "categories": {
                category: len(names) for category, names in USER_TOOL_CATEGORIES.items()
            },
            "internal_and_incubating": {
                "count": len(INTERNAL_CAPABILITIES),
                "exposed": False,
            },
        },
        "copy_move": {
            "tools": list(COPY_MOVE_OPERATIONS),
            "single_call": True,
            "authorization": {
                "copy": ["create", "writes"],
                "move": ["create", "writes", "deletes"],
                "independent_copy_gate": False,
            },
            "page_verification": {
                "capability_aware": True,
                "semantic_content_tier": "semantic_content_v1",
                "unknown_projection": "strict_canonical_fail_closed",
            },
            "public_planning_tools": False,
            "agent_managed_plan_state": False,
            "preview": {
                "available": False,
                "reason": "No public Preview capability is delivered in this release.",
            },
        },
        "mutation_policy": {
            "create_enabled": policy.create_enabled,
            "writes_enabled": policy.writes_enabled,
            "deletes_enabled": policy.deletes_enabled,
            "organize_enabled": policy.organize_enabled,
            "local_file_io_enabled": policy.local_file_io_enabled,
            "ui_control_enabled": policy.ui_control_enabled,
            "notebook_lifecycle_enabled": policy.notebook_lifecycle_enabled,
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
        "batch_mutation_budget": {
            "max_catalog_resources": batch_mutation_budget.max_catalog_resources,
            "max_effective_resources": batch_mutation_budget.max_effective_resources,
            "max_effective_pages": batch_mutation_budget.max_effective_pages,
            "max_direct_siblings": batch_mutation_budget.max_direct_siblings,
            "max_page_content_chars": batch_mutation_budget.max_page_content_chars,
        },
        "notebooks": sum(item["resource_type"] == "notebook" for item in items),
        "sections": sum(item["resource_type"] == "section" for item in items),
        "write_backend": "OneNote desktop COM API",
    }
