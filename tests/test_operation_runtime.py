from __future__ import annotations

import ast
import asyncio
from collections.abc import Mapping
from pathlib import Path

import pytest

from local_onenote_mcp import operation_catalog, server
from local_onenote_mcp.desktop import OneNoteDesktopState
from local_onenote_mcp.onenote_errors import OneNoteDesktopNotRunningError
from local_onenote_mcp.services.base import BaseService
from local_onenote_mcp.services.coordination import ReadWriteCoordinator
from local_onenote_mcp.services.operation_runtime import (
    BackendCategory,
    CoordinationMode,
    MutationOperationPolicy,
    OperationKind,
    OperationRegistry,
    OperationRuntime,
    OperationSpec,
    OperationStage,
    STRATEGIES,
    record_backend_call,
)
from local_onenote_mcp.tools import DEFAULT_TOOLS
from local_onenote_mcp.tools.context import get_runtime
from local_onenote_mcp.tool_surface import (
    INTERNAL_CAPABILITY_NAMES,
    LEGACY_PUBLIC_NAMES,
    USER_TOOL_NAME_SET,
)
from local_onenote_mcp.tools.hierarchy import get_notebook_metadata


PUBLIC_AUTHORIZATION_ENV = {
    "writes": "LOCAL_ONENOTE_ENABLE_WRITES",
    "deletes": "LOCAL_ONENOTE_ENABLE_DELETES",
    "organize": "LOCAL_ONENOTE_ENABLE_ORGANIZE",
    "copy": "LOCAL_ONENOTE_ENABLE_COPY",
    "local_file_io": "LOCAL_ONENOTE_ENABLE_LOCAL_FILE_IO",
    "ui_control": "LOCAL_ONENOTE_ENABLE_UI_CONTROL",
    "notebook_lifecycle": "LOCAL_ONENOTE_ENABLE_NOTEBOOK_LIFECYCLE",
}

REQUIRED_GATES_BY_AUTHORIZATION = {
    "none": (),
    "write": ("writes",),
    "delete": ("deletes",),
    "write_delete": ("writes", "deletes"),
    "organize": ("writes", "organize"),
    "copy": ("writes", "copy"),
    "move": ("writes", "copy", "deletes"),
    "local_file": ("local_file_io",),
    "write_local_file": ("writes", "local_file_io"),
    "ui_control": ("ui_control",),
    "notebook_lifecycle": ("notebook_lifecycle",),
}

EXPECTED_OPERATIONS_BY_AUTHORIZATION = {
    "none": (
        "health_check",
        "list_notebooks",
        "get_hierarchy_path",
        "expand_notebook",
        "expand_section_group",
        "expand_section",
        "expand_page",
        "expand_hierarchy",
        "get_notebook_metadata",
        "get_section_group_metadata",
        "get_section_metadata",
        "get_page_metadata",
        "query_notebook",
        "query_section_group",
        "query_section",
        "query_page",
        "search_pages",
        "get_page_text",
        "get_page_content_objects",
        "get_page_content_object_binary",
        "get_hyperlink",
    ),
    "write": (
        "create_notebook",
        "create_section_group",
        "create_section",
        "create_page",
        "rename_page",
        "rename_section_group",
        "rename_section",
        "reorder_page",
        "reorder_section",
        "append_page_content",
    ),
    "delete": (
        "delete_page_content_object",
        "delete_page",
        "delete_section",
        "delete_section_group",
    ),
    "write_delete": ("replace_page_body",),
    "organize": (
        "reparent_page",
        "reparent_section",
        "reparent_section_group",
    ),
    "copy": (
        "copy_page",
        "copy_section",
        "copy_section_group",
        "copy_notebook",
    ),
    "move": ("move_page", "move_section", "move_section_group"),
    "local_file": ("export_object_to_pdf",),
    "write_local_file": ("add_page_image_from_file",),
    "ui_control": ("launch_onenote_gui", "navigate_to"),
    "notebook_lifecycle": ("request_notebook_sync", "close_notebook"),
}

EXPECTED_AUTHORIZATION_BY_OPERATION = {
    operation: authorization
    for authorization, operations in EXPECTED_OPERATIONS_BY_AUTHORIZATION.items()
    for operation in operations
}

