"""Execution identity carrier for cross-layer correlation without debug coupling."""

from __future__ import annotations

from contextvars import ContextVar, Token

_CORRELATION_ID: ContextVar[str | None] = ContextVar(
    "local_onenote_correlation_id", default=None
)


def current_correlation_id() -> str | None:
    """Return the active Runtime execution correlation ID, if any."""

    return _CORRELATION_ID.get()


def set_correlation_id(correlation_id: str) -> Token[str | None]:
    """Bind a correlation ID for the current execution context."""

    return _CORRELATION_ID.set(correlation_id)


def reset_correlation_id(token: Token[str | None]) -> None:
    """Restore the previous correlation ID binding."""

    _CORRELATION_ID.reset(token)
