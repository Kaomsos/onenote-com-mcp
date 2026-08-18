"""Local-only opaque Notebook bundle cache with exact-path safety gates.

This module never opens OneNote and never interprets ``.one`` files.  It only
copies bytes belonging to closed, disposable Notebook directories created by
the manual-validation runner.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import time
import uuid
from typing import Any, Callable, Iterator, Mapping

from ...local_filesystem import atomic_replace_with_retry
from ...path_budget import (
    AUTHORED_INSTANCE_KEY_PATTERN,
    FINGERPRINT_DISK_KEY_PATTERN,
    FINGERPRINT_PATTERN,
    PUBLISH_STAGING_PATTERN,
    MATERIALIZE_STAGING_PATTERN,
    ROLE_PATTERN,
    authored_location,
    fingerprint_disk_key,
    managed_absolute,
    preflight_path,
    preflight_paths,
    programmatic_location,
    validate_opaque_relative,
    validate_physical_name_has_no_onenote_id,
    validate_role,
    validate_working_name,
)
from ...runtime import InvariantFailure, RunnerFailure
from ...test_utils import utc_now
from ..fixture_recipes.recipe_base import RecipeBase


CACHE_SCHEMA_VERSION = 2
MANAGED_MARKER = ".managed-fixture-cache.json"
PROGRAMMATIC_INSTANCE_PATTERN = re.compile(r"programmatic-[0-9a-f]{16}")
AUTHORED_INSTANCE_PATTERN = re.compile(r"authored-[0-9a-f]{1,24}")
EXACT_ENTRY_STATES = frozenset({"ready", "evidence_only", "invalid", "cleanup_failed"})
LEGACY_SCHEMA_VERSION = 1
LEGACY_EMPTY_CACHE_FILES = frozenset(
    {
        MANAGED_MARKER,
        "index.json",
        "cleanup-tombstones.jsonl",
        "quarantine-evidence.jsonl",
        "recovery-evidence.jsonl",
    }
)


@dataclass(frozen=True)
class ByteInventory:
    files: tuple[tuple[str, int, str], ...]
    digest: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "files": [
                {"relative_path": path, "length": length, "sha256": digest}
                for path, length, digest in self.files
            ],
            "digest": self.digest,
        }


@dataclass(frozen=True)
class CacheHit:
    fingerprint: str
    template_instance_id: str
    entry_path: Path
    entry: Mapping[str, Any]


@dataclass(frozen=True)
class MaterializedBundle:
    fingerprint: str
    template_instance_id: str
    template_paths: Mapping[str, Path]
    working_paths: Mapping[str, Path]
    evidence_path: Path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _is_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & flag)


def _walk_plain_tree(
    root: Path,
    *,
    phase: str,
    target_kind: str,
    opaque_relative: bool,
) -> tuple[Path, ...]:
    """Traverse only after each ordinary path has passed its deterministic budget."""

    root = managed_absolute(root)
    preflight_path(root, phase=phase, target_kind=target_kind)
    if _is_reparse_point(root):
        raise InvariantFailure(f"Managed cache path is a reparse point: {root}")
    stack = [root]
    files: list[Path] = []
    while stack:
        current = stack.pop()
        children = sorted(current.iterdir(), key=lambda path: path.name)
        directories: list[Path] = []
        for candidate in children:
            relative = candidate.relative_to(root).as_posix()
            if opaque_relative:
                validate_opaque_relative(
                    relative,
                    phase=phase,
                    target_path=candidate,
                )
            preflight_path(
                candidate,
                phase=phase,
                target_kind=target_kind,
                relative_path=relative if opaque_relative else None,
            )
            if _is_reparse_point(candidate):
                raise InvariantFailure(
                    f"Managed cache tree contains a reparse point: {candidate}"
                )
            if candidate.is_dir():
                directories.append(candidate)
            elif candidate.is_file():
                files.append(candidate)
            else:
                raise InvariantFailure(
                    f"Managed cache tree contains an unsupported filesystem node: {candidate}"
                )
        stack.extend(reversed(directories))
    return tuple(files)


def _assert_plain_tree(root: Path) -> None:
    _walk_plain_tree(
        root,
        phase="cache_plain_tree_preflight",
        target_kind="cache_managed_path",
        opaque_relative=False,
    )


def inventory_directory(root: Path, *, phase: str = "inventory_preflight") -> ByteInventory:
    root = managed_absolute(root)
    preflight_path(
        root,
        phase=phase,
        target_kind="cache_template_source",
    )
    if not root.is_dir():
        raise InvariantFailure(f"Notebook template path is not a directory: {root}")
    files: list[tuple[str, int, str]] = []
    for path in sorted(
        _walk_plain_tree(
            root,
            phase=phase,
            target_kind="cache_template_source",
            opaque_relative=True,
        ),
        key=str,
    ):
        relative = path.relative_to(root).as_posix()
        files.append((relative, path.stat().st_size, _sha256_file(path)))
    if not files:
        raise InvariantFailure("A Notebook template directory must contain at least one file.")
    canonical = json.dumps(files, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return ByteInventory(tuple(files), hashlib.sha256(canonical).hexdigest())


def bundle_inventory(inventories: Mapping[str, ByteInventory]) -> str:
    payload = [(role, inventories[role].digest) for role in sorted(inventories)]
    return hashlib.sha256(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _atomic_json(
    path: Path,
    value: Mapping[str, Any],
    *,
    phase: str = "metadata_write_preflight",
    target_kind: str = "atomic_metadata",
) -> None:
    validate_physical_name_has_no_onenote_id(path)
    nonce = uuid.uuid4().hex[:16]
    temporary = path.with_name(f".{path.name}.{nonce}.tmp")
    preflight_paths(
        ((path, target_kind, None), (temporary, "atomic_metadata_temp", None)),
        phase=phase,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    atomic_replace_with_retry(temporary, path)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InvariantFailure(f"Invalid fixture cache metadata: {path}") from exc
    if not isinstance(value, dict):
        raise InvariantFailure(f"Fixture cache metadata must be an object: {path}")
    return value


def _atomic_budget_paths(
    path: Path,
    target_kind: str,
) -> tuple[tuple[Path, str, str | None], tuple[Path, str, str | None]]:
    return (
        (path, target_kind, None),
        (
            path.with_name(f".{path.name}.{'0' * 16}.tmp"),
            "atomic_metadata_temp",
            None,
        ),
    )


def legacy_empty_cache_activation_evidence(cache_root: Path) -> dict[str, Any] | None:
    """Prove that pre-upgrade ``clear all`` left only an empty owned shell.

    This is deliberately not a legacy cache lookup or migration path.  It
    accepts no legacy payload and exists only so the new schema can stamp the
    empty ownership metadata that the pre-upgrade maintenance command leaves
    behind after its human-confirmed deletion workflow.
    """

    try:
        root = managed_absolute(cache_root)
        validation_root = root.parent
        marker_path = root / MANAGED_MARKER
        index_path = root / "index.json"
        validation_marker_path = validation_root / ".managed-validation-root.json"
        preflight_paths(
            (
                (root, "legacy_empty_cache_root", None),
                (marker_path, "legacy_cache_marker", None),
                (index_path, "legacy_cache_index", None),
                (validation_marker_path, "legacy_validation_marker", None),
            ),
            phase="cache_schema_activation_proof",
        )
        if (
            validation_root.name != ".local-validation"
            or not root.is_dir()
            or not marker_path.is_file()
            or not index_path.is_file()
            or not validation_marker_path.is_file()
        ):
            return None
        if _is_reparse_point(validation_root) or _is_reparse_point(
            validation_marker_path
        ):
            return None
        _assert_plain_tree(root)
        if any(item.name not in LEGACY_EMPTY_CACHE_FILES for item in root.iterdir()):
            return None
        marker = _read_json(marker_path)
        index = _read_json(index_path)
        validation_marker = _read_json(validation_marker_path)
        post_clear_runs: list[tuple[Path, Path, Mapping[str, Any]]] = []
        for run_path in validation_root.glob("run-*"):
            state_path = run_path / "run-state.json"
            preflight_paths(
                (
                    (run_path, "run_root", None),
                    (state_path, "run_evidence", None),
                ),
                phase="cache_schema_activation_proof",
            )
            if not run_path.is_dir() or not state_path.is_file():
                return None
            _assert_plain_tree(run_path)
            state = _read_json(state_path)
            if (
                state.get("schema_version") != CACHE_SCHEMA_VERSION
                or state.get("human_only") is not True
                or state.get("agent_execution_prohibited") is not True
            ):
                return None
            post_clear_runs.append((run_path, state_path, state))
        index_schema = index.get("schema_version")
        if (
            marker.get("schema_version") != LEGACY_SCHEMA_VERSION
            or marker.get("purpose") != "local-onenote-mcp-fixture-cache"
            or index_schema not in {
                LEGACY_SCHEMA_VERSION,
                CACHE_SCHEMA_VERSION,
            }
            or index.get("entries") != {}
            or (
                index_schema == CACHE_SCHEMA_VERSION
                and (
                    index.get("activated_from_schema_version")
                    != LEGACY_SCHEMA_VERSION
                    or not isinstance(index.get("activation_summary"), str)
                )
            )
            or validation_marker.get("schema_version") != LEGACY_SCHEMA_VERSION
            or validation_marker.get("purpose")
            != "local-onenote-mcp-manual-validation"
        ):
            return None
        metadata_mtime = (
            max(marker_path.stat().st_mtime_ns, index_path.stat().st_mtime_ns)
            if index_schema == LEGACY_SCHEMA_VERSION
            else marker_path.stat().st_mtime_ns
        )
        required_summary = (
            index.get("activation_summary")
            if index_schema == CACHE_SCHEMA_VERSION
            else None
        )
        summaries = sorted(
            validation_root.glob("cleanup-summary-*.json"),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
        for summary_path in summaries:
            preflight_path(
                summary_path,
                phase="cache_schema_activation_proof",
                target_kind="legacy_clear_all_summary",
            )
            if _is_reparse_point(summary_path):
                continue
            if summary_path.stat().st_mtime_ns < metadata_mtime:
                continue
            if any(
                state_path.stat().st_mtime_ns < summary_path.stat().st_mtime_ns
                for _run_path, state_path, _state in post_clear_runs
            ):
                continue
            if required_summary is not None and str(summary_path) != required_summary:
                continue
            summary = _read_json(summary_path)
            counts = summary.get("counts")
            roots = summary.get("managed_roots")
            root_checks = summary.get("root_checks")
            open_snapshot = summary.get("open_path_snapshot")
            finalization = summary.get("finalization")
            targets = summary.get("targets")
            try:
                summary_created_at = datetime.fromisoformat(
                    str(summary.get("created_at", ""))
                )
                post_clear_run_times = [
                    datetime.fromisoformat(str(state.get("started_at", "")))
                    for _run_path, _state_path, state in post_clear_runs
                ]
            except ValueError:
                continue
            if summary_created_at.utcoffset() is None or any(
                value.utcoffset() is None for value in post_clear_run_times
            ):
                continue
            if any(
                run_started_at < summary_created_at
                for run_started_at in post_clear_run_times
            ):
                continue
            if not all(
                (
                    summary.get("schema_version") == LEGACY_SCHEMA_VERSION,
                    summary.get("action") == "clear-all",
                    summary.get("dry_run") is False,
                    summary.get("ok") is True,
                    summary.get("human_confirmation_required") is True,
                    summary.get("confirmation_mode") == "interactive_stdin",
                    isinstance(counts, Mapping),
                    isinstance(roots, Mapping),
                    isinstance(root_checks, Mapping),
                    isinstance(open_snapshot, Mapping),
                    isinstance(finalization, Mapping),
                    isinstance(targets, list),
                )
            ):
                continue
            if (
                counts.get("refused") != 0
                or counts.get("failed") != 0
                or counts.get("planned") != 0
                or counts.get("discovered") != counts.get("deleted")
                or roots.get("validation") != str(validation_root)
                or roots.get("cache") != str(root)
                or summary.get("summary_path") != str(summary_path)
                or root_checks.get("fixed_repository_root") is not True
                or root_checks.get("not_filesystem_root") is not True
                or root_checks.get("not_workspace_root") is not True
                or root_checks.get("root_marker_valid") is not True
                or root_checks.get("root_reparse_point_free") is not True
                or open_snapshot.get("status") != "complete"
                or open_snapshot.get("error") is not None
                or finalization.get("failures") != []
                or any(
                    not isinstance(target, Mapping)
                    or target.get("decision") != "deleted"
                    for target in targets
                )
            ):
                continue
            return {
                "schema_version": LEGACY_SCHEMA_VERSION,
                "summary_path": str(summary_path),
                "summary_created_at": summary.get("created_at"),
                "deleted_targets": counts.get("deleted"),
                "post_clear_schema_v2_runs": [
                    str(run_path)
                    for run_path, _state_path, _state in post_clear_runs
                ],
            }
    except (OSError, InvariantFailure):
        return None
    return None


class BundleCacheStore:
    """Own one configured cache root; callers cannot supply cache entry paths."""

    def __init__(self, cache_root: Path) -> None:
        self.cache_root = managed_absolute(cache_root)
        self.marker_path = self.cache_root / MANAGED_MARKER
        self.tombstone_path = self.cache_root / "cleanup-tombstones.jsonl"
        self.quarantine_path = self.cache_root / "quarantine-evidence.jsonl"
        self.recovery_path = self.cache_root / "recovery-evidence.jsonl"

    def initialize(self) -> None:
        preflight_path(
            self.cache_root,
            phase="cache_initialize_preflight",
            target_kind="cache_root",
        )
        if self.cache_root.parent == self.cache_root:
            raise RunnerFailure("Fixture cache root cannot be a filesystem root.")
        if self.cache_root.exists() and _is_reparse_point(self.cache_root):
            raise RunnerFailure("Fixture cache root cannot be a reparse point.")
        if self.cache_root.exists():
            marker_schema: object | None = None
            if self.marker_path.is_file():
                marker_schema = _read_json(self.marker_path).get("schema_version")
            if marker_schema == LEGACY_SCHEMA_VERSION:
                activation = legacy_empty_cache_activation_evidence(self.cache_root)
                if activation is None:
                    self._assert_no_legacy_layout()
                    raise RunnerFailure("Legacy fixture cache activation proof is incomplete.")
                self._activate_legacy_empty_shell(activation)
            _assert_plain_tree(self.cache_root)
            self._assert_no_legacy_layout()
        self.cache_root.mkdir(parents=True, exist_ok=True)
        if self.marker_path.exists():
            marker = _read_json(self.marker_path)
            if marker.get("schema_version") != CACHE_SCHEMA_VERSION or marker.get(
                "purpose"
            ) != "local-onenote-mcp-fixture-cache":
                raise RunnerFailure("Configured cache root is not owned by this validation runtime.")
        else:
            _atomic_json(
                self.marker_path,
                {
                    "schema_version": CACHE_SCHEMA_VERSION,
                    "purpose": "local-onenote-mcp-fixture-cache",
                    "created_at": utc_now(),
                    "local_only": True,
                    "opaque_notebook_bytes_only": True,
                },
                phase="cache_initialize_preflight",
                target_kind="cache_marker",
            )

    def _activate_legacy_empty_shell(self, evidence: Mapping[str, Any]) -> None:
        """Atomically stamp a proof-backed empty legacy shell as schema v2."""

        activated_at = utc_now()
        index_path = self.cache_root / "index.json"
        preflight_paths(
            (
                *_atomic_budget_paths(index_path, "cache_index"),
                *_atomic_budget_paths(self.marker_path, "cache_marker"),
            ),
            phase="cache_schema_activation_preflight",
        )
        _atomic_json(
            index_path,
            {
                "schema_version": CACHE_SCHEMA_VERSION,
                "entries": {},
                "activated_from_schema_version": LEGACY_SCHEMA_VERSION,
                "activation_summary": evidence["summary_path"],
                "activated_at": activated_at,
            },
            phase="cache_schema_activation_preflight",
            target_kind="cache_index",
        )
        _atomic_json(
            self.marker_path,
            {
                "schema_version": CACHE_SCHEMA_VERSION,
                "purpose": "local-onenote-mcp-fixture-cache",
                "created_at": activated_at,
                "local_only": True,
                "opaque_notebook_bytes_only": True,
                "activated_from_schema_version": LEGACY_SCHEMA_VERSION,
                "activation_summary": evidence["summary_path"],
            },
            phase="cache_schema_activation_preflight",
            target_kind="cache_marker",
        )

    def _assert_no_legacy_layout(self) -> None:
        marker = self.cache_root / MANAGED_MARKER
        if marker.exists():
            value = _read_json(marker)
            if value.get("schema_version") != CACHE_SCHEMA_VERSION:
                raise RunnerFailure(
                    "Legacy fixture cache schema remains. Return to the pre-upgrade version "
                    "and complete its human-gated clear all workflow."
                )
        index_path = self.cache_root / "index.json"
        if index_path.exists():
            index = _read_json(index_path)
            if index.get("schema_version") != CACHE_SCHEMA_VERSION:
                raise RunnerFailure(
                    "Legacy fixture cache index remains. Return to the pre-upgrade version "
                    "and complete its human-gated clear all workflow."
                )
        allowed_files = {
            MANAGED_MARKER,
            "index.json",
            "cleanup-tombstones.jsonl",
            "quarantine-evidence.jsonl",
            "recovery-evidence.jsonl",
        }
        for child in self.cache_root.iterdir():
            if child.name in allowed_files:
                if not child.is_file():
                    raise RunnerFailure("Fixture cache fixed metadata layout is invalid.")
                continue
            if child.is_dir() and re.fullmatch(r"[0-9a-f]{64}", child.name):
                raise RunnerFailure(
                    "Legacy 64-hex fixture cache layout remains. Return to the pre-upgrade "
                    "version and complete its human-gated clear all workflow."
                )
            if child.is_dir() and FINGERPRINT_DISK_KEY_PATTERN.fullmatch(child.name):
                for grandchild in child.iterdir():
                    if grandchild.name == "instances" and grandchild.is_dir():
                        continue
                    if grandchild.name == "bundle.lock.json" and grandchild.is_file():
                        continue
                    raise RunnerFailure(
                        "Unknown or legacy fixture cache fingerprint layout remains."
                    )
                instances = child / "instances"
                if instances.exists():
                    for typed in instances.iterdir():
                        if typed.name == "p" and typed.is_dir():
                            self._assert_fixed_instance_layout(typed)
                            continue
                        if typed.name == "a" and typed.is_dir():
                            if any(
                                not authored.is_dir()
                                or AUTHORED_INSTANCE_KEY_PATTERN.fullmatch(authored.name) is None
                                for authored in typed.iterdir()
                            ):
                                raise RunnerFailure("Legacy full-instance cache layout remains.")
                            for authored in typed.iterdir():
                                self._assert_fixed_instance_layout(authored)
                            continue
                        raise RunnerFailure("Legacy full-instance cache layout remains.")
                continue
            if PUBLISH_STAGING_PATTERN.fullmatch(child.name):
                if not child.is_dir():
                    raise RunnerFailure("Fixture cache staging layout is invalid.")
                self._assert_owned_staging_layout(child)
                continue
            if child.is_dir() and child.name.startswith((".staging-", ".materializing-")):
                raise RunnerFailure(
                    "Legacy fixture cache staging remains. Return to the pre-upgrade version "
                    "and complete its human-gated clear all workflow."
                )
            raise RunnerFailure("Unknown or legacy fixture cache root layout remains.")

    @staticmethod
    def _assert_fixed_instance_layout(instance_root: Path) -> None:
        allowed_instance_files = {"bundle-entry.json", "staging-marker.json"}
        allowed_role_files = {
            "byte-inventory.json",
            "template-manifest.json",
            "template-fixture-result.json",
            "template-snapshot.json",
        }
        instance_children = {child.name: child for child in instance_root.iterdir()}
        if not {"bundle-entry.json", "staging-marker.json", "notebooks"}.issubset(
            instance_children
        ):
            raise RunnerFailure("Fixture cache instance fixed layout is incomplete.")
        for child in instance_children.values():
            if child.name in allowed_instance_files and child.is_file():
                continue
            if child.name != "notebooks" or not child.is_dir():
                raise RunnerFailure("Fixture cache instance fixed layout is invalid.")
            role_roots = list(child.iterdir())
            if not role_roots:
                raise RunnerFailure("Fixture cache role layout is incomplete.")
            for role_root in role_roots:
                if (
                    not role_root.is_dir()
                    or ROLE_PATTERN.fullmatch(role_root.name) is None
                ):
                    raise RunnerFailure("Fixture cache role layout is invalid.")
                role_children = {
                    role_child.name: role_child for role_child in role_root.iterdir()
                }
                if not {"template-notebook", "byte-inventory.json"}.issubset(
                    role_children
                ):
                    raise RunnerFailure("Fixture cache role fixed layout is incomplete.")
                for role_child in role_children.values():
                    if (
                        role_child.name == "template-notebook"
                        and role_child.is_dir()
                    ):
                        continue
                    if role_child.name in allowed_role_files and role_child.is_file():
                        continue
                    raise RunnerFailure("Fixture cache role fixed layout is invalid.")

    def _assert_owned_staging_layout(self, staging: Path) -> None:
        marker_path = staging / "staging-marker.json"
        if not marker_path.is_file():
            raise RunnerFailure("Fixture cache staging ownership marker is missing.")
        marker = _read_json(marker_path)
        fingerprint = str(marker.get("fingerprint", ""))
        instance_id = str(marker.get("template_instance_id", ""))
        location = marker.get("instance_location")
        roles = marker.get("roles")
        if not isinstance(location, Mapping):
            raise RunnerFailure("Fixture cache staging typed identity is invalid.")
        projection_digest = location.get("projection_digest")
        try:
            expected_location = self._instance_location(
                instance_id,
                projection_digest=(
                    str(projection_digest)
                    if projection_digest is not None
                    else None
                ),
            )
        except RunnerFailure as exc:
            raise RunnerFailure("Fixture cache staging typed identity is invalid.") from exc
        if (
            marker.get("schema_version") != CACHE_SCHEMA_VERSION
            or marker.get("staging_name") != staging.name
            or FINGERPRINT_PATTERN.fullmatch(fingerprint) is None
            or marker.get("fingerprint_disk_key") != fingerprint_disk_key(fingerprint)
            or dict(location) != expected_location
            or not isinstance(roles, list)
            or not roles
            or any(
                not isinstance(role, str) or ROLE_PATTERN.fullmatch(role) is None
                for role in roles
            )
            or len(set(roles)) != len(roles)
        ):
            raise RunnerFailure("Fixture cache staging ownership metadata is invalid.")

    def _identity_parts(self, fingerprint: str, instance_id: str) -> tuple[str, tuple[str, ...]]:
        if FINGERPRINT_PATTERN.fullmatch(fingerprint) is None:
            raise RunnerFailure("Cache fingerprint must be a canonical SHA-256 digest.")
        if PROGRAMMATIC_INSTANCE_PATTERN.fullmatch(instance_id):
            location = programmatic_location(instance_id)
        elif AUTHORED_INSTANCE_PATTERN.fullmatch(instance_id):
            location = authored_location(instance_id)
        else:
            raise RunnerFailure("Template instance ID is not a supported typed identifier.")
        return fingerprint_disk_key(fingerprint), location.parts

    def instance_path(self, fingerprint: str, instance_id: str) -> Path:
        disk_key, location_parts = self._identity_parts(fingerprint, instance_id)
        path = managed_absolute(
            self.cache_root.joinpath(disk_key, "instances", *location_parts)
        )
        preflight_path(
            path,
            phase="cache_instance_preflight",
            target_kind="cache_instance",
        )
        return path

    def _instance_location(
        self,
        instance_id: str,
        *,
        projection_digest: str | None = None,
    ) -> dict[str, object]:
        if PROGRAMMATIC_INSTANCE_PATTERN.fullmatch(instance_id):
            return programmatic_location(instance_id).as_dict()
        return authored_location(
            instance_id,
            projection_digest=projection_digest,
        ).as_dict()

    def _entry_location_matches(self, entry: Mapping[str, Any], instance_id: str) -> bool:
        recorded = entry.get("instance_location")
        if not isinstance(recorded, Mapping):
            return False
        digest = recorded.get("projection_digest")
        expected = self._instance_location(
            instance_id,
            projection_digest=str(digest) if digest is not None else None,
        )
        return dict(recorded) == expected

    @staticmethod
    def _projection_digest(entry: Mapping[str, Any]) -> str | None:
        location = entry.get("instance_location")
        if not isinstance(location, Mapping):
            return None
        value = location.get("projection_digest")
        return str(value) if value is not None else None

    def _assert_entry_owned(
        self,
        fingerprint: str,
        instance_id: str,
        path: Path,
        entry: Mapping[str, Any],
    ) -> None:
        roles = entry.get("roles")
        role_entries = entry.get("role_entries")
        if (
            entry.get("schema_version") != CACHE_SCHEMA_VERSION
            or entry.get("fingerprint") != fingerprint
            or entry.get("fingerprint_disk_key") != fingerprint_disk_key(fingerprint)
            or entry.get("template_instance_id") != instance_id
            or not self._entry_location_matches(entry, instance_id)
            or path != self.instance_path(fingerprint, instance_id)
            or not isinstance(roles, list)
            or not roles
            or any(
                not isinstance(role, str) or ROLE_PATTERN.fullmatch(role) is None
                for role in roles
            )
            or len(set(roles)) != len(roles)
            or not isinstance(role_entries, Mapping)
            or set(role_entries) != set(roles)
        ):
            raise RunnerFailure(
                "Cache entry ownership metadata does not match its typed path."
            )
        for role in roles:
            role_entry = role_entries.get(role)
            expected_template = managed_absolute(
                path / "notebooks" / role / "template-notebook"
            )
            if (
                not isinstance(role_entry, Mapping)
                or managed_absolute(str(role_entry.get("template_path", "")))
                != expected_template
            ):
                raise RunnerFailure(
                    "Cache entry template path does not match its owned typed path."
                )
            preflight_path(
                expected_template,
                phase="cache_entry_identity_preflight",
                target_kind="cache_template",
            )

    def _assert_owned_instance(self, fingerprint: str, instance_id: str, path: Path) -> None:
        expected = self.instance_path(fingerprint, instance_id)
        if managed_absolute(path) != expected or self.cache_root not in expected.parents:
            raise RunnerFailure("Cache operation escaped the exact typed instance path.")
        if expected in {self.cache_root, self.cache_root.parent}:
            raise RunnerFailure("Cache operation resolved to a broad root.")
        if expected.exists():
            _assert_plain_tree(expected)

    @contextmanager
    def lock(self, fingerprint: str, *, run_id: str, timeout_seconds: int = 30) -> Iterator[None]:
        if FINGERPRINT_PATTERN.fullmatch(fingerprint) is None:
            raise RunnerFailure("Cache fingerprint must be a canonical SHA-256 digest.")
        disk_key = fingerprint_disk_key(fingerprint)
        lock_path = self.cache_root / disk_key / "bundle.lock.json"
        preflight_path(
            lock_path,
            phase="cache_lock_preflight",
            target_kind="cache_lock",
        )
        self._assert_fingerprint_identity(fingerprint)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        payload = json.dumps(
            {
                "schema_version": CACHE_SCHEMA_VERSION,
                "fingerprint": fingerprint,
                "run_id": run_id,
                "process_id": os.getpid(),
                "created_at": utc_now(),
            },
            sort_keys=True,
        ).encode("utf-8")
        while True:
            try:
                descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(payload)
                break
            except FileExistsError:
                existing = _read_json(lock_path)
                if existing.get("fingerprint") != fingerprint:
                    raise RunnerFailure(
                        "Fixture cache fingerprint disk-key collision detected in lock metadata."
                    )
                if time.monotonic() - started >= timeout_seconds:
                    raise RunnerFailure("Fixture cache fingerprint lock is already owned.")
                time.sleep(0.05)
        try:
            yield
        finally:
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass

    def _assert_fingerprint_identity(self, fingerprint: str) -> None:
        disk_key = fingerprint_disk_key(fingerprint)
        index_path = self.cache_root / "index.json"
        fingerprint_root = self.cache_root / disk_key
        bucket = fingerprint_root / "instances"
        preflight_paths(
            (
                (index_path, "cache_index", None),
                (fingerprint_root, "cache_fingerprint_root", None),
                (bucket, "cache_instances_root", None),
            ),
            phase="cache_identity_preflight",
        )
        if index_path.exists():
            index = _read_json(index_path)
            if index.get("schema_version") != CACHE_SCHEMA_VERSION:
                raise RunnerFailure("Legacy fixture cache index schema remains; clear it with the pre-upgrade version.")
            entries = index.get("entries")
            if not isinstance(entries, Mapping):
                raise RunnerFailure("Fixture cache index entries are invalid.")
            for key, value in entries.items():
                if not isinstance(value, Mapping):
                    raise RunnerFailure("Fixture cache index entry is invalid.")
                recorded = str(value.get("fingerprint", ""))
                instance_id = str(value.get("template_instance_id", ""))
                location = value.get("instance_location")
                if not isinstance(location, Mapping):
                    raise RunnerFailure("Fixture cache index instance location is invalid.")
                projection_digest = location.get("projection_digest")
                try:
                    expected_location = self._instance_location(
                        instance_id,
                        projection_digest=(
                            str(projection_digest)
                            if projection_digest is not None
                            else None
                        ),
                    )
                except RunnerFailure as exc:
                    raise RunnerFailure("Fixture cache index identity is invalid.") from exc
                if (
                    FINGERPRINT_PATTERN.fullmatch(recorded) is None
                    or value.get("fingerprint_disk_key")
                    != fingerprint_disk_key(recorded)
                    or key != f"{recorded}:{instance_id}"
                    or dict(location) != expected_location
                ):
                    raise RunnerFailure("Fixture cache index identity is invalid.")
                if value.get("fingerprint_disk_key") == disk_key and recorded != fingerprint:
                    raise RunnerFailure("Fixture cache fingerprint disk-key collision detected in index metadata.")
        if not bucket.exists():
            return
        entry_paths = [bucket / "p" / "bundle-entry.json"]
        authored_root = bucket / "a"
        if authored_root.is_dir():
            entry_paths.extend(
                child / "bundle-entry.json"
                for child in authored_root.iterdir()
                if child.is_dir()
                and AUTHORED_INSTANCE_KEY_PATTERN.fullmatch(child.name) is not None
            )
        for entry_path in entry_paths:
            preflight_path(
                entry_path,
                phase="cache_identity_preflight",
                target_kind="cache_entry_metadata",
            )
            if not entry_path.is_file():
                continue
            entry = _read_json(entry_path)
            if entry.get("fingerprint") != fingerprint:
                raise RunnerFailure("Fixture cache fingerprint disk-key collision detected in entry metadata.")

    def lookup(self, recipe: RecipeBase, instance_id: str) -> CacheHit | None:
        self._assert_fingerprint_identity(recipe.cache_fingerprint)
        path = self.instance_path(recipe.cache_fingerprint, instance_id)
        entry_path = path / "bundle-entry.json"
        if not entry_path.exists():
            return None
        self._assert_owned_instance(recipe.cache_fingerprint, instance_id, path)
        entry = _read_json(entry_path)
        if entry.get("state") not in {"ready", "evidence_only"}:
            return None
        return self._validate_hit(recipe, instance_id, path, entry)

    def exact_entry_state(self, recipe: RecipeBase, instance_id: str) -> str | None:
        """Return the owned exact entry state without treating invalid as a miss."""

        self._assert_fingerprint_identity(recipe.cache_fingerprint)
        path = self.instance_path(recipe.cache_fingerprint, instance_id)
        if not path.exists():
            return None
        self._assert_owned_instance(recipe.cache_fingerprint, instance_id, path)
        entry_path = path / "bundle-entry.json"
        if not entry_path.exists():
            raise RunnerFailure(
                "Existing exact fixture cache instance is missing ownership metadata; "
                "cleanup and rebuild are blocked."
            )
        entry = _read_json(entry_path)
        self._assert_entry_owned(
            recipe.cache_fingerprint,
            instance_id,
            path,
            entry,
        )
        state = entry.get("state")
        if state not in EXACT_ENTRY_STATES:
            raise RunnerFailure(
                f"Exact fixture cache instance has unsupported state {state!r}; "
                "cleanup and rebuild are blocked."
            )
        return str(state)

    def list_ready_instances(
        self,
        recipe: RecipeBase,
        *,
        mutation_eligible_only: bool = False,
    ) -> list[str]:
        """Return owned ready instance IDs for one fingerprint."""

        self._assert_fingerprint_identity(recipe.cache_fingerprint)
        disk_key = fingerprint_disk_key(recipe.cache_fingerprint)
        bucket = self.cache_root / disk_key / "instances"
        if not bucket.is_dir():
            return []
        instance_ids: list[str] = []
        programmatic = bucket / "p" / "bundle-entry.json"
        if programmatic.is_file():
            entry = _read_json(programmatic)
            if (
                entry.get("state") == "ready"
                and entry.get("fingerprint") == recipe.cache_fingerprint
                and (
                    not mutation_eligible_only
                    or entry.get("mutation_eligible") is not False
                )
            ):
                instance_ids.append(str(entry.get("template_instance_id", "")))
        authored_root = bucket / "a"
        if authored_root.is_dir():
            for child in sorted(authored_root.iterdir()):
                if not child.is_dir():
                    continue
                if AUTHORED_INSTANCE_KEY_PATTERN.fullmatch(child.name) is None:
                    continue
                entry_path = child / "bundle-entry.json"
                if not entry_path.is_file():
                    continue
                entry = _read_json(entry_path)
                if entry.get("state") != "ready":
                    continue
                if entry.get("fingerprint") != recipe.cache_fingerprint:
                    continue
                if mutation_eligible_only and entry.get("mutation_eligible") is not True:
                    continue
                instance_ids.append(str(entry.get("template_instance_id", "")))
        return [value for value in instance_ids if value]

    def _validate_hit(
        self,
        recipe: RecipeBase,
        instance_id: str,
        path: Path,
        entry: Mapping[str, Any],
    ) -> CacheHit:
        if (
            entry.get("schema_version") != CACHE_SCHEMA_VERSION
            or entry.get("fingerprint") != recipe.cache_fingerprint
            or entry.get("template_instance_id") != instance_id
            or entry.get("fingerprint_disk_key")
            != fingerprint_disk_key(recipe.cache_fingerprint)
            or not self._entry_location_matches(entry, instance_id)
            or entry.get("cache_identity") != recipe.cache_identity.as_dict()
        ):
            raise InvariantFailure("Cache entry identity or schema is incompatible.")
        expected_roles = [role.role for role in recipe.cache_identity.notebook_roles]
        if entry.get("roles") != expected_roles:
            raise InvariantFailure("Cache entry role set differs from the Recipe identity.")
        inventories: dict[str, ByteInventory] = {}
        for role in expected_roles:
            template = path / "notebooks" / role / "template-notebook"
            observed = inventory_directory(template)
            recorded = entry.get("role_inventories", {}).get(role)
            if recorded != observed.as_dict():
                raise InvariantFailure(f"Cache byte inventory mismatch for role {role}.")
            inventories[role] = observed
        if entry.get("bundle_inventory_digest") != bundle_inventory(inventories):
            raise InvariantFailure("Cache bundle inventory digest mismatch.")
        index = _read_json(self.cache_root / "index.json")
        record = index.get("entries", {}).get(
            f"{recipe.cache_fingerprint}:{instance_id}"
        )
        if not isinstance(record, Mapping) or any(
            (
                record.get("fingerprint") != recipe.cache_fingerprint,
                record.get("fingerprint_disk_key")
                != fingerprint_disk_key(recipe.cache_fingerprint),
                record.get("template_instance_id") != instance_id,
                record.get("instance_location") != entry.get("instance_location"),
                record.get("state") != entry.get("state"),
            )
        ):
            raise InvariantFailure("Cache index identity does not match the exact entry.")
        return CacheHit(recipe.cache_fingerprint, instance_id, path, entry)

    def recover_retryable_open_failure(
        self,
        recipe: RecipeBase,
        instance_id: str,
        *,
        run_id: str,
    ) -> CacheHit | None:
        """Recover a historical false quarantine for a run-local open failure only."""

        path = self.instance_path(recipe.cache_fingerprint, instance_id)
        entry_path = path / "bundle-entry.json"
        if not entry_path.exists():
            return None
        self._assert_owned_instance(recipe.cache_fingerprint, instance_id, path)
        entry = _read_json(entry_path)
        reason = str(entry.get("invalid_reason", ""))
        validation = entry.get("validation", {})
        if (
            entry.get("state") != "invalid"
            or not reason.startswith(
                (
                    "materialized-open failed:",
                    "cold-materialized-open failed:",
                    "bootstrap-materialized-open failed:",
                )
            )
            or not isinstance(validation, Mapping)
            or not (
                validation.get("status") == "passed"
                or validation.get("passed") is True
            )
        ):
            return None
        self._validate_hit(recipe, instance_id, path, entry)
        preflight_paths(
            (
                *_atomic_budget_paths(entry_path, "cache_entry_metadata"),
                *_atomic_budget_paths(self.cache_root / "index.json", "cache_index"),
                (self.recovery_path, "cache_recovery_evidence", None),
            ),
            phase="cache_recovery_preflight",
        )
        prior = {
            "reason": reason,
            "invalidated_at": entry.get("invalidated_at"),
            "invalidated_by_run": entry.get("invalidated_by_run"),
        }
        recoveries = list(entry.get("open_failure_recoveries", []))
        recoveries.append({**prior, "recovered_at": utc_now(), "recovered_by_run": run_id})
        entry.update(state="ready", open_failure_recoveries=recoveries)
        entry.pop("invalid_reason", None)
        entry.pop("invalidated_at", None)
        entry.pop("invalidated_by_run", None)
        _atomic_json(entry_path, entry)
        self._update_index(
            recipe.cache_fingerprint,
            instance_id,
            "ready",
            projection_digest=self._projection_digest(entry),
        )
        evidence = {
            "schema_version": CACHE_SCHEMA_VERSION,
            "fingerprint": recipe.cache_fingerprint,
            "template_instance_id": instance_id,
            "state": "ready",
            "reason": "working-copy materialized-open failure did not invalidate template bytes",
            "prior_quarantine": prior,
            "run_id": run_id,
            "template_inventory_revalidated": True,
            "template_deleted": False,
            "created_at": utc_now(),
        }
        preflight_path(
            self.recovery_path,
            phase="cache_recovery_preflight",
            target_kind="cache_recovery_evidence",
        )
        with self.recovery_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(evidence, ensure_ascii=False, sort_keys=True) + "\n")
        return CacheHit(recipe.cache_fingerprint, instance_id, path, entry)

    def publish(
        self,
        recipe: RecipeBase,
        instance_id: str,
        *,
        source_paths: Mapping[str, Path],
        source_notebooks: Mapping[str, Mapping[str, Any]],
        closed_roles: set[str],
        validation: Mapping[str, Any],
        artifacts: Mapping[str, Mapping[str, Any]] | None = None,
        projection_digest: str | None = None,
        state: str = "ready",
        mutation_eligible: bool | None = None,
        move_source_deletion_allowed: bool | None = None,
    ) -> CacheHit:
        if state not in {"ready", "evidence_only"}:
            raise RunnerFailure("Only validated ready/evidence_only bundles can be published.")
        roles = tuple(role.role for role in recipe.cache_identity.notebook_roles)
        if set(source_paths) != set(roles) or set(source_notebooks) != set(roles):
            raise RunnerFailure("Cache publish requires the complete Recipe role bundle.")
        if closed_roles != set(roles):
            raise RunnerFailure("Every Notebook role must be precisely closed before cache publish.")
        if artifacts is not None and set(artifacts) != set(roles):
            raise RunnerFailure("Cache publish artifacts must cover the complete role bundle.")
        authored_instance = AUTHORED_INSTANCE_PATTERN.fullmatch(instance_id) is not None
        if authored_instance:
            if projection_digest is None:
                raise RunnerFailure(
                    "User-authored cache publication requires the full projection digest."
                )
            expected_eligible = state == "ready"
            if mutation_eligible is None:
                mutation_eligible = expected_eligible
            if move_source_deletion_allowed is None:
                move_source_deletion_allowed = expected_eligible
            if (
                mutation_eligible is not expected_eligible
                or move_source_deletion_allowed is not expected_eligible
            ):
                raise RunnerFailure(
                    "User-authored cache eligibility must match its ready/evidence_only state."
                )
        self._assert_fingerprint_identity(recipe.cache_fingerprint)
        final = self.instance_path(recipe.cache_fingerprint, instance_id)
        self._assert_owned_instance(recipe.cache_fingerprint, instance_id, final)
        if final.exists():
            raise RunnerFailure("Refusing to overwrite an existing fixture cache instance.")
        inventories: dict[str, ByteInventory] = {}
        resolved_sources: dict[str, Path] = {}
        for role in roles:
            validate_role(role)
            source = managed_absolute(source_paths[role])
            if not source.is_dir():
                raise InvariantFailure(f"Notebook template path is not a directory: {source}")
            resolved_sources[role] = source
            inventories[role] = inventory_directory(
                source,
                phase="cache_publish_preflight",
            )

        staging = self._allocate_staging_path(
            prefix=".s-",
            pattern=PUBLISH_STAGING_PATTERN,
            phase="cache_publish_preflight",
            inventories=inventories,
            final=final,
            artifacts=artifacts,
        )
        final.parent.mkdir(parents=True, exist_ok=True)
        try:
            _atomic_json(
                staging / "staging-marker.json",
                {
                    "schema_version": CACHE_SCHEMA_VERSION,
                    "staging_name": staging.name,
                    "fingerprint": recipe.cache_fingerprint,
                    "fingerprint_disk_key": fingerprint_disk_key(
                        recipe.cache_fingerprint
                    ),
                    "template_instance_id": instance_id,
                    "instance_location": self._instance_location(
                        instance_id,
                        projection_digest=projection_digest,
                    ),
                    "roles": list(roles),
                    "created_at": utc_now(),
                },
                phase="cache_publish_preflight",
                target_kind="cache_staging_marker",
            )
            role_entries: dict[str, Any] = {}
            for role in roles:
                source = resolved_sources[role]
                target = staging / "notebooks" / role / "template-notebook"
                shutil.copytree(source, target)
                source_inventory = inventories[role]
                copied_inventory = inventory_directory(
                    target,
                    phase="cache_publish_copy_verification",
                )
                if copied_inventory != source_inventory:
                    raise InvariantFailure(f"Opaque template copy mismatch for role {role}.")
                role_entries[role] = {
                    "template_path": str(
                        managed_absolute(
                            final / "notebooks" / role / "template-notebook"
                        )
                    ),
                    "source_notebook": dict(source_notebooks[role]),
                    "closed_before_publish": True,
                }
                _atomic_json(
                    staging / "notebooks" / role / "byte-inventory.json",
                    copied_inventory.as_dict(),
                    phase="cache_publish_preflight",
                    target_kind="cache_inventory",
                )
                if artifacts is not None:
                    for artifact_name in ("manifest", "fixture_result", "snapshot"):
                        value = artifacts[role].get(artifact_name)
                        if not isinstance(value, Mapping):
                            raise RunnerFailure(
                                f"Cache publish is missing {artifact_name} for role {role}."
                            )
                        _atomic_json(
                            staging
                            / "notebooks"
                            / role
                            / f"template-{artifact_name.replace('_', '-')}.json",
                            value,
                            phase="cache_publish_preflight",
                            target_kind="cache_artifact",
                        )
            entry = {
                "schema_version": CACHE_SCHEMA_VERSION,
                "state": state,
                "fingerprint": recipe.cache_fingerprint,
                "fingerprint_disk_key": fingerprint_disk_key(recipe.cache_fingerprint),
                "template_instance_id": instance_id,
                "instance_location": self._instance_location(
                    instance_id,
                    projection_digest=projection_digest,
                ),
                "recipe_name": recipe.recipe_name,
                "recipe_class": type(recipe).__name__,
                "recipe_version": recipe.recipe_version,
                "build_mode": recipe.build_mode.value,
                "cache_identity": recipe.cache_identity.as_dict(),
                "roles": list(roles),
                "role_entries": role_entries,
                "role_inventories": {
                    role: inventories[role].as_dict() for role in roles
                },
                "bundle_inventory_digest": bundle_inventory(inventories),
                "validation": dict(validation),
                "created_at": utc_now(),
                "immutable": True,
                "opened_template": False,
                "path_budget": self._publish_budget_evidence(
                    staging,
                    final,
                    inventories,
                    artifacts,
                ),
            }
            if authored_instance:
                entry.update(
                    mutation_eligible=mutation_eligible,
                    move_source_deletion_allowed=move_source_deletion_allowed,
                )
            _atomic_json(
                staging / "bundle-entry.json",
                entry,
                phase="cache_publish_preflight",
                target_kind="cache_entry_metadata",
            )
            atomic_replace_with_retry(
                staging,
                final,
                destination_must_be_absent=True,
            )
            self._update_index(
                recipe.cache_fingerprint,
                instance_id,
                state,
                projection_digest=projection_digest,
            )
            return CacheHit(recipe.cache_fingerprint, instance_id, final, entry)
        except Exception as publish_error:
            if staging.exists():
                try:
                    shutil.rmtree(staging)
                except OSError as cleanup_error:
                    raise RunnerFailure(
                        "Cache publish failed and its owned staging directory could not be removed."
                    ) from publish_error
            raise

    def _update_index(
        self,
        fingerprint: str,
        instance_id: str,
        state: str,
        *,
        projection_digest: str | None = None,
    ) -> None:
        path = self.cache_root / "index.json"
        index = _read_json(path) if path.exists() else {
            "schema_version": CACHE_SCHEMA_VERSION,
            "entries": {},
        }
        if index.get("schema_version") != CACHE_SCHEMA_VERSION or not isinstance(
            index.get("entries"), Mapping
        ):
            raise RunnerFailure("Fixture cache index schema is invalid.")
        entries = dict(index.get("entries", {}))
        entries[f"{fingerprint}:{instance_id}"] = {
            "fingerprint": fingerprint,
            "fingerprint_disk_key": fingerprint_disk_key(fingerprint),
            "template_instance_id": instance_id,
            "instance_location": self._instance_location(
                instance_id,
                projection_digest=projection_digest,
            ),
            "state": state,
            "updated_at": utc_now(),
        }
        index["entries"] = entries
        _atomic_json(path, index)

    def _publish_budget_paths(
        self,
        staging: Path,
        final: Path,
        inventories: Mapping[str, ByteInventory],
        artifacts: Mapping[str, Mapping[str, Any]] | None,
    ) -> list[tuple[Path, str, str | None]]:
        paths: list[tuple[Path, str, str | None]] = [
            (self.cache_root, "cache_root", None),
            *_atomic_budget_paths(self.cache_root / "index.json", "cache_index"),
            (final / "bundle-entry.json", "cache_entry_metadata", None),
            (
                staging / "bundle-entry.json",
                "cache_publish_staging_metadata",
                None,
            ),
            (
                staging / "staging-marker.json",
                "cache_staging_marker",
                None,
            ),
        ]
        metadata_names = ["byte-inventory.json"]
        if artifacts is not None:
            metadata_names.extend(
                ("template-manifest.json", "template-fixture-result.json", "template-snapshot.json")
            )
        for role, inventory in inventories.items():
            for relative, _length, _digest in inventory.files:
                paths.extend(
                    (
                        (
                            final / "notebooks" / role / "template-notebook" / Path(relative),
                            "cache_template",
                            relative,
                        ),
                        (
                            staging / "notebooks" / role / "template-notebook" / Path(relative),
                            "cache_publish_staging",
                            relative,
                        ),
                    )
                )
            for name in metadata_names:
                for root, kind in (
                    (final, "cache_artifact"),
                    (staging, "cache_publish_staging_artifact"),
                ):
                    metadata = root / "notebooks" / role / name
                    paths.extend(
                        (
                            (metadata, kind, None),
                            (
                                metadata.with_name(f".{metadata.name}.{'0' * 16}.tmp"),
                                "atomic_metadata_temp",
                                None,
                            ),
                        )
                    )
        for metadata, kind in (
            (final / "bundle-entry.json", "cache_entry_metadata"),
            (staging / "bundle-entry.json", "cache_publish_staging_metadata"),
            (staging / "staging-marker.json", "cache_staging_marker"),
        ):
            paths.append(
                (
                    metadata.with_name(f".{metadata.name}.{'0' * 16}.tmp"),
                    "atomic_metadata_temp",
                    None,
                )
            )
        return paths

    def _publish_budget_evidence(
        self,
        staging: Path,
        final: Path,
        inventories: Mapping[str, ByteInventory],
        artifacts: Mapping[str, Mapping[str, Any]] | None,
    ) -> dict[str, object]:
        return preflight_paths(
            self._publish_budget_paths(staging, final, inventories, artifacts),
            phase="cache_publish_preflight",
        )

    def _allocate_staging_path(
        self,
        *,
        prefix: str,
        pattern: re.Pattern[str],
        phase: str,
        inventories: Mapping[str, ByteInventory],
        final: Path,
        artifacts: Mapping[str, Mapping[str, Any]] | None,
    ) -> Path:
        for _attempt in range(32):
            candidate = self.cache_root / f"{prefix}{uuid.uuid4().hex[:16]}"
            if pattern.fullmatch(candidate.name) is None:
                raise InvariantFailure("Generated staging name violates the typed schema.")
            self._publish_budget_evidence(candidate, final, inventories, artifacts)
            try:
                candidate.mkdir()
            except FileExistsError:
                continue
            else:
                return candidate
        raise RunnerFailure("Unable to allocate a unique fixture cache staging directory.")

    def materialize(
        self,
        hit: CacheHit,
        run_dir: Path,
        *,
        working_names: Mapping[str, str] | None = None,
        cache_origin: str = "validated_hit",
    ) -> MaterializedBundle:
        materialize_started = time.monotonic()
        run_dir = managed_absolute(run_dir)
        working_root = run_dir / "notebooks"
        template_paths: dict[str, Path] = {}
        working_paths: dict[str, Path] = {}
        published_working_paths: list[Path] = []
        evidence_roles: dict[str, Any] = {}
        roles = tuple(str(role) for role in hit.entry["roles"])
        selected_names = (
            dict(working_names)
            if working_names is not None
            else {role: f"{role}-working-copy" for role in roles}
        )
        if set(selected_names) != set(roles):
            raise RunnerFailure("Cache materialization names must cover every role exactly.")
        inventories: dict[str, ByteInventory] = {}
        for role in roles:
            validate_role(role)
            validate_working_name(str(selected_names[role]))
            template = managed_absolute(
                hit.entry_path / "notebooks" / role / "template-notebook"
            )
            if not template.is_dir():
                raise InvariantFailure(f"Notebook template path is not a directory: {template}")
            template_paths[role] = template
            working_paths[role] = managed_absolute(
                working_root / str(selected_names[role])
            )
            inventories[role] = inventory_directory(
                template,
                phase="materialize_preflight",
            )
            if inventories[role].as_dict() != hit.entry.get("role_inventories", {}).get(role):
                raise InvariantFailure(f"Cache byte inventory mismatch for role {role}.")
            if working_paths[role].exists():
                raise RunnerFailure(f"Run-scoped working path already exists for role {role}.")
        staging = self._allocate_materialize_staging(
            run_dir,
            inventories,
            template_paths,
            working_paths,
        )
        budget_evidence = self._materialize_budget_evidence(
            run_dir,
            staging,
            inventories,
            template_paths,
            working_paths,
        )
        working_root.mkdir(parents=True, exist_ok=True)
        preflight_completed = time.monotonic()
        try:
            for role in roles:
                template = template_paths[role]
                working_name = str(selected_names[role])
                working = working_paths[role]
                copied = staging / role
                shutil.copytree(template, copied)
                template_inventory = inventories[role]
                copied_inventory = inventory_directory(
                    copied,
                    phase="materialize_copy_verification",
                )
                if copied_inventory != template_inventory:
                    raise InvariantFailure(f"Working copy inventory mismatch for role {role}.")
                evidence_roles[role] = {
                    "template_path": str(template),
                    "working_name": working_name,
                    "working_path": str(working),
                    "template_inventory": template_inventory.as_dict(),
                    "working_inventory_before_open": copied_inventory.as_dict(),
                    "opened_template": False,
                }
            copy_completed = time.monotonic()
            for role in hit.entry["roles"]:
                atomic_replace_with_retry(
                    staging / role,
                    working_paths[role],
                    destination_must_be_absent=True,
                )
                published_working_paths.append(working_paths[role])
            publish_completed = time.monotonic()
            evidence = {
                "schema_version": CACHE_SCHEMA_VERSION,
                "fingerprint": hit.fingerprint,
                "template_instance_id": hit.template_instance_id,
                "decision": "validated_hit",
                "cache_origin": cache_origin,
                "roles": evidence_roles,
                "bundle_inventory_digest": hit.entry["bundle_inventory_digest"],
                "materialized_at": utc_now(),
                "templates_opened": False,
                "path_budget": budget_evidence,
                "phases_seconds": {
                    "preflight": round(preflight_completed - materialize_started, 6),
                    "copy_and_verify": round(copy_completed - preflight_completed, 6),
                    "publish_working_paths": round(publish_completed - copy_completed, 6),
                    "total": round(publish_completed - materialize_started, 6),
                },
            }
            evidence_path = run_dir / "cache-materialization.json"
            _atomic_json(
                evidence_path,
                evidence,
                phase="materialize_preflight",
                target_kind="materialize_evidence",
            )
            if staging.exists():
                shutil.rmtree(staging)
            return MaterializedBundle(
                hit.fingerprint,
                hit.template_instance_id,
                template_paths,
                working_paths,
                evidence_path,
            )
        except Exception as materialize_error:
            cleanup_failures: list[Path] = []
            for working in reversed(published_working_paths):
                if working.exists():
                    try:
                        shutil.rmtree(working)
                    except OSError:
                        cleanup_failures.append(working)
            if staging.exists():
                try:
                    shutil.rmtree(staging)
                except OSError:
                    cleanup_failures.append(staging)
            if cleanup_failures:
                retained = ", ".join(str(path) for path in cleanup_failures)
                raise RunnerFailure(
                    "Cache materialization failed and exact owned paths could not be "
                    f"removed; retained: {retained}"
                ) from materialize_error
            raise

    def _materialize_budget_paths(
        self,
        run_dir: Path,
        staging: Path,
        inventories: Mapping[str, ByteInventory],
        template_paths: Mapping[str, Path],
        working_paths: Mapping[str, Path],
    ) -> list[tuple[Path, str, str | None]]:
        evidence = run_dir / "cache-materialization.json"
        paths: list[tuple[Path, str, str | None]] = [
            (run_dir, "run_root", None),
            (evidence, "materialize_evidence", None),
            (
                evidence.with_name(f".{evidence.name}.{'0' * 16}.tmp"),
                "atomic_metadata_temp",
                None,
            ),
        ]
        for role, inventory in inventories.items():
            for relative, _length, _digest in inventory.files:
                paths.extend(
                    (
                        (
                            template_paths[role] / Path(relative),
                            "cache_template_source",
                            relative,
                        ),
                        (
                            staging / role / Path(relative),
                            "materialize_staging",
                            relative,
                        ),
                        (
                            working_paths[role] / Path(relative),
                            "working_copy",
                            relative,
                        ),
                    )
                )
        return paths

    def _materialize_budget_evidence(
        self,
        run_dir: Path,
        staging: Path,
        inventories: Mapping[str, ByteInventory],
        template_paths: Mapping[str, Path],
        working_paths: Mapping[str, Path],
    ) -> dict[str, object]:
        return preflight_paths(
            self._materialize_budget_paths(
                run_dir,
                staging,
                inventories,
                template_paths,
                working_paths,
            ),
            phase="materialize_preflight",
        )

    def _allocate_materialize_staging(
        self,
        run_dir: Path,
        inventories: Mapping[str, ByteInventory],
        template_paths: Mapping[str, Path],
        working_paths: Mapping[str, Path],
    ) -> Path:
        for _attempt in range(32):
            candidate = run_dir / f".m-{uuid.uuid4().hex[:16]}"
            if MATERIALIZE_STAGING_PATTERN.fullmatch(candidate.name) is None:
                raise InvariantFailure("Generated materialize staging name violates the typed schema.")
            self._materialize_budget_evidence(
                run_dir,
                candidate,
                inventories,
                template_paths,
                working_paths,
            )
            run_dir.mkdir(parents=True, exist_ok=True)
            try:
                candidate.mkdir()
            except FileExistsError:
                continue
            else:
                return candidate
        raise RunnerFailure("Unable to allocate a unique materialize staging directory.")

    def record_opened_working_role(
        self,
        materialized: MaterializedBundle,
        *,
        role: str,
        notebook_id: str,
        actual_path: Path,
    ) -> None:
        evidence = _read_json(materialized.evidence_path)
        roles = evidence.get("roles", {})
        if role not in roles or role not in materialized.working_paths:
            raise RunnerFailure("Opened working role is outside the materialized bundle.")
        actual = actual_path.resolve(strict=True)
        if actual != materialized.working_paths[role] or actual in set(
            materialized.template_paths.values()
        ):
            raise RunnerFailure("Materialization evidence refuses a template or mismatched open path.")
        roles[role].update(
            actual_opened_path=str(actual),
            working_notebook_id=notebook_id,
            path_assertion_passed=True,
            opened_template=False,
        )
        opened_ids = [
            value.get("working_notebook_id")
            for value in roles.values()
            if value.get("working_notebook_id")
        ]
        if len(opened_ids) != len(set(opened_ids)):
            raise RunnerFailure("Materialized roles resolved to a duplicate Notebook ID.")
        evidence["all_roles_opened"] = len(opened_ids) == len(roles)
        evidence["all_working_notebook_ids_unique"] = len(opened_ids) == len(set(opened_ids))
        _atomic_json(materialized.evidence_path, evidence)

    def verify_templates_unchanged(self, materialized: MaterializedBundle) -> dict[str, Any]:
        evidence = _read_json(materialized.evidence_path)
        checks: dict[str, Any] = {}
        for role, template_path in materialized.template_paths.items():
            before = evidence["roles"][role]["template_inventory"]
            after = inventory_directory(template_path).as_dict()
            if after != before:
                raise InvariantFailure(f"Immutable cache template changed during role {role} mutation.")
            checks[role] = {"unchanged": True, "inventory": after}
        result = {
            "schema_version": CACHE_SCHEMA_VERSION,
            "fingerprint": materialized.fingerprint,
            "template_instance_id": materialized.template_instance_id,
            "roles": checks,
            "all_templates_unchanged": True,
            "verified_at": utc_now(),
        }
        _atomic_json(materialized.evidence_path.parent / "cache-template-immutability.json", result)
        return result

    def quarantine_exact(
        self,
        recipe: RecipeBase,
        instance_id: str,
        *,
        reason: str,
        run_id: str,
    ) -> dict[str, Any]:
        """Make a failed live-validation entry unmatchable without deleting evidence."""

        path = self.instance_path(recipe.cache_fingerprint, instance_id)
        self._assert_owned_instance(recipe.cache_fingerprint, instance_id, path)
        entry_path = path / "bundle-entry.json"
        if not entry_path.exists():
            raise RunnerFailure("Cannot quarantine a missing exact cache instance.")
        entry = _read_json(entry_path)
        self._assert_entry_owned(
            recipe.cache_fingerprint,
            instance_id,
            path,
            entry,
        )
        preflight_paths(
            (
                *_atomic_budget_paths(entry_path, "cache_entry_metadata"),
                *_atomic_budget_paths(self.cache_root / "index.json", "cache_index"),
                (self.quarantine_path, "cache_quarantine_evidence", None),
            ),
            phase="cache_quarantine_preflight",
        )
        entry.update(
            state="invalid",
            invalid_reason=reason,
            invalidated_at=utc_now(),
            invalidated_by_run=run_id,
        )
        _atomic_json(entry_path, entry)
        self._update_index(
            recipe.cache_fingerprint,
            instance_id,
            "invalid",
            projection_digest=self._projection_digest(entry),
        )
        evidence = {
            "schema_version": CACHE_SCHEMA_VERSION,
            "fingerprint": recipe.cache_fingerprint,
            "template_instance_id": instance_id,
            "target": str(path),
            "state": "invalid",
            "reason": reason,
            "run_id": run_id,
            "template_deleted": False,
            "evidence_preserved": True,
            "created_at": utc_now(),
        }
        preflight_path(
            self.quarantine_path,
            phase="cache_quarantine_preflight",
            target_kind="cache_quarantine_evidence",
        )
        with self.quarantine_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(evidence, ensure_ascii=False, sort_keys=True) + "\n")
        return evidence

    def invalidate_exact(
        self,
        recipe: RecipeBase,
        instance_id: str,
        *,
        reason: str,
        open_state_probe: Callable[[Mapping[str, Any]], bool] | None = None,
    ) -> dict[str, Any]:
        path = self.instance_path(recipe.cache_fingerprint, instance_id)
        self._assert_owned_instance(recipe.cache_fingerprint, instance_id, path)
        if not path.exists():
            raise RunnerFailure("Cannot invalidate a missing exact cache instance.")
        entry_path = path / "bundle-entry.json"
        entry = _read_json(entry_path)
        self._assert_entry_owned(
            recipe.cache_fingerprint,
            instance_id,
            path,
            entry,
        )
        preflight_paths(
            (
                *_atomic_budget_paths(entry_path, "cache_entry_metadata"),
                *_atomic_budget_paths(self.cache_root / "index.json", "cache_index"),
                (self.tombstone_path, "cache_tombstone_evidence", None),
            ),
            phase="cache_invalidation_preflight",
        )
        if open_state_probe is not None and open_state_probe(entry):
            raise RunnerFailure("Cannot clean a cache instance while its template is open.")
        entry.update(state="invalid", invalid_reason=reason, invalidated_at=utc_now())
        _atomic_json(entry_path, entry)
        cleanup = {
            "schema_version": CACHE_SCHEMA_VERSION,
            "fingerprint": recipe.cache_fingerprint,
            "template_instance_id": instance_id,
            "target": str(path),
            "reason": reason,
            "root_containment": True,
            "ownership_verified": True,
            "reparse_point_free": True,
            "template_not_open": True,
            "run_lease_checked": False,
            "deleted": False,
            "created_at": utc_now(),
        }
        preflight_path(
            self.tombstone_path,
            phase="cache_invalidation_preflight",
            target_kind="cache_tombstone_evidence",
        )
        try:
            self._assert_owned_instance(recipe.cache_fingerprint, instance_id, path)
            shutil.rmtree(path)
            cleanup.update(deleted=True, completed_at=utc_now())
            self._update_index(
                recipe.cache_fingerprint,
                instance_id,
                "tombstone",
                projection_digest=self._projection_digest(entry),
            )
        except Exception as exc:
            cleanup.update(state="cleanup_failed", error=str(exc), failed_at=utc_now())
            if entry_path.exists():
                entry.update(state="cleanup_failed", cleanup_error=str(exc))
                _atomic_json(entry_path, entry)
            self._append_tombstone(cleanup)
            raise RunnerFailure("Exact fixture cache cleanup failed; rebuild is blocked.") from exc
        self._append_tombstone(cleanup)
        return cleanup

    def _append_tombstone(self, evidence: Mapping[str, Any]) -> None:
        preflight_path(
            self.tombstone_path,
            phase="cache_invalidation_preflight",
            target_kind="cache_tombstone_evidence",
        )
        self.cache_root.mkdir(parents=True, exist_ok=True)
        with self.tombstone_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(evidence, ensure_ascii=False, sort_keys=True) + "\n")


__all__ = [
    "BundleCacheStore",
    "ByteInventory",
    "CACHE_SCHEMA_VERSION",
    "CacheHit",
    "MaterializedBundle",
    "bundle_inventory",
    "inventory_directory",
]
