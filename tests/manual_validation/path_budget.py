"""Deterministic Windows path budgets for managed manual-validation files."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path, PurePath
import re
from typing import Iterable

from .runtime import PathBudgetFailure, RunnerFailure


MAX_MANAGED_PATH_UNITS = 240
MAX_ONENOTE_OPEN_PATH_UNITS = 147
MAX_COMPONENT_UNITS = 120
MAX_OPAQUE_RELATIVE_UNITS = 96
MAX_OPAQUE_DEPTH = 8
MAX_ROLE_UNITS = 12
MAX_WORKING_NAME_UNITS = 64
MAX_RUN_EVIDENCE_LEAF_UNITS = 64
FINGERPRINT_PATTERN = re.compile(r"[0-9a-f]{64}")
FINGERPRINT_DISK_KEY_PATTERN = re.compile(r"[0-9a-f]{32}")
AUTHORED_INSTANCE_KEY_PATTERN = re.compile(r"[0-9a-f]{1,24}")
ROLE_PATTERN = re.compile(r"[a-z][a-z0-9_-]{0,11}")
PUBLISH_STAGING_PATTERN = re.compile(r"\.s-[0-9a-f]{16}")
MATERIALIZE_STAGING_PATTERN = re.compile(r"\.m-[0-9a-f]{16}")
ONENOTE_OBJECT_ID_PATTERN = re.compile(
    r"\{[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\}"
    r"\{[0-9]+\}\{[0-9a-z]+\}",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class CacheDiskLocation:
    kind: str
    parts: tuple[str, ...]
    logical_instance_id: str
    projection_digest: str | None = None

    @property
    def key(self) -> str:
        return "/".join(self.parts)

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "parts": list(self.parts),
            "key": self.key,
            "logical_instance_id": self.logical_instance_id,
            "projection_digest": self.projection_digest,
        }


@dataclass(frozen=True)
class PathBudgetEvidence:
    limit_utf16: int
    longest_path_utf16: int
    remaining_utf16: int
    kind: str
    path: str
    passed: bool = True

    def as_dict(self) -> dict[str, object]:
        return {
            "limit_utf16": self.limit_utf16,
            "longest_path_utf16": self.longest_path_utf16,
            "remaining_utf16": self.remaining_utf16,
            "kind": self.kind,
            "path": self.path,
            "passed": self.passed,
        }


def windows_path_units(value: str | Path) -> int:
    return len(str(value).encode("utf-16-le")) // 2


def managed_absolute(value: str | Path) -> Path:
    """Normalize lexically without requiring the target to be Win32-openable."""

    return Path(os.path.abspath(os.fspath(value)))


def fingerprint_disk_key(fingerprint: str) -> str:
    if FINGERPRINT_PATTERN.fullmatch(fingerprint) is None:
        raise RunnerFailure("Cache fingerprint must be a canonical SHA-256 digest.")
    return fingerprint[:32]


def programmatic_location(instance_id: str) -> CacheDiskLocation:
    if re.fullmatch(r"programmatic-[0-9a-f]{16}", instance_id) is None:
        raise RunnerFailure("Programmatic template instance ID has an invalid typed format.")
    return CacheDiskLocation("programmatic", ("p",), instance_id)


def authored_location(instance_id: str, *, projection_digest: str | None = None) -> CacheDiskLocation:
    match = re.fullmatch(r"authored-([0-9a-f]{1,24})", instance_id)
    if match is None:
        raise RunnerFailure("User-authored template instance ID has an invalid typed format.")
    if projection_digest is not None and FINGERPRINT_PATTERN.fullmatch(projection_digest) is None:
        raise RunnerFailure("User-authored projection digest must be a canonical SHA-256 digest.")
    if projection_digest is not None and not projection_digest.startswith(match.group(1)):
        raise RunnerFailure(
            "User-authored instance key does not match the full projection digest."
        )
    return CacheDiskLocation(
        "authored",
        ("a", match.group(1)),
        instance_id,
        projection_digest,
    )


def instance_location_from_id(instance_id: str) -> CacheDiskLocation:
    if re.fullmatch(r"programmatic-[0-9a-f]{16}", instance_id):
        return programmatic_location(instance_id)
    return authored_location(instance_id)


def validate_role(role: str) -> str:
    if ROLE_PATTERN.fullmatch(role) is None:
        raise ValueError("Notebook role must match [a-z][a-z0-9_-]{0,11}.")
    return role


def validate_working_name(name: str) -> str:
    if not name or Path(name).name != name or name in {".", ".."}:
        raise RunnerFailure("Working Notebook name must be a Windows-safe leaf name.")
    if windows_path_units(name) > MAX_WORKING_NAME_UNITS:
        _raise_budget(
            Path(name),
            phase="run_identity_preflight",
            target_kind="working_name",
            actual_utf16=windows_path_units(name),
            limit_utf16=MAX_WORKING_NAME_UNITS,
        )
    if any(char in '<>:"/\\|?*' or ord(char) < 32 for char in name):
        raise RunnerFailure("Working Notebook name must be a Windows-safe leaf name.")
    validate_physical_name_has_no_onenote_id(name)
    return name


def validate_onenote_open_path(
    path: str | Path,
    *,
    phase: str = "onenote_open_path_preflight",
) -> Path:
    resolved = managed_absolute(path)
    actual = windows_path_units(resolved)
    if actual > MAX_ONENOTE_OPEN_PATH_UNITS:
        _raise_budget(
            resolved,
            phase=phase,
            target_kind="onenote_open_path",
            actual_utf16=actual,
            limit_utf16=MAX_ONENOTE_OPEN_PATH_UNITS,
        )
    return resolved


def validate_physical_name_has_no_onenote_id(value: str | Path) -> str | Path:
    """Keep COM object IDs as logical metadata, never generated physical names."""

    rendered = str(value)
    if ONENOTE_OBJECT_ID_PATTERN.search(rendered):
        raise RunnerFailure(
            "OneNote object IDs must remain in JSON evidence and must not enter "
            "generated physical file or directory names."
        )
    return value


def validate_run_evidence_leaf(path: Path) -> Path:
    validate_physical_name_has_no_onenote_id(path.name)
    units = windows_path_units(path.name)
    if not path.name or units > MAX_RUN_EVIDENCE_LEAF_UNITS:
        _raise_budget(
            path,
            phase="run_evidence_preflight",
            target_kind="run_evidence_name",
            actual_utf16=units,
            limit_utf16=MAX_RUN_EVIDENCE_LEAF_UNITS,
        )
    return path


def remediation_for(target_kind: str) -> dict[str, str]:
    if target_kind in {"cache_root", "repository_path"} or target_kind.startswith(
        ("cache_", "atomic_cache_")
    ):
        return {
            "code": "shorten_repository_path",
            "message": "Move the repository to a shorter local path, then start a new run.",
        }
    if target_kind == "run_root" or target_kind.startswith(
        ("run_", "working_copy", "materialize_")
    ):
        return {
            "code": "use_shorter_unique_run_dir",
            "message": "Use a new, shorter, empty and unique --run-dir, then start a new run.",
        }
    if target_kind.startswith("opaque_"):
        return {
            "code": "shorten_disposable_fixture_hierarchy",
            "message": (
                "Shorten only this disposable fixture's SectionGroup/Section hierarchy, "
                "then rebuild or bootstrap it."
            ),
        }
    if target_kind == "legacy_schema":
        return {
            "code": "clear_legacy_with_previous_version",
            "message": (
                "Return to the pre-upgrade version and complete its human-gated clear all "
                "workflow before using this runtime."
            ),
        }
    return {
        "code": "fix_typed_path_contract",
        "message": (
            "Correct the typed role, working name, or fixed managed layout in code; do "
            "not edit cache files by hand."
        ),
    }


def _raise_budget(
    path: Path,
    *,
    phase: str,
    target_kind: str,
    actual_utf16: int,
    limit_utf16: int = MAX_MANAGED_PATH_UNITS,
    relative_path: str | None = None,
) -> None:
    raise PathBudgetFailure(
        phase=phase,
        target_kind=target_kind,
        path=path,
        actual_utf16=actual_utf16,
        limit_utf16=limit_utf16,
        relative_path=relative_path,
        remediation=remediation_for(target_kind),
    )


def preflight_path(
    path: Path,
    *,
    phase: str,
    target_kind: str,
    relative_path: str | None = None,
) -> PathBudgetEvidence:
    resolved = managed_absolute(path)
    rendered = str(resolved)
    if rendered.startswith("\\\\?\\"):
        raise RunnerFailure("Extended-length paths are not supported for managed validation data.")
    actual = windows_path_units(resolved)
    if actual > MAX_MANAGED_PATH_UNITS:
        _raise_budget(
            resolved,
            phase=phase,
            target_kind=target_kind,
            actual_utf16=actual,
            relative_path=relative_path,
        )
    for component in resolved.parts:
        component_units = windows_path_units(component)
        if component_units > MAX_COMPONENT_UNITS:
            _raise_budget(
                resolved,
                phase=phase,
                target_kind=("opaque_component" if relative_path else "fixed_component"),
                actual_utf16=component_units,
                limit_utf16=MAX_COMPONENT_UNITS,
                relative_path=relative_path,
            )
    return PathBudgetEvidence(
        MAX_MANAGED_PATH_UNITS,
        actual,
        MAX_MANAGED_PATH_UNITS - actual,
        target_kind,
        str(resolved),
    )


def validate_opaque_relative(relative: str, *, phase: str, target_path: Path) -> None:
    normalized = relative.replace("\\", "/")
    pure = PurePath(normalized)
    parts = pure.parts
    if (
        not normalized
        or pure.is_absolute()
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise RunnerFailure("Opaque Notebook inventory contains an unsafe relative path.")
    if any(windows_path_units(part) > MAX_COMPONENT_UNITS for part in parts):
        _raise_budget(
            target_path,
            phase=phase,
            target_kind="opaque_component",
            actual_utf16=max(windows_path_units(part) for part in parts),
            limit_utf16=MAX_COMPONENT_UNITS,
            relative_path=normalized,
        )
    units = windows_path_units(normalized)
    if units > MAX_OPAQUE_RELATIVE_UNITS:
        _raise_budget(
            target_path,
            phase=phase,
            target_kind="opaque_relative_path",
            actual_utf16=units,
            limit_utf16=MAX_OPAQUE_RELATIVE_UNITS,
            relative_path=normalized,
        )
    if len(parts) > MAX_OPAQUE_DEPTH:
        _raise_budget(
            target_path,
            phase=phase,
            target_kind="opaque_hierarchy_depth",
            actual_utf16=len(parts),
            limit_utf16=MAX_OPAQUE_DEPTH,
            relative_path=normalized,
        )


def preflight_paths(
    paths: Iterable[tuple[Path, str, str | None]],
    *,
    phase: str,
) -> dict[str, object]:
    reports = [
        preflight_path(path, phase=phase, target_kind=kind, relative_path=relative)
        for path, kind, relative in paths
    ]
    longest = max(reports, key=lambda item: item.longest_path_utf16)
    return {
        "limit_utf16": MAX_MANAGED_PATH_UNITS,
        "longest_path_utf16": longest.longest_path_utf16,
        "remaining_utf16": longest.remaining_utf16,
        "kind": longest.kind,
        "path": longest.path,
        "passed": True,
        "checked_path_count": len(reports),
    }


__all__ = [
    "AUTHORED_INSTANCE_KEY_PATTERN",
    "CacheDiskLocation",
    "FINGERPRINT_DISK_KEY_PATTERN",
    "FINGERPRINT_PATTERN",
    "MATERIALIZE_STAGING_PATTERN",
    "MAX_COMPONENT_UNITS",
    "MAX_MANAGED_PATH_UNITS",
    "MAX_OPAQUE_DEPTH",
    "MAX_OPAQUE_RELATIVE_UNITS",
    "MAX_ONENOTE_OPEN_PATH_UNITS",
    "MAX_ROLE_UNITS",
    "MAX_RUN_EVIDENCE_LEAF_UNITS",
    "MAX_WORKING_NAME_UNITS",
    "PUBLISH_STAGING_PATTERN",
    "ONENOTE_OBJECT_ID_PATTERN",
    "PathBudgetEvidence",
    "ROLE_PATTERN",
    "authored_location",
    "fingerprint_disk_key",
    "instance_location_from_id",
    "managed_absolute",
    "preflight_path",
    "preflight_paths",
    "programmatic_location",
    "remediation_for",
    "validate_opaque_relative",
    "validate_onenote_open_path",
    "validate_physical_name_has_no_onenote_id",
    "validate_role",
    "validate_run_evidence_leaf",
    "validate_working_name",
    "windows_path_units",
]
