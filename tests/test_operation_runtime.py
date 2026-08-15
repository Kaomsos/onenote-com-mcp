from __future__ import annotations

import ast
import asyncio
from collections.abc import Mapping
from pathlib import Path

import pytest

from local_onenote_mcp import server
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
)
from local_onenote_mcp.tools import DEFAULT_TOOLS
from local_onenote_mcp.tools.context import get_runtime
from local_onenote_mcp.tools.hierarchy import get_notebook


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
    finalizer=None,
    clock=None,
) -> OperationRuntime:
    registry = OperationRegistry()
    policy = mutation_policy(name) if kind is OperationKind.MUTATION else None
    spec = OperationSpec(
        name=name,
        kind=kind,
        capability=name,
        coordination=coordination,
        backend=backend,
        strategy=kind.value,
        handler=f"tests.{name}",
        mutation=policy,
        attempt_policy_id=policy.attempt_policy_id if policy else None,
    )
    registry.register(spec, STRATEGIES[spec.strategy], handler, authorizer)
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


def test_registry_is_the_unique_default_and_advanced_tool_inventory() -> None:
    registry = get_runtime().registry
    default_names = {tool.__name__ for tool in DEFAULT_TOOLS}
    advanced_names = set(registry.names_for_profile("advanced"))

    assert registry.names_for_profile("default") == default_names
    assert registry.names_for_profile("advanced") == advanced_names
    assert advanced_names == set()
    assert len(registry.bindings) == len(default_names) == 56
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
        assert callable(binding.authorizer)
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

    assert non_read - covered == set()


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

    assert bindings["get_notebook"].spec.kind is OperationKind.READ
    assert bindings["get_notebook"].spec.coordination is CoordinationMode.SHARED
    assert bindings["rename_section"].spec.kind is OperationKind.MUTATION
    assert bindings["rename_section"].spec.coordination is CoordinationMode.EXCLUSIVE
    assert bindings["sync_notebook"].spec.kind is OperationKind.LIFECYCLE
    assert bindings["publish_object"].spec.kind is OperationKind.FILESYSTEM_EFFECT
    assert bindings["publish_object"].spec.backend is BackendCategory.FILESYSTEM
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
    assert OperationStage.RECONCILE not in bindings["get_notebook"].strategy.stages
    assert OperationStage.RECONCILE not in bindings["sync_notebook"].strategy.stages
    assert OperationStage.RECONCILE not in bindings["publish_object"].strategy.stages
    assert OperationStage.RECONCILE not in bindings["navigate_to"].strategy.stages


def test_registry_authorization_catalog_is_explicit_for_risk_classes() -> None:
    bindings = get_runtime().registry.bindings

    assert bindings["create_page"].spec.authorization_policy == "write"
    assert bindings["delete_page"].spec.authorization_policy == "delete"
    assert bindings["replace_page_body"].spec.authorization_policy == "write_delete"
    assert (
        bindings["reparent_page"].spec.authorization_policy
        == "experimental_reparent"
    )
    assert bindings["copy_page"].spec.authorization_policy == "experimental_copy"
    assert bindings["move_page"].spec.authorization_policy == "move_page"
    assert bindings["move_section"].spec.authorization_policy == "move_containers"
    assert bindings["close_notebook"].spec.authorization_policy == "write"
    assert bindings["sync_notebook"].spec.authorization_policy == "none"


def test_production_authorization_rejects_before_coordination_or_argument_access(
    monkeypatch,
) -> None:
    monkeypatch.delenv("LOCAL_ONENOTE_ENABLE_WRITES", raising=False)
    monkeypatch.delenv("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_COPY", raising=False)
    runtime = get_runtime()
    generation = runtime.coordinator.generation

    outcome = runtime.execute("copy_page", {})

    assert outcome.success is False
    assert isinstance(outcome.error, PermissionError)
    assert outcome.stage is OperationStage.AUTHORIZATION
    assert outcome.backend_calls == 0
    assert outcome.generation_before == outcome.generation_after == generation


def test_registry_rejects_duplicate_operations_and_incomplete_profile() -> None:
    registry = OperationRegistry()
    spec = OperationSpec(
        name="read",
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
        registry.audit_public_tools({"missing"}, profile="default")
    except RuntimeError as exc:
        assert "unregistered" in str(exc)
    else:
        raise AssertionError("Unregistered public tools must fail startup audit.")


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

    result = asyncio.run(get_notebook("notebook-id"))

    assert result["ok"] is True
    assert result["item"]["id"] == "notebook-id"
    assert result["execution"]["operation"] == "get_notebook"
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