EXPECTED_PLATFORM_PREFLIGHT_BY_OPERATION = {
    operation: (
        "onenote_gui_ready"
        if authorization != "none" and operation != "launch_onenote_gui"
        else "none"
    )
    for operation, authorization in EXPECTED_AUTHORIZATION_BY_OPERATION.items()
}

GUI_READY_PREFLIGHT_CASES = tuple(
    pytest.param(operation, authorization, id=operation)
    for operation, authorization in EXPECTED_AUTHORIZATION_BY_OPERATION.items()
    if EXPECTED_PLATFORM_PREFLIGHT_BY_OPERATION[operation] == "onenote_gui_ready"
)

AUTHORIZATION_ALLOW_CASES = tuple(
    pytest.param(operation, authorization, id=operation)
    for operation, authorization in EXPECTED_AUTHORIZATION_BY_OPERATION.items()
)

AUTHORIZATION_DENY_CASES = tuple(
    pytest.param(
        operation,
        authorization,
        missing_gate,
        id=f"{operation}-missing-{missing_gate}",
    )
    for operation, authorization in EXPECTED_AUTHORIZATION_BY_OPERATION.items()
    for missing_gate in REQUIRED_GATES_BY_AUTHORIZATION[authorization]
)


def mutation_policy(name: str = "test_mutation") -> MutationOperationPolicy:
    return MutationOperationPolicy(
        attempt_policy_id=name,
        replay="never",
        identity="exact_typed_identity",
        observer="live_observer",
        partial_boundary="fail_closed",
        recovery="inspect_and_start_a_new_call",
    )


def binding_runtime(
    *,
    name: str = "operation",
    kind: OperationKind = OperationKind.READ,
    coordination: CoordinationMode = CoordinationMode.SHARED,
    backend: BackendCategory = BackendCategory.ONENOTE_COM,
    handler=lambda _arguments: {"value": True},
    coordinator: ReadWriteCoordinator | None = None,
    authorizer=None,
    platform_preflight=None,
    finalizer=None,
    clock=None,
) -> OperationRuntime:
    registry = OperationRegistry()
    policy = mutation_policy(name) if kind is OperationKind.MUTATION else None
    spec = OperationSpec(
        name=name,
        category="test",
        kind=kind,
        capability=name,
        coordination=coordination,
        backend=backend,
        strategy=kind.value,
        handler=f"tests.{name}",
        mutation=policy,
        attempt_policy_id=policy.attempt_policy_id if policy else None,
    )
    registry.register(
        spec,
        STRATEGIES[spec.strategy],
        handler,
        authorizer,
        platform_preflight,
    )
    kwargs = {}
    if finalizer is not None:
        kwargs["finalizer"] = finalizer
    if clock is not None:
        kwargs["clock"] = clock
    return OperationRuntime(
        registry,
        coordinator or ReadWriteCoordinator(default_timeout_seconds=1),
        **kwargs,
    )


def mock_production_operation_runtime(operation: str, handler) -> OperationRuntime:
    source = get_runtime().registry.resolve(operation)
    registry = OperationRegistry()
    registry.register(
        source.spec,
        source.strategy,
        handler,
        source.authorizer,
        source.platform_preflight,
    )
    return OperationRuntime(
        registry,
        ReadWriteCoordinator(default_timeout_seconds=1),
    )


def set_public_authorization_environment(monkeypatch, enabled_gates) -> None:
    enabled = set(enabled_gates)
    for gate, env_name in PUBLIC_AUTHORIZATION_ENV.items():
        monkeypatch.setenv(env_name, "true" if gate in enabled else "false")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_PERMANENT_DELETES", "false")


def test_public_operation_authorization_mapping_is_frozen_for_all_52_tools() -> None:
    actual = {
        operation: binding.spec.authorization_policy
        for operation, binding in get_runtime().registry.bindings.items()
    }

    assert len(EXPECTED_AUTHORIZATION_BY_OPERATION) == 52
    assert len(AUTHORIZATION_ALLOW_CASES) == 52
    assert len(AUTHORIZATION_DENY_CASES) == 46
    assert actual == EXPECTED_AUTHORIZATION_BY_OPERATION


