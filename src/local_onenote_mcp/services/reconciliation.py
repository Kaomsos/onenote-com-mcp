"""Operation-neutral mutation execution and post-error reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Generic, TypeVar


T = TypeVar("T")
R = TypeVar("R")


class ReconciliationState(str, Enum):
    NOT_APPLIED = "not_applied"
    APPLIED = "applied"
    PARTIALLY_APPLIED = "partially_applied"
    INDETERMINATE = "indeterminate"


@dataclass(frozen=True)
class ReconciliationResult(Generic[T, R]):
    state: ReconciliationState
    value: T | None
    execution_result: R | None
    attempts: int
    error: Exception | None = None
    execution_succeeded: bool = False

    def summary(self) -> dict[str, object]:
        return {
            "state": self.state.value,
            "execute_attempts": self.attempts,
            "had_backend_error": self.error is not None,
            "execution_succeeded": self.execution_succeeded,
        }


def reconcile_mutation(
    *,
    execute: Callable[[], R],
    observe: Callable[[], T],
    is_pre_state: Callable[[T], bool],
    is_post_state: Callable[[T], bool],
    is_partial_state: Callable[[T], bool] | None = None,
    retry_if_unchanged: bool = False,
    retry_allowed: Callable[[Exception], bool] | None = None,
) -> ReconciliationResult[T, R]:
    """Execute once and classify exact live state after any execution error.

    A second execution is possible only for an explicitly allowed operation,
    after an exact pre-state observation. It is always bounded to one retry.
    """

    attempts = 0
    result: R | None = None
    caught: Exception | None = None
    execution_succeeded = False
    for attempt in range(2):
        attempts += 1
        try:
            result = execute()
            execution_succeeded = True
            break
        except Exception as exc:
            caught = exc
            try:
                value = observe()
            except Exception:
                return ReconciliationResult(
                    ReconciliationState.INDETERMINATE, None, result, attempts, exc
                )
            if is_post_state(value):
                return ReconciliationResult(
                    ReconciliationState.APPLIED, value, result, attempts, exc
                )
            if is_partial_state is not None and is_partial_state(value):
                return ReconciliationResult(
                    ReconciliationState.PARTIALLY_APPLIED, value, result, attempts, exc
                )
            unchanged = is_pre_state(value)
            allowed = retry_allowed(exc) if retry_allowed is not None else False
            if attempt == 0 and retry_if_unchanged and unchanged and allowed:
                continue
            state = (
                ReconciliationState.NOT_APPLIED
                if unchanged
                else ReconciliationState.INDETERMINATE
            )
            return ReconciliationResult(state, value, result, attempts, exc)

    try:
        value = observe()
    except Exception as exc:
        return ReconciliationResult(
            ReconciliationState.INDETERMINATE,
            None,
            result,
            attempts,
            caught or exc,
            execution_succeeded,
        )
    if is_post_state(value):
        return ReconciliationResult(
            ReconciliationState.APPLIED,
            value,
            result,
            attempts,
            caught,
            execution_succeeded,
        )
    if is_partial_state is not None and is_partial_state(value):
        return ReconciliationResult(
            ReconciliationState.PARTIALLY_APPLIED,
            value,
            result,
            attempts,
            caught,
            execution_succeeded,
        )
    if is_pre_state(value):
        return ReconciliationResult(
            ReconciliationState.NOT_APPLIED,
            value,
            result,
            attempts,
            caught,
            execution_succeeded,
        )
    return ReconciliationResult(
        ReconciliationState.INDETERMINATE,
        value,
        result,
        attempts,
        caught,
        execution_succeeded,
    )
