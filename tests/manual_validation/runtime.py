"""Shared runtime types and exit codes for manual validation commands."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .progress import RunProgressReporter


EXIT_ARGUMENT = 2
EXIT_MCP = 3
EXIT_RESTORE = 4
EXIT_INVARIANT = 5


class RunnerFailure(RuntimeError):
    def __init__(self, message: str, exit_code: int = EXIT_ARGUMENT) -> None:
        super().__init__(message)
        self.exit_code = exit_code

    def as_error_dict(self) -> dict[str, Any]:
        return {"ok": False, "error": str(self), "exit_code": self.exit_code}

    def terminal_lines(self) -> tuple[str, ...]:
        return (f"ERROR: {self}",)


class PathBudgetFailure(RunnerFailure):
    """Stable fail-closed error for a managed Windows path budget violation."""

    def __init__(
        self,
        *,
        phase: str,
        target_kind: str,
        path: Path,
        actual_utf16: int,
        limit_utf16: int,
        relative_path: str | None,
        remediation: Mapping[str, str],
        filesystem_changes_started: bool = False,
        cache_entry_published: bool = False,
        onenote_opened: bool = False,
        mutation_started: bool = False,
        failure_evidence_written: bool = False,
    ) -> None:
        super().__init__("Fixture cache path budget exceeded.", EXIT_INVARIANT)
        self.phase = phase
        self.target_kind = target_kind
        self.path = Path(path)
        self.actual_utf16 = actual_utf16
        self.limit_utf16 = limit_utf16
        self.relative_path = relative_path
        self.remediation = dict(remediation)
        self.filesystem_changes_started = filesystem_changes_started
        self.cache_entry_published = cache_entry_published
        self.onenote_opened = onenote_opened
        self.mutation_started = mutation_started
        self.failure_evidence_written = failure_evidence_written

    @property
    def over_by_utf16(self) -> int:
        return max(0, self.actual_utf16 - self.limit_utf16)

    def with_failure_evidence(self, written: bool) -> "PathBudgetFailure":
        self.failure_evidence_written = bool(written)
        return self

    def as_error_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": str(self),
            "error_type": "path_budget_exceeded",
            "exit_code": self.exit_code,
            "phase": self.phase,
            "target_kind": self.target_kind,
            "limit_utf16": self.limit_utf16,
            "actual_utf16": self.actual_utf16,
            "over_by_utf16": self.over_by_utf16,
            "path": str(self.path),
            "relative_path": self.relative_path,
            "filesystem_changes_started": self.filesystem_changes_started,
            "cache_entry_published": self.cache_entry_published,
            "onenote_opened": self.onenote_opened,
            "mutation_started": self.mutation_started,
            "failure_evidence_written": self.failure_evidence_written,
            "remediation": dict(self.remediation),
        }

    def terminal_lines(self) -> tuple[str, ...]:
        trigger_label = "Relative path" if self.relative_path is not None else "Path"
        trigger = self.relative_path if self.relative_path is not None else str(self.path)
        side_effects = (
            "No staging directory or cache entry was created; OneNote and scenario "
            "mutation were not started."
            if not any(
                (
                    self.filesystem_changes_started,
                    self.cache_entry_published,
                    self.onenote_opened,
                    self.mutation_started,
                )
            )
            else (
                "Side effects: filesystem_changes_started="
                f"{str(self.filesystem_changes_started).lower()}, cache_entry_published="
                f"{str(self.cache_entry_published).lower()}, onenote_opened="
                f"{str(self.onenote_opened).lower()}, mutation_started="
                f"{str(self.mutation_started).lower()}."
            )
        )
        return (
            "ERROR: Fixture cache path budget exceeded.",
            f"Phase: {self.phase}",
            f"Target: {self.target_kind}",
            (
                f"Limit: {self.limit_utf16} UTF-16 units; actual: {self.actual_utf16}; "
                f"exceeded by: {self.over_by_utf16}"
            ),
            f"{trigger_label}: {trigger}",
            side_effects,
            f"How to fix: {self.remediation['message']}",
        )


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
    verbosity: str = "normal"
    progress: RunProgressReporter = field(
        default_factory=RunProgressReporter.disabled,
        compare=False,
        repr=False,
    )


__all__ = [
    "EXIT_ARGUMENT",
    "EXIT_INVARIANT",
    "EXIT_MCP",
    "EXIT_RESTORE",
    "InvariantFailure",
    "PathBudgetFailure",
    "RestoreFailure",
    "RunnerFailure",
    "RuntimeOptions",
]