def test_gui_readiness_is_an_independent_explicit_registry_policy() -> None:
    actual = {
        operation: binding.spec.platform_preflight_policy
        for operation, binding in get_runtime().registry.bindings.items()
    }

    assert len(GUI_READY_PREFLIGHT_CASES) == 30
    assert actual == EXPECTED_PLATFORM_PREFLIGHT_BY_OPERATION
    assert actual["health_check"] == "none"
    assert actual["get_page_text"] == "none"
    assert actual["launch_onenote_gui"] == "none"


@pytest.mark.parametrize(
    ("operation", "authorization"), AUTHORIZATION_ALLOW_CASES
)
def test_each_public_operation_accepts_its_exact_minimum_authorization(
    monkeypatch, operation, authorization
) -> None:
    required_gates = REQUIRED_GATES_BY_AUTHORIZATION[authorization]
    set_public_authorization_environment(monkeypatch, required_gates)
    monkeypatch.setattr(
        operation_catalog,
        "require_onenote_desktop",
        lambda **_kwargs: OneNoteDesktopState(True, True),
    )
    handler_calls = []

    def handler(arguments):
        handler_calls.append(dict(arguments))
        record_backend_call("mock_backend")
        return {}

    runtime = mock_production_operation_runtime(operation, handler)
    outcome = runtime.execute(operation, {})

    assert outcome.success is True
    assert outcome.error is None
    assert outcome.backend_calls == 1
    assert handler_calls == [{}]


@pytest.mark.parametrize(
    ("operation", "authorization"), GUI_READY_PREFLIGHT_CASES
)
def test_each_authorized_effect_rejects_when_gui_is_not_ready_before_backend(
    monkeypatch, operation, authorization
) -> None:
    set_public_authorization_environment(
        monkeypatch, REQUIRED_GATES_BY_AUTHORIZATION[authorization]
    )
    preflight_calls = []

    def reject_gui(**kwargs):
        preflight_calls.append(kwargs)
        raise OneNoteDesktopNotRunningError(
            "The operation requires OneNote Desktop to be running with a visible GUI.",
            operation=kwargs["operation"],
            details={
                "failed_precondition": "onenote_gui_ready",
                "recovery": {
                    "sequence": [
                        "health_check",
                        "launch_onenote_gui",
                        "health_check",
                        "retry_original_operation",
                    ]
                },
            },
        )

    monkeypatch.setattr(
        operation_catalog, "require_onenote_desktop", reject_gui
    )

    def handler(_arguments):
        raise AssertionError("GUI preflight must prevent Handler execute")

    runtime = mock_production_operation_runtime(operation, handler)
    generation = runtime.coordinator.generation
    outcome = runtime.execute(operation, {})

    assert outcome.success is False
    assert isinstance(outcome.error, OneNoteDesktopNotRunningError)
    assert outcome.stage is OperationStage.PLATFORM_PREFLIGHT
    assert outcome.backend_calls == 0
    assert outcome.generation_before == outcome.generation_after == generation
    assert preflight_calls == [
        {
            "operation": operation,
            "ui_control_enabled": (
                "ui_control" in REQUIRED_GATES_BY_AUTHORIZATION[authorization]
            ),
        }
    ]


@pytest.mark.parametrize(
    ("operation", "enabled_gates"),
    (
        ("health_check", ()),
        ("get_page_text", ()),
        ("launch_onenote_gui", ("ui_control",)),
    ),
)
def test_health_reads_and_launch_do_not_require_gui_ready_preflight(
    monkeypatch, operation, enabled_gates
) -> None:
    set_public_authorization_environment(monkeypatch, enabled_gates)
    monkeypatch.setattr(
        operation_catalog,
        "require_onenote_desktop",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError(f"{operation} must be exempt from GUI preflight")
        ),
    )
    handler_calls = []

    def handler(arguments):
        handler_calls.append(dict(arguments))
        record_backend_call("mock_backend")
        return {}

    outcome = mock_production_operation_runtime(operation, handler).execute(
        operation, {}
    )

    assert outcome.success is True
    assert outcome.backend_calls == 1
    assert handler_calls == [{}]


