"""Shared runtime types and exit codes for manual validation commands."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


EXIT_ARGUMENT = 2
EXIT_MCP = 3
EXIT_RESTORE = 4
EXIT_INVARIANT = 5


class RunnerFailure(RuntimeError):
    def __init__(self, message: str, exit_code: int = EXIT_ARGUMENT) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class InvariantFailure(RunnerFailure):
    def __init__(self, message: str) -> None:
        super().__init__(message, EXIT_INVARIANT)


class RestoreFailure(RunnerFailure):
    def __init__(self, message: str) -> None:
        super().__init__(message, EXIT_RESTORE)


@dataclass(frozen=True)
class RuntimeOptions:
    run_dir: Path
    timeout: int
    json_output: bool
    dry_run: bool
    use_cache: bool = False
    cache_root: Path | None = None


__all__ = [
    "EXIT_ARGUMENT",
    "EXIT_INVARIANT",
    "EXIT_MCP",
    "EXIT_RESTORE",
    "InvariantFailure",
    "RestoreFailure",
    "RunnerFailure",
    "RuntimeOptions",
]
