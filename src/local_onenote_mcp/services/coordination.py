"""Process-local reader/writer coordination for complete MCP tool calls."""

from __future__ import annotations

from contextlib import contextmanager
import threading
import time
from collections.abc import Iterator
from typing import Callable

from ..onenote_errors import OneNoteCoordinationTimeoutError


class ReadWriteCoordinator:
    """Writer-preferring process-local coordinator with bounded acquisition."""

    def __init__(
        self,
        *,
        default_timeout_seconds: float = 90.0,
        mutation_invalidator: Callable[[int], None] | None = None,
    ) -> None:
        if default_timeout_seconds <= 0:
            raise ValueError("Coordinator timeout must be positive.")
        self.default_timeout_seconds = float(default_timeout_seconds)
        self._condition = threading.Condition(threading.Lock())
        self._readers = 0
        self._writer = False
        self._waiting_writers = 0
        self._generation = 0
        self._mutation_invalidator = mutation_invalidator

    @property
    def generation(self) -> int:
        with self._condition:
            return self._generation

    def configure_mutation_invalidator(
        self, invalidator: Callable[[int], None] | None
    ) -> None:
        """Attach the TODO-024 cache invalidator without coupling to a cache type."""

        with self._condition:
            if self._writer or self._readers:
                raise RuntimeError("Cannot replace the cache invalidator while calls are active.")
            self._mutation_invalidator = invalidator

    def _deadline(self, timeout_seconds: float | None) -> float:
        timeout = self.default_timeout_seconds if timeout_seconds is None else float(timeout_seconds)
        if timeout <= 0:
            raise ValueError("Coordinator timeout must be positive.")
        return time.monotonic() + timeout

    def _wait(self, predicate: Callable[[], bool], deadline: float, mode: str) -> None:
        while not predicate():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise OneNoteCoordinationTimeoutError(
                    f"Timed out waiting for the process-local OneNote {mode} coordinator.",
                    operation=f"coordinate_{mode}",
                    reconciliation="not_applied",
                )
            self._condition.wait(remaining)

    @contextmanager
    def read(self, *, timeout_seconds: float | None = None) -> Iterator[None]:
        deadline = self._deadline(timeout_seconds)
        with self._condition:
            self._wait(lambda: not self._writer and self._waiting_writers == 0, deadline, "read")
            self._readers += 1
        try:
            yield
        finally:
            with self._condition:
                self._readers -= 1
                self._condition.notify_all()

    @contextmanager
    def mutation(self, *, timeout_seconds: float | None = None) -> Iterator[None]:
        deadline = self._deadline(timeout_seconds)
        acquired = False
        with self._condition:
            self._waiting_writers += 1
            try:
                self._wait(lambda: not self._writer and self._readers == 0, deadline, "mutation")
                self._writer = True
                acquired = True
            finally:
                self._waiting_writers -= 1
                if not acquired:
                    self._condition.notify_all()
        try:
            with self._condition:
                self._generation += 1
                generation = self._generation
            if self._mutation_invalidator is not None:
                self._mutation_invalidator(generation)
            yield
        finally:
            with self._condition:
                self._writer = False
                self._condition.notify_all()
