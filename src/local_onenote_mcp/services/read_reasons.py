"""Content-free read reason allowlist for Copy/Move readback attribution."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Iterator

SOURCE_CONFIRMATION = "source_confirmation"
PLAN_CAPTURE = "plan_capture"
DESTINATION_PRECONDITION = "destination_precondition"
POST_CREATE_CONVERGENCE = "post_create_convergence"
PRE_WRITE_TARGET_OBSERVATION = "pre_write_target_observation"
POST_WRITE_RECONCILIATION = "post_write_reconciliation"
POST_WRITE_CONVERGENCE = "post_write_convergence"
TOPOLOGY_VERIFICATION = "topology_verification"
SOURCE_DRIFT_REVALIDATION = "source_drift_revalidation"
DELETE_CONFIRMATION = "delete_confirmation"
DELETE_CONVERGENCE = "delete_convergence"

READ_REASONS: frozenset[str] = frozenset(
    {
        SOURCE_CONFIRMATION,
        PLAN_CAPTURE,
        DESTINATION_PRECONDITION,
        POST_CREATE_CONVERGENCE,
        PRE_WRITE_TARGET_OBSERVATION,
        POST_WRITE_RECONCILIATION,
        POST_WRITE_CONVERGENCE,
        TOPOLOGY_VERIFICATION,
        SOURCE_DRIFT_REVALIDATION,
        DELETE_CONFIRMATION,
        DELETE_CONVERGENCE,
    }
)

_CURRENT_READ_REASON: ContextVar[str | None] = ContextVar(
    "local_onenote_copy_read_reason",
    default=None,
)
_COPY_MOVE_READ_ATTRIBUTION_ACTIVE: ContextVar[bool] = ContextVar(
    "local_onenote_copy_move_read_attribution_active",
    default=False,
)


def current_read_reason() -> str | None:
    return _CURRENT_READ_REASON.get()


@contextmanager
def copy_move_read_attribution() -> Iterator[None]:
    """Enable Copy/Move-only attribution for shared mutation-service reads."""

    token = _COPY_MOVE_READ_ATTRIBUTION_ACTIVE.set(True)
    try:
        yield
    finally:
        _COPY_MOVE_READ_ATTRIBUTION_ACTIVE.reset(token)


@contextmanager
def read_reason(reason: str) -> Iterator[None]:
    if reason not in READ_REASONS:
        raise ValueError(f"Unsupported read reason: {reason!r}.")
    token: Token = _CURRENT_READ_REASON.set(reason)
    try:
        yield
    finally:
        _CURRENT_READ_REASON.reset(token)


@contextmanager
def copy_move_read_reason(reason: str) -> Iterator[None]:
    """Tag a shared-service read only while an enclosing Copy/Move is active."""

    if not _COPY_MOVE_READ_ATTRIBUTION_ACTIVE.get():
        yield
        return
    with read_reason(reason):
        yield
