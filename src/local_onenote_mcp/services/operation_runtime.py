"""Transport-independent operation execution control plane.

The runtime owns cross-cutting execution protocol.  Typed OneNote, filesystem,
and UI semantics remain in operation handlers and the existing services.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from enum import StrEnum
import time
from types import MappingProxyType
from typing import Any, Protocol

from .coordination import ReadWriteCoordinator


class OperationKind(StrEnum):
    READ = "read"
    MUTATION = "mutation"
    LIFECYCLE = "lifecycle"
    FILESYSTEM_EFFECT = "filesystem_effect"
    UI_EFFECT = "ui_effect"
    STATIC = "static"


class CoordinationMode(StrEnum):
    NONE = "none"
    SHARED = "shared"
    EXCLUSIVE = "exclusive"


class BackendCategory(StrEnum):
    ONENOTE_COM = "onenote_com"
    FILESYSTEM = "filesystem"
    WINDOWS_UI = "windows_ui"
    PROCESS = "process"


class OperationStage(StrEnum):
    ADMISSION = "admission"
    AUTHORIZATION = "authorization"
    PLATFORM_PREFLIGHT = "platform_preflight"
    COORDINATION = "coordination"
    PREFLIGHT = "preflight"
    EXECUTE = "execute"
    OBSERVE = "observe"
    RECONCILE = "reconcile"
    CONVERGE = "converge"
    POSTCONDITION = "postcondition"
    FINALIZE = "finalize"


@dataclass(frozen=True)
class MutationOperationPolicy:
    """Operation-wide mutation semantics recorded by the canonical registry."""

    attempt_policy_id: str
    replay: str
    identity: str
    observer: str
    partial_boundary: str
    recovery: str
    saga: bool = False


@dataclass(frozen=True)
class OperationSpec:
    name: str
    category: str
    kind: OperationKind
    capability: str
    coordination: CoordinationMode
    backend: BackendCategory
    strategy: str
    handler: str
    budget_policy: str = "bounded_by_backend_timeout"
    cache_policy: str = "live"
    retry_policy: str = "never"
    authorization_policy: str = "none"
    platform_preflight_policy: str = "none"
    audit_policy: str = "content_free"
    exposures: frozenset[str] = frozenset({"default"})
    mutation: MutationOperationPolicy | None = None
    attempt_policy_id: str | None = None

    def __post_init__(self) -> None:
        if (
            not self.name
            or not self.category
            or not self.capability
            or not self.strategy
            or not self.handler
            or not self.authorization_policy
            or not self.platform_preflight_policy
        ):
            raise ValueError("OperationSpec identity fields must be non-empty.")
        if self.kind is OperationKind.MUTATION and self.mutation is None:
            raise ValueError(f"Mutation operation {self.name!r} requires a mutation policy.")
        if self.kind is not OperationKind.MUTATION and self.mutation is not None:
            raise ValueError(f"Non-mutation operation {self.name!r} cannot have mutation policy.")
        if self.mutation is not None and self.attempt_policy_id != self.mutation.attempt_policy_id:
            raise ValueError(
                f"Operation {self.name!r} attempt policy disagrees with its mutation policy."
            )


OperationHandler = Callable[[Mapping[str, Any]], dict[str, Any]]
OperationAuthorizer = Callable[[Mapping[str, Any]], None]
OperationPlatformPreflight = Callable[[Mapping[str, Any]], None]


class ExecutionStrategy(Protocol):
    name: str

    def execute(
        self,
        runtime: "OperationRuntime",
        binding: "OperationBinding",
        execution: "OperationExecution",
        arguments: Mapping[str, Any],
        timeout_seconds: float | None,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class OperationBinding:
    spec: OperationSpec
    strategy: ExecutionStrategy
    handler: OperationHandler
    authorizer: OperationAuthorizer
    platform_preflight: OperationPlatformPreflight


@dataclass
class OperationExecution:
    operation: str
    kind: OperationKind
    backend: BackendCategory
    stage: OperationStage
    started_monotonic: float
    deadline_monotonic: float
    attempts: int = 0
    replayed: bool = False
    backend_calls: int = 0
    completed_steps: list[dict[str, Any]] = field(default_factory=list)
    generation_before: int = 0
    generation_after: int = 0
    observed_outcome: str = "not_observed"
    retry_safety: str = "new_call_required"
    recommended_action: str = "inspect_error_and_retry_only_if_safe"


@dataclass(frozen=True)
class OperationOutcome:
    operation: str
    success: bool
    stage: OperationStage
    kind: OperationKind
    backend: BackendCategory
    data: Mapping[str, Any] | None
    error: Exception | None
    attempts: int
    replayed: bool
    backend_calls: int
    completed_steps: tuple[Mapping[str, Any], ...]
    observed_outcome: str
    retry_safety: str
    recommended_action: str
    generation_before: int
    generation_after: int

    def public_execution(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "stage": self.stage.value,
            "kind": self.kind.value,
            "backend_category": self.backend.value,
            "attempts": self.attempts,
            "replayed": self.replayed,
            "backend_calls": self.backend_calls,
            "completed_steps": [dict(step) for step in self.completed_steps],
            "observed_outcome": self.observed_outcome,
            "retry_safety": self.retry_safety,
            "recommended_action": self.recommended_action,
            "cache_generation": {
                "before": self.generation_before,
                "after": self.generation_after,
            },
            "content_exposed": False,
        }


class OperationRegistry:
    """Unique operation → Spec, Strategy, Handler authority."""

    def __init__(self) -> None:
        self._bindings: dict[str, OperationBinding] = {}

    def register(
        self,
        spec: OperationSpec,
        strategy: ExecutionStrategy,
        handler: OperationHandler,
        authorizer: OperationAuthorizer | None = None,
        platform_preflight: OperationPlatformPreflight | None = None,
    ) -> None:
        if spec.name in self._bindings:
            raise ValueError(f"Duplicate operation registration: {spec.name}")
        if strategy.name != spec.strategy:
            raise ValueError(
                f"Operation {spec.name!r} declares strategy {spec.strategy!r}, "
                f"but received {strategy.name!r}."
            )
        if (
            spec.platform_preflight_policy != "none"
            and platform_preflight is None
        ):
            raise ValueError(
                f"Operation {spec.name!r} declares platform preflight policy "
                f"{spec.platform_preflight_policy!r} but has no preflight binding."
            )
        self._bindings[spec.name] = OperationBinding(
            spec,
            strategy,
            handler,
            authorizer or (lambda _arguments: None),
            platform_preflight or (lambda _arguments: None),
        )

    def resolve(self, operation: str) -> OperationBinding:
        try:
            return self._bindings[operation]
        except KeyError as exc:
            raise KeyError(f"No operation registered for public tool {operation!r}.") from exc

    @property
    def bindings(self) -> Mapping[str, OperationBinding]:
        return MappingProxyType(self._bindings)

    def freeze_order(self, names: tuple[str, ...]) -> None:
        """Freeze the complete registry in one externally reviewed product order."""

        if len(names) != len(set(names)) or set(names) != set(self._bindings):
            raise RuntimeError("Registry order must name every registered operation exactly once.")
        self._bindings = {name: self._bindings[name] for name in names}

    def names_for_profile(self, profile: str) -> frozenset[str]:
        return frozenset(
            name
            for name, binding in self._bindings.items()
            if profile in binding.spec.exposures
        )

    def ordered_names_for_profile(self, profile: str) -> tuple[str, ...]:
        return tuple(
            name
            for name, binding in self._bindings.items()
            if profile in binding.spec.exposures
        )

    def audit_public_tools(self, names: tuple[str, ...], *, profile: str) -> None:
        expected = self.names_for_profile(profile)
        expected_order = self.ordered_names_for_profile(profile)
        actual = frozenset(names)
        missing = sorted(actual - set(self._bindings))
        profile_missing = sorted(actual - expected)
        unregistered_surface = sorted(expected - actual)
        order_mismatch = names != expected_order
        if missing or profile_missing or unregistered_surface or order_mismatch:
            raise RuntimeError(
                "Operation registry/profile audit failed: "
                f"unregistered={missing}, wrong_profile={profile_missing}, "
                f"missing_from_surface={unregistered_surface}, "
                f"order_mismatch={order_mismatch}."
            )
        for name in actual:
            binding = self._bindings[name]
            if (
                not binding.spec.handler
                or binding.handler is None
                or not callable(binding.authorizer)
                or not callable(binding.platform_preflight)
            ):
                raise RuntimeError(f"Operation {name!r} has no registered Handler.")


_CURRENT_EXECUTION: ContextVar[OperationExecution | None] = ContextVar(
    "local_onenote_operation_execution", default=None
)


def record_backend_call(_backend_operation: str) -> None:
    """Count a backend call without retaining its arguments or payload."""

    execution = _CURRENT_EXECUTION.get()
    if execution is not None:
        execution.backend_calls += 1


def _safe_steps(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for step in value[:128]:
        if not isinstance(step, Mapping):
            continue
        projected = {
            key: step[key]
            for key in ("operation", "status", "attempt", "count")
            if key in step and isinstance(step[key], (str, int, bool, type(None)))
        }
        if projected:
            result.append(projected)
    return result


class _BaseStrategy:
    name = "base"
    stages: tuple[OperationStage, ...] = (OperationStage.EXECUTE,)

    def execute(
        self,
        runtime: "OperationRuntime",
        binding: OperationBinding,
        execution: OperationExecution,
        arguments: Mapping[str, Any],
        timeout_seconds: float | None,
    ) -> dict[str, Any]:
        spec = binding.spec
        execution.stage = OperationStage.COORDINATION
        scope = runtime.coordination_scope(spec.coordination, timeout_seconds)
        with scope:
            execution.generation_after = runtime.coordinator.generation
            for stage in self.stages:
                execution.stage = stage
                if stage is OperationStage.EXECUTE:
                    if runtime.clock() >= execution.deadline_monotonic:
                        raise TimeoutError(
                            f"Operation {execution.operation!r} deadline expired before execute."
                        )
                    result = binding.handler(arguments)
            execution.stage = OperationStage.FINALIZE
            runtime.finalizer(execution)
            return result


class ReadExecutionStrategy(_BaseStrategy):
    name = "read"
    stages = (OperationStage.EXECUTE,)


class MutationExecutionStrategy(_BaseStrategy):
    """Operation-wide mutation protocol that composes the 029 attempt primitive."""

    name = "mutation"
    stages = (
        OperationStage.PREFLIGHT,
        OperationStage.EXECUTE,
        OperationStage.OBSERVE,
        OperationStage.RECONCILE,
        OperationStage.CONVERGE,
        OperationStage.POSTCONDITION,
    )


class LifecycleExecutionStrategy(_BaseStrategy):
    name = "lifecycle"
    stages = (OperationStage.PREFLIGHT, OperationStage.EXECUTE, OperationStage.OBSERVE)


class FilesystemEffectExecutionStrategy(_BaseStrategy):
    name = "filesystem_effect"
    stages = (OperationStage.PREFLIGHT, OperationStage.EXECUTE, OperationStage.POSTCONDITION)


class UIEffectExecutionStrategy(_BaseStrategy):
    name = "ui_effect"
    stages = (OperationStage.PREFLIGHT, OperationStage.EXECUTE)


class StaticExecutionStrategy(_BaseStrategy):
    name = "static"
    stages = (OperationStage.EXECUTE,)


STRATEGIES: Mapping[str, ExecutionStrategy] = MappingProxyType(
    {
        strategy.name: strategy
        for strategy in (
            ReadExecutionStrategy(),
            MutationExecutionStrategy(),
            LifecycleExecutionStrategy(),
            FilesystemEffectExecutionStrategy(),
            UIEffectExecutionStrategy(),
            StaticExecutionStrategy(),
        )
    }
)


class _NoCoordination:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_args: Any) -> None:
        return None


class OperationRuntime:
    def __init__(
        self,
        registry: OperationRegistry,
        coordinator: ReadWriteCoordinator,
        *,
        clock: Callable[[], float] = time.monotonic,
        finalizer: Callable[[OperationExecution], None] | None = None,
        audit_limit: int = 256,
    ) -> None:
        self.registry = registry
        self.coordinator = coordinator
        self.clock = clock
        self.finalizer = finalizer or (lambda _execution: None)
        self._audit: deque[dict[str, Any]] = deque(maxlen=audit_limit)

    @property
    def audit_events(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(MappingProxyType(dict(event)) for event in self._audit)

    def coordination_scope(
        self, mode: CoordinationMode, timeout_seconds: float | None
    ) -> Any:
        if mode is CoordinationMode.SHARED:
            return self.coordinator.read(timeout_seconds=timeout_seconds)
        if mode is CoordinationMode.EXCLUSIVE:
            return self.coordinator.mutation(timeout_seconds=timeout_seconds)
        return _NoCoordination()

    def execute(
        self,
        operation: str,
        arguments: Mapping[str, Any],
        *,
        timeout_seconds: float | None = None,
    ) -> OperationOutcome:
        binding = self.registry.resolve(operation)
        spec = binding.spec
        started = self.clock()
        timeout = (
            self.coordinator.default_timeout_seconds
            if timeout_seconds is None
            else float(timeout_seconds)
        )
        execution = OperationExecution(
            operation=operation,
            kind=spec.kind,
            backend=spec.backend,
            stage=OperationStage.ADMISSION,
            started_monotonic=started,
            deadline_monotonic=started + timeout,
            generation_before=self.coordinator.generation,
            generation_after=self.coordinator.generation,
        )
        token: Token[OperationExecution | None] = _CURRENT_EXECUTION.set(execution)
        safe_arguments = MappingProxyType(dict(arguments))
        data: dict[str, Any] | None = None
        error: Exception | None = None
        try:
            execution.stage = OperationStage.AUTHORIZATION
            binding.authorizer(safe_arguments)
            execution.stage = OperationStage.PLATFORM_PREFLIGHT
            binding.platform_preflight(safe_arguments)
            data = binding.strategy.execute(
                self, binding, execution, safe_arguments, timeout_seconds
            )
            self._absorb_result(execution, data)
            execution.stage = OperationStage.FINALIZE
        except Exception as exc:
            error = exc
            self._absorb_error(execution, exc)
        finally:
            execution.generation_after = self.coordinator.generation
            _CURRENT_EXECUTION.reset(token)
        outcome = OperationOutcome(
            operation=operation,
            success=error is None,
            stage=execution.stage,
            kind=spec.kind,
            backend=spec.backend,
            data=MappingProxyType(data) if data is not None else None,
            error=error,
            attempts=execution.attempts,
            replayed=execution.replayed,
            backend_calls=execution.backend_calls,
            completed_steps=tuple(MappingProxyType(step) for step in execution.completed_steps),
            observed_outcome=execution.observed_outcome,
            retry_safety=execution.retry_safety,
            recommended_action=execution.recommended_action,
            generation_before=execution.generation_before,
            generation_after=execution.generation_after,
        )
        self._audit.append(outcome.public_execution())
        return outcome

    @staticmethod
    def _absorb_result(execution: OperationExecution, result: Mapping[str, Any]) -> None:
        reconciliation = result.get("reconciliation")
        attempt = reconciliation if isinstance(reconciliation, Mapping) else {}
        default_attempts = 1 if execution.kind is OperationKind.MUTATION else 0
        execution.attempts = int(
            result.get(
                "attempts",
                attempt.get(
                    "mutation_attempts",
                    attempt.get(
                        "execute_attempts", execution.attempts or default_attempts
                    ),
                ),
            )
        )
        execution.replayed = bool(
            result.get(
                "replayed",
                attempt.get("mutation_replayed", attempt.get("replayed", False)),
            )
        )
        execution.completed_steps = _safe_steps(result.get("completed_steps"))
        if (
            execution.kind is OperationKind.LIFECYCLE
            and result.get("accepted") is True
            and result.get("completion_observable") is False
        ):
            execution.observed_outcome = "accepted_completion_unobservable"
        elif execution.kind is OperationKind.UI_EFFECT:
            execution.observed_outcome = "action_accepted"
        elif execution.kind is OperationKind.FILESYSTEM_EFFECT:
            execution.observed_outcome = "filesystem_effect_completed"
        else:
            execution.observed_outcome = str(
                result.get(
                    "observed_outcome",
                    result.get(
                        "outcome",
                        attempt.get("observed_outcome", attempt.get("state", "completed")),
                    ),
                )
            )
        execution.retry_safety = str(
            result.get("retry_safety", attempt.get("retry_safety", "not_needed"))
        )
        execution.recommended_action = str(
            result.get(
                "recommended_action", attempt.get("recommended_action", "none")
            )
        )

    @staticmethod
    def _absorb_error(execution: OperationExecution, exc: Exception) -> None:
        details = getattr(exc, "details", {})
        if isinstance(details, Mapping):
            stage = details.get("stage")
            if isinstance(stage, str) and stage in OperationStage._value2member_map_:
                execution.stage = OperationStage(stage)
            execution.attempts = int(details.get("attempts", execution.attempts))
            execution.replayed = bool(details.get("replayed", execution.replayed))
            execution.completed_steps = _safe_steps(details.get("completed_steps"))
            execution.observed_outcome = str(
                details.get("observed_outcome", details.get("reconciliation", "failed"))
            )
            execution.retry_safety = str(
                details.get("retry_safety", details.get("retryability", "unknown"))
            )
            execution.recommended_action = str(
                details.get("recommended_action", details.get("recovery_action", "inspect_error"))
            )