@pytest.mark.parametrize(
    ("operation", "authorization", "missing_gate"), AUTHORIZATION_DENY_CASES
)
def test_each_required_gate_rejects_before_backend_for_every_public_operation(
    monkeypatch, operation, authorization, missing_gate
) -> None:
    required_gates = set(REQUIRED_GATES_BY_AUTHORIZATION[authorization])
    assert missing_gate in required_gates
    enabled_gates = set(PUBLIC_AUTHORIZATION_ENV) - {missing_gate}
    set_public_authorization_environment(monkeypatch, enabled_gates)
    monkeypatch.setattr(
        operation_catalog,
        "require_onenote_desktop",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("authorization must reject before GUI preflight")
        ),
    )
    handler_calls = []

    def handler(arguments):
        handler_calls.append(dict(arguments))
        raise AssertionError("authorization rejection must prevent Handler execute")

    runtime = mock_production_operation_runtime(operation, handler)
    generation = runtime.coordinator.generation
    outcome = runtime.execute(operation, {})

    assert outcome.success is False
    assert isinstance(outcome.error, PermissionError)
    assert outcome.stage is OperationStage.AUTHORIZATION
    assert outcome.backend_calls == 0
    assert handler_calls == []
    assert outcome.generation_before == outcome.generation_after == generation


def test_registry_is_the_unique_default_and_advanced_tool_inventory() -> None:
    registry = get_runtime().registry
    default_names = {tool.__name__ for tool in DEFAULT_TOOLS}
    advanced_names = set(registry.names_for_profile("advanced"))

    assert registry.names_for_profile("default") == default_names
    assert registry.names_for_profile("advanced") == advanced_names
    assert advanced_names == set()
    assert len(registry.bindings) == len(default_names) == 52
    assert default_names == USER_TOOL_NAME_SET
    assert INTERNAL_CAPABILITY_NAMES.isdisjoint(default_names | advanced_names)
    assert LEGACY_PUBLIC_NAMES.isdisjoint(default_names | advanced_names)
    assert {
        "plan_copy",
        "plan_move_page",
        "plan_move_section",
        "plan_move_section_group",
        "preview_copy",
        "preview_move",
        "find_meta",
        "open_hierarchy",
        "update_page_xml",
        "merge_sections",
        "set_filing_location",
        "reorder_section_group",
    }.isdisjoint(default_names | advanced_names)
    for name, binding in registry.bindings.items():
        assert binding.spec.name == name
        assert binding.spec.strategy == binding.strategy.name
        assert binding.spec.handler
        assert callable(binding.handler)
        assert binding.spec.authorization_policy
        assert binding.spec.platform_preflight_policy
        assert callable(binding.authorizer)
        assert callable(binding.platform_preflight)
        if binding.spec.kind is OperationKind.MUTATION:
            assert binding.spec.authorization_policy != "none"


def test_every_non_read_operation_has_a_named_black_box_manual_scenario() -> None:
    import tests.manual_validation.scenarios  # noqa: F401
    from tests.manual_validation.scenarios.common.registry import SCENARIO_REGISTRY

    non_read = {
        name
        for name, binding in get_runtime().registry.bindings.items()
        if binding.spec.kind is not OperationKind.READ
    }
    covered = {
        tool
        for scenario in SCENARIO_REGISTRY.values()
        for tool in scenario.spec.tool_allowlist
    }

    # GUI launch has a separate human acceptance flow because the standard
    # runner must prove OneNote is already running before any Scenario starts.
    assert non_read - covered == {"launch_onenote_gui"}
    assert "launch_onenote_gui" not in covered


