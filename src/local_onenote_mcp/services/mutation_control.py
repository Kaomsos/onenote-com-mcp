"""Explicit policies and outcomes for one bounded mutation attempt.

This module governs the principal execute/reconcile attempt only.  An enclosing
operation may still have operation-specific steps before or after that attempt
(for example root-only Page Reparent descendant promotion).  Operation-wide
admission, coordination, saga state, and backend-call accounting belong to the
future Operation Runtime rather than this module.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Generic, TypeVar

from ..onenote_errors import (
    OneNoteFileUnavailableError,
    OneNoteModalUIBlockedError,
    OneNoteNotYetSynchronizedError,
    OneNoteObjectUnavailableError,
    OneNoteOperationTimeoutError,
    idempotent_retry_allowed,
)
from .reconciliation import (
    ReconciliationResult,
    ReconciliationState,
    reconcile_mutation,
)


T = TypeVar("T")
R = TypeVar("R")


class MutationReplayPolicy(str, Enum):
    """Whether one unchanged-prestate replay is part of an attempt policy."""

    NEVER = "never"
    EXACT_PRESTATE_TYPED_TRANSIENT = "exact_prestate_typed_transient"


class MutationIdentityPolicy(str, Enum):
    """Identity behavior permitted by a bounded mutation attempt."""

    PRESERVED = "preserved"
    PAGE_AND_CONTENT_REMAP = "page_and_content_remap"
    CONTENT_OBJECTS_MAY_CHANGE = "content_objects_may_change"
    TARGET_REMOVED = "target_removed"


@dataclass(frozen=True)
class MutationAttemptPolicy:
    """Static, content-free policy for one bounded mutation attempt."""

    policy_id: str
    replay_policy: MutationReplayPolicy
    identity_policy: MutationIdentityPolicy
    observer_description: str
    partial_boundary_description: str
    forbidden_backend_operations: tuple[str, ...] = ()
    persistence_checkpoint: str = "not_observable"
    allow_execute_error_reconciled_success: bool = True

    @property
    def max_execute_attempts(self) -> int:
        return (
            2
            if self.replay_policy
            is MutationReplayPolicy.EXACT_PRESTATE_TYPED_TRANSIENT
            else 1
        )


@dataclass(frozen=True)
class RecoveryDecision:
    """Stable caller action derived from typed evidence, never error text."""

    retry_safety: str
    recommended_action: str
    manual_recovery_required: bool


@dataclass(frozen=True)
class MutationAttemptOutcome(Generic[T, R]):
    """One bounded mutation attempt reconciled against live state."""

    policy: MutationAttemptPolicy
    reconciliation: ReconciliationResult[T, R]
    recovery: RecoveryDecision
    observation_attempts: int

    @property
    def state(self) -> ReconciliationState:
        return self.reconciliation.state

    @property
    def applied(self) -> bool:
        return self.state is ReconciliationState.APPLIED

    def summary(self) -> dict[str, object]:
        result = self.reconciliation
        execute_error_reconciled = (
            result.error is not None
            and self.applied
            and not result.execution_succeeded
        )
        return {
            "state": self.state.value,
            "execute_attempts": result.attempts,
            "had_backend_error": result.error is not None,
            "execution_succeeded": result.execution_succeeded,
            "mutation_stage": "postcondition" if self.applied else "reconciliation",
            "preflight_state": "logical_ready",
            "persistence_checkpoint": self.policy.persistence_checkpoint,
            "mutation_attempted": result.attempts > 0,
            "mutation_attempts": result.attempts,
            "mutation_replayed": result.attempts > 1,
            "observed_outcome": self.state.value,
            "execute_error_reconciled": execute_error_reconciled,
            "retry_safety": self.recovery.retry_safety,
            "recommended_action": self.recovery.recommended_action,
            "manual_recovery_required": self.recovery.manual_recovery_required,
            "observation_attempts": self.observation_attempts,
            "identity_policy": self.policy.identity_policy.value,
        }

    def failure_details(self) -> dict[str, object]:
        """Return the stable, content-free fields required on every failure."""

        summary = self.summary()
        return {
            "mutation_stage": summary["mutation_stage"],
            "mutation_attempted": summary["mutation_attempted"],
            "mutation_attempts": summary["mutation_attempts"],
            "mutation_replayed": summary["mutation_replayed"],
            "observed_outcome": summary["observed_outcome"],
            "preflight_state": summary["preflight_state"],
            "persistence_checkpoint": summary["persistence_checkpoint"],
            "retry_safety": summary["retry_safety"],
            "recommended_action": summary["recommended_action"],
            "manual_recovery_required": summary["manual_recovery_required"],
            "observation_attempts": summary["observation_attempts"],
        }


def _recovery_decision(
    state: ReconciliationState, error: Exception | None
) -> RecoveryDecision:
    if state is ReconciliationState.APPLIED:
        return RecoveryDecision("not_needed", "none", False)
    if state is ReconciliationState.PARTIALLY_APPLIED:
        return RecoveryDecision(
            "do_not_replay",
            "query_current_ids_and_locations_then_recover_manually",
            True,
        )
    if state is ReconciliationState.INDETERMINATE:
        return RecoveryDecision(
            "do_not_replay",
            "query_current_state_with_read_only_tools_before_recovery",
            True,
        )
    if isinstance(error, OneNoteModalUIBlockedError):
        return RecoveryDecision(
            "new_call_after_user_action",
            "close_blocking_onenote_dialog_then_submit_a_new_call",
            False,
        )
    if isinstance(
        error, (OneNoteNotYetSynchronizedError, OneNoteFileUnavailableError)
    ):
        return RecoveryDecision(
            "new_call_after_user_action",
            "close_and_reopen_the_notebook_in_onenote_then_submit_a_new_call",
            False,
        )
    if isinstance(error, OneNoteObjectUnavailableError):
        return RecoveryDecision(
            "new_call_after_read_only_refresh",
            "query_the_current_object_id_and_location_then_submit_a_new_call",
            False,
        )
    if isinstance(error, OneNoteOperationTimeoutError):
        return RecoveryDecision(
            "new_call_after_read_only_confirmation",
            "refresh_confirmation_fields_then_submit_a_new_call",
            False,
        )
    return RecoveryDecision(
        "do_not_replay",
        "inspect_the_typed_error_and_current_state_before_any_new_call",
        True,
    )


class MutationAttemptExecutor:
    """Apply one explicit policy to bounded execute/reconcile behavior."""

    def execute(
        self,
        policy: MutationAttemptPolicy,
        *,
        execute: Callable[[], R],
        observe: Callable[[], T],
        is_pre_state: Callable[[T], bool],
        is_post_state: Callable[[T], bool],
        is_partial_state: Callable[[T], bool] | None = None,
        retry_observation_if: Callable[[Exception], bool] | None = None,
        observation_retry: Callable[[], None] | None = None,
    ) -> MutationAttemptOutcome[T, R]:
        observation_attempts = 0
        last_execute_failed = False

        def bounded_execute() -> R:
            nonlocal last_execute_failed
            last_execute_failed = False
            try:
                return execute()
            except Exception:
                last_execute_failed = True
                raise

        def bounded_observe() -> T:
            nonlocal observation_attempts
            for attempt in range(2):
                observation_attempts += 1
                try:
                    return observe()
                except Exception as exc:
                    allowed = (
                        attempt == 0
                        and retry_observation_if is not None
                        and retry_observation_if(exc)
                    )
                    if not allowed:
                        raise
                    if observation_retry is not None:
                        observation_retry()
            raise AssertionError("bounded observation loop did not return")

        replay_allowed = (
            policy.replay_policy
            is MutationReplayPolicy.EXACT_PRESTATE_TYPED_TRANSIENT
        )
        result = reconcile_mutation(
            execute=bounded_execute,
            observe=bounded_observe,
            is_pre_state=is_pre_state,
            is_post_state=lambda value: is_post_state(value)
            and (
                not last_execute_failed
                or policy.allow_execute_error_reconciled_success
            ),
            is_partial_state=is_partial_state,
            retry_if_unchanged=replay_allowed,
            retry_allowed=idempotent_retry_allowed if replay_allowed else None,
        )
        if result.attempts > policy.max_execute_attempts:
            raise AssertionError(
                f"{policy.policy_id} exceeded its declared execute-attempt policy."
            )
        return MutationAttemptOutcome(
            policy=policy,
            reconciliation=result,
            recovery=_recovery_decision(result.state, result.error),
            observation_attempts=observation_attempts,
        )

    def reconcile_observation(
        self,
        policy: MutationAttemptPolicy,
        *,
        observation: T | None,
        is_pre_state: Callable[[T], bool],
        is_post_state: Callable[[T], bool],
        is_partial_state: Callable[[T], bool] | None = None,
        execution_result: R | None = None,
        execution_error: Exception | None = None,
        execution_succeeded: bool,
        observation_error: Exception | None = None,
        observation_attempts: int = 1,
    ) -> MutationAttemptOutcome[T, R]:
        """Classify a previously executed operation after its shared observer ran.

        Reparent uses this form so success and exception paths share its existing
        bounded hierarchy convergence and full-evidence bookend without adding
        another expensive Page-content capture to the normal success path.
        """

        if policy.max_execute_attempts != 1:
            raise ValueError(
                "Deferred observation is only valid for execute-once mutation attempt policies."
            )
        error = execution_error or observation_error
        if observation is None or observation_error is not None:
            state = ReconciliationState.INDETERMINATE
            value = None
        elif is_post_state(observation) and (
            execution_error is None
            or policy.allow_execute_error_reconciled_success
        ):
            state = ReconciliationState.APPLIED
            value = observation
        elif is_partial_state is not None and is_partial_state(observation):
            state = ReconciliationState.PARTIALLY_APPLIED
            value = observation
        elif is_pre_state(observation):
            state = ReconciliationState.NOT_APPLIED
            value = observation
        else:
            state = ReconciliationState.INDETERMINATE
            value = observation
        result = ReconciliationResult(
            state=state,
            value=value,
            execution_result=execution_result,
            attempts=1,
            error=error,
            execution_succeeded=execution_succeeded,
        )
        return MutationAttemptOutcome(
            policy=policy,
            reconciliation=result,
            recovery=_recovery_decision(state, error),
            observation_attempts=observation_attempts,
        )


def _policy(
    policy_id: str,
    *,
    replay: MutationReplayPolicy,
    identity: MutationIdentityPolicy,
    observer_description: str,
    partial_boundary_description: str,
    forbidden: tuple[str, ...] = (),
    allow_execute_error_reconciled_success: bool = True,
) -> MutationAttemptPolicy:
    return MutationAttemptPolicy(
        policy_id=policy_id,
        replay_policy=replay,
        identity_policy=identity,
        observer_description=observer_description,
        partial_boundary_description=partial_boundary_description,
        forbidden_backend_operations=forbidden,
        allow_execute_error_reconciled_success=allow_execute_error_reconciled_success,
    )


_NEVER = MutationReplayPolicy.NEVER
_PRESERVED = MutationIdentityPolicy.PRESERVED


MUTATION_ATTEMPT_POLICIES = MappingProxyType(
    {
        "update_page_title": _policy(
            "update_page_title",
            replay=_NEVER,
            identity=_PRESERVED,
            observer_description="typed_page_title",
            partial_boundary_description="target identity missing or non-title protected state changed",
        ),
        "rename_resource": _policy(
            "rename_resource",
            replay=_NEVER,
            identity=_PRESERVED,
            observer_description="typed_resource_name_and_parent",
            partial_boundary_description="target identity missing or protected parent changed",
        ),
        "reorder_page": _policy(
            "reorder_page",
            replay=_NEVER,
            identity=_PRESERVED,
            observer_description="section_page_order",
            partial_boundary_description="sibling identity set changed",
        ),
        "reorder_section": _policy(
            "reorder_section",
            replay=_NEVER,
            identity=_PRESERVED,
            observer_description="container_child_order_and_subtree",
            partial_boundary_description="direct-child identity set changed",
        ),
        "append_to_page": _policy(
            "append_to_page",
            replay=_NEVER,
            identity=MutationIdentityPolicy.CONTENT_OBJECTS_MAY_CHANGE,
            observer_description="page_content_digest",
            partial_boundary_description="Page identity unavailable after content mutation",
            allow_execute_error_reconciled_success=False,
        ),
        "add_image_to_page": _policy(
            "add_image_to_page",
            replay=_NEVER,
            identity=MutationIdentityPolicy.CONTENT_OBJECTS_MAY_CHANGE,
            observer_description="page_content_digest",
            partial_boundary_description="Page identity unavailable after content mutation",
            allow_execute_error_reconciled_success=False,
        ),
        "delete_page_content": _policy(
            "delete_page_content",
            replay=_NEVER,
            identity=MutationIdentityPolicy.TARGET_REMOVED,
            observer_description="page_content_object_ids",
            partial_boundary_description="Page content identity set changed without removing the target",
        ),
        "delete_hierarchy": _policy(
            "delete_hierarchy",
            replay=_NEVER,
            identity=MutationIdentityPolicy.TARGET_REMOVED,
            observer_description="typed_resource_activity",
            partial_boundary_description="permanent delete reached only recycle-bin state",
        ),
        "close_notebook": _policy(
            "close_notebook",
            replay=_NEVER,
            identity=MutationIdentityPolicy.TARGET_REMOVED,
            observer_description="notebook_open_state",
            partial_boundary_description="Notebook open state cannot be read conclusively",
        ),
        "reparent_page": _policy(
            "reparent_page",
            replay=_NEVER,
            identity=MutationIdentityPolicy.PAGE_AND_CONTENT_REMAP,
            observer_description="reparent_full_snapshot",
            partial_boundary_description="any observed topology, identity, content, or promotion change",
            forbidden=(
                "sync_hierarchy",
                "close_notebook",
                "open_hierarchy",
                "filesystem_readiness_probe",
            ),
        ),
        "reparent_section": _policy(
            "reparent_section",
            replay=_NEVER,
            identity=_PRESERVED,
            observer_description="reparent_full_snapshot",
            partial_boundary_description="any observed topology, identity, or content change",
            forbidden=(
                "sync_hierarchy",
                "close_notebook",
                "open_hierarchy",
                "filesystem_readiness_probe",
            ),
        ),
        "reparent_section_group": _policy(
            "reparent_section_group",
            replay=_NEVER,
            identity=_PRESERVED,
            observer_description="reparent_full_snapshot",
            partial_boundary_description="any observed topology, identity, or content change",
            forbidden=(
                "sync_hierarchy",
                "close_notebook",
                "open_hierarchy",
                "filesystem_readiness_probe",
            ),
        ),
    }
)


# Temporary executable inventory for the TODO 029 -> TODO 036 handoff.  Keys
# are public operation/tool IDs; values are attempt-policy IDs.  The future
# OperationRegistry will own this mapping together with kind, handler,
# coordination, capability, backend, budget, cache, and audit policy.
MUTATION_ATTEMPT_POLICY_BINDINGS = MappingProxyType(
    {
        "update_page_title": "update_page_title",
        "rename_section": "rename_resource",
        "rename_section_group": "rename_resource",
        "reorder_page": "reorder_page",
        "reorder_section": "reorder_section",
        "append_to_page": "append_to_page",
        "add_image_to_page": "add_image_to_page",
        "delete_page_content": "delete_page_content",
        "delete_page": "delete_hierarchy",
        "delete_section": "delete_hierarchy",
        "delete_section_group": "delete_hierarchy",
        "close_notebook": "close_notebook",
        "reparent_page": "reparent_page",
        "reparent_section": "reparent_section",
        "reparent_section_group": "reparent_section_group",
    }
)


def mutation_attempt_policy(policy_id: str) -> MutationAttemptPolicy:
    try:
        return MUTATION_ATTEMPT_POLICIES[policy_id]
    except KeyError as exc:
        raise KeyError(f"No mutation attempt policy registered for {policy_id!r}.") from exc
