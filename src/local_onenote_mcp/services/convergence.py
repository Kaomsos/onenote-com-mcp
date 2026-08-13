"""Deadline-based, operation-neutral OneNote read-after-write convergence."""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any, Callable, Generic, TypeVar

T = TypeVar("T")
Identity = str | int | float | bool | None | tuple[Any, ...]


@dataclass(frozen=True)
class ConvergenceConfig:
    deadline_seconds: float = 4.0
    interval_seconds: float = 0.5
    required_stable_observations: int = 2
    max_observations: int = 16

    def __post_init__(self) -> None:
        if self.deadline_seconds <= 0:
            raise ValueError("Convergence deadline must be positive.")
        if self.interval_seconds < 0:
            raise ValueError("Convergence interval cannot be negative.")
        if self.required_stable_observations < 1:
            raise ValueError("Convergence requires at least one stable observation.")
        if self.max_observations < self.required_stable_observations:
            raise ValueError("max_observations cannot be smaller than the stability requirement.")


DEFAULT_CONVERGENCE = ConvergenceConfig()


@dataclass(frozen=True)
class ConvergenceResult(Generic[T]):
    converged: bool
    value: T | None
    attempts: int
    elapsed_seconds: float
    stable_observations: int
    observation_history: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    transient_errors: tuple[str, ...] = field(default_factory=tuple)
    identity_remap: dict[str, str] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {
            "converged": self.converged,
            "attempts": self.attempts,
            "elapsed_seconds": round(self.elapsed_seconds, 6),
            "stable_observations": self.stable_observations,
            "identity_remap": dict(self.identity_remap),
            "transient_errors": list(self.transient_errors),
        }


def converge(
    observe: Callable[[], T],
    accept: Callable[[T], bool],
    project_identity: Callable[[T], Identity],
    *,
    config: ConvergenceConfig = DEFAULT_CONVERGENCE,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    identity_remap: dict[str, str] | None = None,
    transient: Callable[[Exception], bool] | None = None,
) -> ConvergenceResult[T]:
    """Observe until the same accepted identity is seen consecutively.

    History deliberately records only booleans, stable counts, and typed error
    codes. The observed OneNote value (which may contain paths or content) is
    returned to the in-process caller but never copied into timing evidence.
    """

    started = clock()
    deadline = started + config.deadline_seconds
    last_value: T | None = None
    last_identity: Identity | object = object()
    stable = 0
    attempts = 0
    history: list[dict[str, Any]] = []
    transient_errors: list[str] = []

    while attempts < config.max_observations:
        attempts += 1
        try:
            value = observe()
            last_value = value
            accepted = bool(accept(value))
            identity = project_identity(value) if accepted else None
            if accepted and identity == last_identity:
                stable += 1
            elif accepted:
                stable = 1
                last_identity = identity
            else:
                stable = 0
                last_identity = object()
            history.append({"attempt": attempts, "accepted": accepted, "stable": stable})
            if stable >= config.required_stable_observations:
                return ConvergenceResult(
                    True,
                    value,
                    attempts,
                    max(0.0, clock() - started),
                    stable,
                    tuple(history),
                    tuple(transient_errors),
                    dict(identity_remap or {}),
                )
        except Exception as exc:
            allowed = transient(exc) if transient is not None else False
            if not allowed:
                raise
            code = getattr(exc, "code", type(exc).__name__)
            transient_errors.append(str(code))
            stable = 0
            last_identity = object()
            history.append({"attempt": attempts, "accepted": False, "stable": 0, "error_type": str(code)})

        now = clock()
        if now >= deadline or attempts >= config.max_observations:
            break
        sleeper(min(config.interval_seconds, max(0.0, deadline - now)))

    return ConvergenceResult(
        False,
        last_value,
        attempts,
        max(0.0, clock() - started),
        stable,
        tuple(history),
        tuple(transient_errors),
        dict(identity_remap or {}),
    )