def test_manual_validation_never_imports_the_operation_control_plane() -> None:
    manual_root = Path(__file__).parent / "manual_validation"
    forbidden_modules = {
        "local_onenote_mcp.operation_catalog",
        "local_onenote_mcp.server",
        "local_onenote_mcp.services.mutation_control",
        "local_onenote_mcp.services.operation_runtime",
        "local_onenote_mcp.tools.context",
        "local_onenote_mcp.tools.responses",
    }
    violations: list[str] = []

    for path in sorted(manual_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            imported: list[str] = []
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                imported.append(module)
                imported.extend(
                    f"{module}.{alias.name}" if module else alias.name
                    for alias in node.names
                )
            for module in imported:
                if any(
                    module == forbidden or module.startswith(f"{forbidden}.")
                    for forbidden in forbidden_modules
                ):
                    violations.append(
                        f"{path.relative_to(manual_root)}:{node.lineno}:{module}"
                    )

    assert violations == []


def test_registry_covers_five_effect_kinds_without_mutation_semantic_leakage() -> None:
    bindings = get_runtime().registry.bindings

    assert bindings["get_notebook_metadata"].spec.kind is OperationKind.READ
    assert bindings["get_notebook_metadata"].spec.coordination is CoordinationMode.SHARED
    assert bindings["rename_section"].spec.kind is OperationKind.MUTATION
    assert bindings["rename_section"].spec.coordination is CoordinationMode.EXCLUSIVE
    assert bindings["request_notebook_sync"].spec.kind is OperationKind.LIFECYCLE
    assert bindings["export_object_to_pdf"].spec.kind is OperationKind.FILESYSTEM_EFFECT
    assert bindings["export_object_to_pdf"].spec.backend is BackendCategory.FILESYSTEM
    assert bindings["navigate_to"].spec.kind is OperationKind.UI_EFFECT
    assert bindings["navigate_to"].spec.backend is BackendCategory.WINDOWS_UI

    mutation_stages = bindings["rename_section"].strategy.stages
    assert mutation_stages == (
        OperationStage.PREFLIGHT,
        OperationStage.EXECUTE,
        OperationStage.OBSERVE,
        OperationStage.RECONCILE,
        OperationStage.CONVERGE,
        OperationStage.POSTCONDITION,
    )
    assert OperationStage.RECONCILE not in bindings["get_notebook_metadata"].strategy.stages
    assert OperationStage.RECONCILE not in bindings["request_notebook_sync"].strategy.stages
    assert OperationStage.RECONCILE not in bindings["export_object_to_pdf"].strategy.stages
    assert OperationStage.RECONCILE not in bindings["navigate_to"].strategy.stages


def test_registry_authorization_catalog_is_explicit_for_risk_classes() -> None:
    bindings = get_runtime().registry.bindings

    assert bindings["create_page"].spec.authorization_policy == "write"
    assert bindings["delete_page"].spec.authorization_policy == "delete"
    assert bindings["replace_page_body"].spec.authorization_policy == "write_delete"
    assert (
        bindings["reparent_page"].spec.authorization_policy
        == "organize"
    )
    assert bindings["copy_page"].spec.authorization_policy == "copy"
    assert bindings["move_page"].spec.authorization_policy == "move"
    assert bindings["move_section"].spec.authorization_policy == "move"
    assert bindings["close_notebook"].spec.authorization_policy == "notebook_lifecycle"
    assert bindings["request_notebook_sync"].spec.authorization_policy == "notebook_lifecycle"
    assert bindings["launch_onenote_gui"].spec.authorization_policy == "ui_control"


def test_production_authorization_rejects_before_coordination_or_argument_access(
    monkeypatch,
) -> None:
    monkeypatch.delenv("LOCAL_ONENOTE_ENABLE_WRITES", raising=False)
    monkeypatch.delenv("LOCAL_ONENOTE_ENABLE_COPY", raising=False)
    runtime = get_runtime()
    generation = runtime.coordinator.generation

    outcome = runtime.execute("copy_page", {})

    assert outcome.success is False
    assert isinstance(outcome.error, PermissionError)
    assert outcome.stage is OperationStage.AUTHORIZATION
    assert outcome.backend_calls == 0
    assert outcome.generation_before == outcome.generation_after == generation


def test_launch_authorization_rejects_before_any_process_side_effect(monkeypatch) -> None:
    monkeypatch.delenv("LOCAL_ONENOTE_ENABLE_UI_CONTROL", raising=False)
    runtime = get_runtime()
    generation = runtime.coordinator.generation

    outcome = runtime.execute("launch_onenote_gui", {})

    assert outcome.success is False
    assert isinstance(outcome.error, PermissionError)
    assert outcome.stage is OperationStage.AUTHORIZATION
    assert outcome.backend_calls == 0
    assert outcome.generation_before == outcome.generation_after == generation


def test_registry_rejects_duplicate_operations_and_incomplete_profile() -> None:
    registry = OperationRegistry()
    spec = OperationSpec(
        name="read",
        category="test",
        kind=OperationKind.READ,
        capability="read",
        coordination=CoordinationMode.SHARED,
        backend=BackendCategory.ONENOTE_COM,
        strategy="read",
        handler="tests.read",
    )
    registry.register(spec, STRATEGIES["read"], lambda _a: {})

    try:
        registry.register(spec, STRATEGIES["read"], lambda _a: {})
    except ValueError as exc:
        assert "Duplicate" in str(exc)
    else:
        raise AssertionError("Duplicate operation registration must fail closed.")

    try:
        registry.audit_public_tools(("missing",), profile="default")
    except RuntimeError as exc:
        assert "unregistered" in str(exc)
    else:
        raise AssertionError("Unregistered public tools must fail startup audit.")

    protected = OperationSpec(
        name="protected",
        category="test",
        kind=OperationKind.READ,
        capability="protected",
        coordination=CoordinationMode.SHARED,
        backend=BackendCategory.ONENOTE_COM,
        strategy="read",
        handler="tests.protected",
        platform_preflight_policy="onenote_gui_ready",
    )
    with pytest.raises(ValueError, match="has no preflight binding"):
        OperationRegistry().register(
            protected, STRATEGIES["read"], lambda _a: {}
        )


def test_mutation_invalidates_generation_once_and_counts_all_base_service_calls() -> None:
    invalidations: list[int] = []
    coordinator = ReadWriteCoordinator(
        default_timeout_seconds=1,
        mutation_invalidator=invalidations.append,
    )

    class Bridge:
        def call(self, operation: str, **_params):
            return {"operation": operation}

    service = BaseService(Bridge())
    runtime = binding_runtime(
        name="mutation",
        kind=OperationKind.MUTATION,
        coordination=CoordinationMode.EXCLUSIVE,
        coordinator=coordinator,
        handler=lambda _a: service.call("backend_once"),
    )

    outcome = runtime.execute("mutation", {})

    assert outcome.success is True
    assert outcome.backend_calls == 1
    assert outcome.generation_before == 0
    assert outcome.generation_after == 1
    assert invalidations == [1]


def test_policy_rejection_happens_before_backend_execute_and_releases_writer() -> None:
    coordinator = ReadWriteCoordinator(default_timeout_seconds=0.1)
    runtime = binding_runtime(
        name="mutation",
        kind=OperationKind.MUTATION,
        coordination=CoordinationMode.EXCLUSIVE,
        coordinator=coordinator,
        authorizer=lambda _a: (_ for _ in ()).throw(PermissionError("disabled")),
        handler=lambda _a: (_ for _ in ()).throw(
            AssertionError("authorization rejection must prevent Handler execute")
        ),
    )

    outcome = runtime.execute("mutation", {})

    assert outcome.success is False
    assert outcome.backend_calls == 0
    assert outcome.stage is OperationStage.AUTHORIZATION
    assert outcome.generation_before == outcome.generation_after == 0
    with coordinator.read(timeout_seconds=0.01):
        pass


def test_platform_preflight_runs_after_authorization_and_before_coordination() -> None:
    events: list[str] = []
    coordinator = ReadWriteCoordinator(
        default_timeout_seconds=0.1,
        mutation_invalidator=lambda _generation: events.append("coordination"),
    )
    runtime = binding_runtime(
        name="mutation",
        kind=OperationKind.MUTATION,
        coordination=CoordinationMode.EXCLUSIVE,
        coordinator=coordinator,
        authorizer=lambda _a: events.append("authorization"),
        platform_preflight=lambda _a: events.append("platform_preflight"),
        handler=lambda _a: events.append("handler") or {},
    )

    outcome = runtime.execute("mutation", {})

    assert outcome.success is True
    assert events == [
        "authorization",
        "platform_preflight",
        "coordination",
        "handler",
    ]


def test_handler_and_finalize_failures_release_lease_and_preserve_stage() -> None:
    coordinator = ReadWriteCoordinator(default_timeout_seconds=0.1)
    handler_runtime = binding_runtime(
        coordinator=coordinator,
        handler=lambda _a: (_ for _ in ()).throw(RuntimeError("handler bug")),
    )
    handler_outcome = handler_runtime.execute("operation", {})
    assert handler_outcome.success is False
    assert handler_outcome.stage is OperationStage.EXECUTE
    with coordinator.mutation(timeout_seconds=0.01):
        pass


def test_base_exception_exit_releases_lease_and_execution_context() -> None:
    coordinator = ReadWriteCoordinator(default_timeout_seconds=0.1)
    runtime = binding_runtime(
        coordinator=coordinator,
        handler=lambda _a: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    with pytest.raises(KeyboardInterrupt):
        runtime.execute("operation", {})

    with coordinator.mutation(timeout_seconds=0.01):
        pass

    # A subsequent operation must not inherit the aborted execution's backend count.
    healthy = binding_runtime(coordinator=coordinator)
    assert healthy.execute("operation", {}).backend_calls == 0

    finalize_runtime = binding_runtime(
        coordinator=coordinator,
        finalizer=lambda _execution: (_ for _ in ()).throw(RuntimeError("finalize bug")),
    )
    finalize_outcome = finalize_runtime.execute("operation", {})
    assert finalize_outcome.success is False
    assert finalize_outcome.stage is OperationStage.FINALIZE
    with coordinator.mutation(timeout_seconds=0.01):
        pass


def test_coordination_timeout_and_pre_execute_deadline_release_all_state() -> None:
    coordinator = ReadWriteCoordinator(default_timeout_seconds=0.1)
    runtime = binding_runtime(coordinator=coordinator)
    with coordinator.mutation(timeout_seconds=0.01):
        timeout = runtime.execute("operation", {}, timeout_seconds=0.001)
    assert timeout.success is False
    assert timeout.stage is OperationStage.COORDINATION
    with coordinator.read(timeout_seconds=0.01):
        pass

    ticks = iter((0.0, 2.0))
    deadline_runtime = binding_runtime(clock=lambda: next(ticks))
    deadline = deadline_runtime.execute("operation", {}, timeout_seconds=1)
    assert deadline.success is False
    assert deadline.stage is OperationStage.EXECUTE
    assert isinstance(deadline.error, TimeoutError)


def test_error_stage_from_reconciliation_failure_is_preserved_and_lease_released() -> None:
    coordinator = ReadWriteCoordinator(default_timeout_seconds=0.1)

    class ReconciliationFailure(RuntimeError):
        details = {
            "stage": "reconcile",
            "attempts": 1,
            "replayed": False,
            "reconciliation": "indeterminate",
            "retryability": "manual_recovery_required",
            "recovery_action": "inspect_live_state",
        }

    runtime = binding_runtime(
        name="mutation",
        kind=OperationKind.MUTATION,
        coordination=CoordinationMode.EXCLUSIVE,
        coordinator=coordinator,
        handler=lambda _a: (_ for _ in ()).throw(ReconciliationFailure()),
    )
    outcome = runtime.execute("mutation", {})

    assert outcome.stage is OperationStage.RECONCILE
    assert outcome.observed_outcome == "indeterminate"
    assert outcome.retry_safety == "manual_recovery_required"
    assert outcome.recommended_action == "inspect_live_state"
    with coordinator.read(timeout_seconds=0.01):
        pass


def test_audit_and_completed_steps_are_content_free_allowlist_projections() -> None:
    secret = "sensitive-page-body-and-C:/private/notebook.one"
    runtime = binding_runtime(
        handler=lambda _a: {
            "value": secret,
            "completed_steps": [
                {
                    "operation": "copy_resource",
                    "status": "completed",
                    "object_id": "private-object-id",
                    "raw_xml": secret,
                }
            ],
        }
    )
    outcome = runtime.execute(
        "operation",
        {"xml": secret, "secret": secret, "target_path": secret},
    )

    assert outcome.success is True
    assert outcome.completed_steps == (
        {"operation": "copy_resource", "status": "completed"},
    )
    audit_text = repr(runtime.audit_events)
    assert secret not in audit_text
    assert "private-object-id" not in audit_text
    assert "raw_xml" not in audit_text
    assert runtime.audit_events[-1]["content_exposed"] is False


def test_strategy_specific_outcomes_do_not_fake_completion_semantics() -> None:
    cases = (
        (
            OperationKind.LIFECYCLE,
            CoordinationMode.EXCLUSIVE,
            BackendCategory.ONENOTE_COM,
            {"accepted": True, "completion_observable": False},
            "accepted_completion_unobservable",
        ),
        (
            OperationKind.UI_EFFECT,
            CoordinationMode.SHARED,
            BackendCategory.WINDOWS_UI,
            {"navigated": True},
            "action_accepted",
        ),
        (
            OperationKind.FILESYSTEM_EFFECT,
            CoordinationMode.SHARED,
            BackendCategory.FILESYSTEM,
            {"published": True},
            "filesystem_effect_completed",
        ),
    )
    for kind, coordination, backend, result, expected in cases:
        runtime = binding_runtime(
            kind=kind,
            coordination=coordination,
            backend=backend,
            handler=lambda _a, result=result: result,
        )
        outcome = runtime.execute("operation", {})
        assert outcome.observed_outcome == expected


def test_runtime_composes_nested_029_attempt_outcome_into_operation_outcome() -> None:
    runtime = binding_runtime(
        name="mutation",
        kind=OperationKind.MUTATION,
        coordination=CoordinationMode.EXCLUSIVE,
        handler=lambda _a: {
            "reconciliation": {
                "mutation_attempts": 1,
                "mutation_replayed": False,
                "observed_outcome": "applied",
                "retry_safety": "not_needed",
                "recommended_action": "none",
            }
        },
    )

    outcome = runtime.execute("mutation", {})

    assert outcome.attempts == 1
    assert outcome.replayed is False
    assert outcome.observed_outcome == "applied"
    assert outcome.retry_safety == "not_needed"
    assert outcome.recommended_action == "none"


def test_lifecycle_close_can_report_nested_029_attempt_without_mutation_semantics() -> None:
    runtime = binding_runtime(
        kind=OperationKind.LIFECYCLE,
        coordination=CoordinationMode.EXCLUSIVE,
        handler=lambda _a: {
            "closed": True,
            "reconciliation": {
                "mutation_attempts": 1,
                "mutation_replayed": False,
                "observed_outcome": "applied",
                "retry_safety": "not_needed",
                "recommended_action": "none",
            },
        },
    )

    outcome = runtime.execute("operation", {})

    assert outcome.kind is OperationKind.LIFECYCLE
    assert outcome.attempts == 1
    assert outcome.observed_outcome == "applied"


def test_tool_response_adds_stable_execution_projection(monkeypatch) -> None:
    monkeypatch.setattr(
        server.services.hierarchy,
        "resource",
        lambda object_id, resource_type=None: {
            "id": object_id,
            "resource_type": resource_type,
        },
    )

    result = asyncio.run(get_notebook_metadata("notebook-id"))

    assert result["ok"] is True
    assert result["result"]["item"]["id"] == "notebook-id"
    assert result["execution"]["operation"] == "get_notebook_metadata"
    assert result["execution"]["stage"] == "finalize"
    assert result["execution"]["kind"] == "read"
    assert result["execution"]["backend_category"] == "onenote_com"
    assert result["execution"]["content_exposed"] is False


def test_tool_adapters_have_no_service_bridge_or_boolean_mutation_bypass() -> None:
    tools_root = Path(server.__file__).parent / "tools"
    for path in tools_root.glob("*.py"):
        if path.name == "context.py":
            continue
        source = path.read_text(encoding="utf-8")
        assert "get_services" not in source
        assert "mutation=True" not in source
        assert ".bridge" not in source
