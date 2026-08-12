"""Fail-closed cleanup of exact runner-owned validation payloads.

These actions are deliberately separate from Scenario execution.  The module
does not import Scenario mutation, fixture construction, or restore runtimes.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import json
import msvcrt
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import time
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import unquote, urlparse
import uuid
import xml.etree.ElementTree as ET

from local_onenote_mcp.bridge import OneNoteBridge
from local_onenote_mcp.constants import HIERARCHY_SCOPES, XML_SCHEMA_2013

from ..runtime import EXIT_INVARIANT, RunnerFailure
from ..test_utils import utc_now
from ..scenarios.common.fixture_cache import (
    CACHE_SCHEMA_VERSION,
    FINGERPRINT_PATTERN,
    INSTANCE_PATTERN,
    MANAGED_MARKER,
    bundle_inventory,
    inventory_directory,
)


MAINTENANCE_COMMAND = "clear"
MAINTENANCE_ACTIONS = frozenset({"runs", "cache", "all"})
CONFIRMATIONS = {
    "clear-runs": "CLEAR-RUNS",
    "clear-cache": "CLEAR-CACHE",
    "clear-all": "CLEAR-ALL",
}
VALIDATION_MARKER = ".managed-validation-root.json"
VALIDATION_PURPOSE = "local-onenote-mcp-manual-validation"
RUN_NAME_PATTERN = re.compile(r"run-[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{2}-[0-9]{2}-[0-9]{2}")
STAGING_PATTERN = re.compile(r"\.staging-[0-9a-f]{32}")
RECEIPT_PREFIX = "cleanup-receipt-"
SUMMARY_PREFIX = "cleanup-summary-"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RunnerFailure(f"Invalid managed cleanup metadata: {path}") from exc
    if not isinstance(value, dict):
        raise RunnerFailure(f"Managed cleanup metadata must be an object: {path}")
    return value


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    existed = path.exists()
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    if path.name == VALIDATION_MARKER and not existed:
        try:
            from ctypes import WinDLL

            kernel32 = WinDLL("kernel32", use_last_error=True)
            attributes = int(kernel32.GetFileAttributesW(str(path)))
            if attributes == -1 or not kernel32.SetFileAttributesW(
                str(path), attributes | 0x2
            ):
                raise OSError("Could not hide the managed validation root marker.")
        except Exception:
            try:
                path.unlink()
            except OSError:
                pass
            raise


def _is_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & flag)


def _filesystem_path(path: Path) -> Path:
    """Use an extended Windows path for deep cache trees without changing identity."""

    resolved = path.resolve()
    text = str(resolved)
    if os.name == "nt" and not text.startswith("\\\\?\\"):
        return Path(f"\\\\?\\{text}")
    return resolved


def _remove_tree(path: Path) -> None:
    shutil.rmtree(_filesystem_path(path))


@contextmanager
def _working_open_lock(validation_root: Path, *, timeout_seconds: int = 30):
    """Serialize real cleanup with run-local working Notebook identity/open checks."""

    lock_path = validation_root / "working-notebook-open.lock"
    started = time.monotonic()
    with lock_path.open("a+b") as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        while True:
            try:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() - started >= timeout_seconds:
                    raise RunnerFailure(
                        "Another validation run is opening a working Notebook; cleanup refused."
                    )
                time.sleep(0.05)
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def _plain_tree(root: Path) -> tuple[bool, str | None]:
    filesystem_root = _filesystem_path(root)
    try:
        if _is_reparse_point(filesystem_root):
            return False, str(root)
        for current, directories, files in os.walk(filesystem_root, followlinks=False):
            current_path = Path(current)
            for name in (*directories, *files):
                candidate = current_path / name
                if _is_reparse_point(candidate):
                    return False, str(candidate)
    except OSError as exc:
        return False, f"{type(exc).__name__}: {exc}"
    return True, None


def _canonical_path(value: str | Path) -> Path:
    text = str(value)
    if text.casefold().startswith("file:"):
        parsed = urlparse(text)
        if parsed.netloc:
            text = f"//{parsed.netloc}{unquote(parsed.path)}"
        else:
            text = unquote(parsed.path)
            if re.match(r"^/[A-Za-z]:/", text):
                text = text[1:]
    path = Path(text).resolve()
    if path.suffix.casefold() == ".onetoc2":
        path = path.parent
    return path


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left.resolve())) == os.path.normcase(str(right.resolve()))


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class OpenNotebookPathSnapshot:
    """One read-only snapshot of currently open Notebook IDs and local paths."""

    status: str
    notebooks: tuple[tuple[str, Path], ...] = ()
    error: str | None = None

    @classmethod
    def capture(cls, *, timeout_seconds: int = 30) -> "OpenNotebookPathSnapshot":
        try:
            bridge = OneNoteBridge(timeout_seconds=timeout_seconds)
            result = bridge.call(
                "get_hierarchy",
                start_id="",
                scope=HIERARCHY_SCOPES["notebooks"],
                schema=XML_SCHEMA_2013,
            )
            root = ET.fromstring(str(result["xml"]))
            notebooks: list[tuple[str, Path]] = []
            for node in root.iter():
                if node.tag.rsplit("}", 1)[-1] != "Notebook":
                    continue
                notebook_id = str(node.attrib.get("ID", ""))
                reported_path = str(node.attrib.get("path", ""))
                if not notebook_id or not reported_path:
                    return cls(
                        "failed",
                        tuple(notebooks),
                        "An open Notebook omitted its exact ID or local path.",
                    )
                notebooks.append((notebook_id, _canonical_path(reported_path)))
            return cls("complete", tuple(notebooks))
        except Exception as exc:
            return cls("failed", error=f"{type(exc).__name__}: {exc}")

    def any_within(self, root: Path) -> list[dict[str, str]]:
        return [
            {"notebook_id": notebook_id, "path": str(path)}
            for notebook_id, path in self.notebooks
            if _inside(path, root)
        ]

    def any_exact(self, paths: Iterable[Path]) -> list[dict[str, str]]:
        exact = tuple(paths)
        return [
            {"notebook_id": notebook_id, "path": str(path)}
            for notebook_id, path in self.notebooks
            if any(_same_path(path, candidate) for candidate in exact)
        ]

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "count": len(self.notebooks),
            "notebooks": [
                {"notebook_id": notebook_id, "path": str(path)}
                for notebook_id, path in self.notebooks
            ],
            "error": self.error,
            "read_only": True,
            "close_performed": False,
        }


@dataclass
class CleanupAssessment:
    kind: str
    target: Path
    identity: dict[str, Any]
    checks: dict[str, Any]
    decision: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "target": str(self.target),
            "identity": self.identity,
            "checks": self.checks,
            "decision": self.decision,
            "reason": self.reason,
        }


def register_maintenance_parsers(subparsers: Any) -> None:
    clear = subparsers.add_parser(
        MAINTENANCE_COMMAND,
        help="Inspect or clear exact runner-owned local validation payloads.",
    )
    actions = clear.add_subparsers(dest="clear_action", required=True)
    for subaction in ("runs", "cache", "all"):
        action = f"clear-{subaction}"
        parser = actions.add_parser(
            subaction,
            help=f"Inspect or clear managed {subaction} payloads.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Read metadata and the current OneNote open-path snapshot without writing or deleting.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            dest="json_output",
            help="Print stable JSON only.",
        )


def _require_interactive_confirmation(
    action: str,
    *,
    planned_count: int,
    residue_count: int,
    reader: Callable[[str], str] | None,
) -> None:
    expected = CONFIRMATIONS[action]
    prompt = (
        f"{action} is ready to delete {planned_count} exact managed target(s). "
        f"It will also compact/prune {residue_count} verified residue item(s). "
        f"Type {expected} to continue: "
    )
    if reader is None:
        if not sys.stdin.isatty():
            raise RunnerFailure(
                "Real clear requires an interactive terminal; piped or redirected stdin is refused."
            )
        sys.stderr.write(prompt)
        sys.stderr.flush()
        response = sys.stdin.readline()
        if response == "":
            raise RunnerFailure("Interactive clear confirmation reached EOF; nothing was deleted.")
    else:
        response = reader(prompt)
    if str(response).strip() != expected:
        raise RunnerFailure(
            f"Interactive confirmation did not match {expected}; nothing was deleted."
        )


def _validate_root(
    validation_root: Path,
    *,
    workspace_root: Path | None = None,
) -> tuple[Path, Path, dict[str, Any]]:
    workspace_root = (workspace_root or Path(__file__).resolve().parents[3]).resolve()
    expected = (workspace_root / ".local-validation").resolve()
    resolved = validation_root.resolve()
    checks: dict[str, Any] = {
        "fixed_repository_root": _same_path(resolved, expected),
        "not_filesystem_root": resolved.parent != resolved,
        "not_workspace_root": not _same_path(resolved, workspace_root),
        "root_reparse_point_free": True,
    }
    if resolved.exists():
        try:
            checks["root_reparse_point_free"] = not _is_reparse_point(resolved)
        except OSError:
            checks["root_reparse_point_free"] = False
    if not all(checks.values()):
        raise RunnerFailure("Managed validation root failed fixed-root safety checks.")
    marker = resolved / VALIDATION_MARKER
    if marker.exists():
        value = _read_json(marker)
        checks["root_marker_valid"] = (
            value.get("schema_version") == 1
            and value.get("purpose") == VALIDATION_PURPOSE
        )
        if not checks["root_marker_valid"]:
            raise RunnerFailure("Managed validation root marker is invalid.")
        try:
            attributes = getattr(marker.stat(), "st_file_attributes", 0)
        except OSError:
            attributes = 0
        checks["root_marker_hidden"] = bool(attributes & 0x2)
        if not checks["root_marker_hidden"]:
            raise RunnerFailure("Managed validation root marker is not hidden.")
    else:
        checks["root_marker_valid"] = None
        checks["root_marker_hidden"] = None
        checks["historical_metadata_mode"] = True
    return resolved, workspace_root, checks


def _assess_run(
    target: Path,
    *,
    validation_root: Path,
    workspace_root: Path,
    snapshot: OpenNotebookPathSnapshot,
) -> CleanupAssessment:
    target = target.resolve()
    checks: dict[str, Any] = {
        "direct_run_child": (
            target.parent == validation_root
            and RUN_NAME_PATTERN.fullmatch(target.name) is not None
        ),
        "not_validation_root": not _same_path(target, validation_root),
        "not_cache_root": not _same_path(target, validation_root / "fixture-cache"),
        "not_workspace_root": not _same_path(target, workspace_root),
        "snapshot_complete": snapshot.status == "complete",
    }
    plain, reparse = _plain_tree(target)
    checks["plain_tree"] = plain
    checks["reparse_point"] = reparse
    metadata_path = target / "run-state.json"
    checks["run_state_present"] = metadata_path.is_file()
    state: dict[str, Any] | None = None
    if checks["run_state_present"]:
        try:
            state = _read_json(metadata_path)
        except RunnerFailure:
            state = None
    checks["run_state_owned"] = bool(
        state
        and state.get("schema_version") == 1
        and state.get("human_only") is True
        and state.get("agent_execution_prohibited") is True
        and isinstance(state.get("command"), str)
        and state.get("command") == state.get("scenario")
        and _same_path(_canonical_path(str(state.get("run_dir", ""))), target)
    )
    result_present = (target / "run-result.json").is_file()
    failure_present = (target / "run-failure.json").is_file()
    checks["terminal_metadata"] = {
        "run_result": result_present,
        "run_failure": failure_present,
        "in_progress_or_preserved_allowed": not result_present and not failure_present,
    }
    open_matches = snapshot.any_within(target) if snapshot.status == "complete" else []
    checks["open_notebooks"] = open_matches
    checks["not_open"] = snapshot.status == "complete" and not open_matches
    if not checks["direct_run_child"]:
        reason = "refused_outside_exact_run_root"
    elif not plain:
        reason = "refused_reparse_point"
    elif not checks["run_state_owned"]:
        reason = "refused_unowned"
    elif snapshot.status != "complete":
        reason = "refused_open_snapshot_failed"
    elif open_matches:
        reason = "refused_open"
    else:
        reason = "all_run_cleanup_checks_passed"
    return CleanupAssessment(
        "run",
        target,
        {"run_id": target.name},
        checks,
        "would_delete" if reason == "all_run_cleanup_checks_passed" else "refused",
        reason,
    )


def _load_cache_index(cache_root: Path) -> tuple[dict[str, Any], str | None]:
    path = cache_root / "index.json"
    if not path.exists():
        return {"schema_version": CACHE_SCHEMA_VERSION, "entries": {}}, None
    try:
        index = _read_json(path)
    except RunnerFailure as exc:
        return {}, str(exc)
    if index.get("schema_version") != CACHE_SCHEMA_VERSION or not isinstance(
        index.get("entries"), dict
    ):
        return {}, "Cache index schema or entries are invalid."
    return index, None


def _disk_cache_identities(cache_root: Path) -> tuple[set[tuple[str, str]], list[Path]]:
    identities: set[tuple[str, str]] = set()
    unknown: list[Path] = []
    if not cache_root.exists():
        return identities, unknown
    for fingerprint_root in cache_root.iterdir():
        if fingerprint_root.name in {
            MANAGED_MARKER,
            "index.json",
            "cleanup-tombstones.jsonl",
            "quarantine-evidence.jsonl",
            "recovery-evidence.jsonl",
            "working-leases",
        } or STAGING_PATTERN.fullmatch(fingerprint_root.name):
            continue
        if not fingerprint_root.is_dir() or FINGERPRINT_PATTERN.fullmatch(
            fingerprint_root.name
        ) is None:
            unknown.append(fingerprint_root)
            continue
        for child in fingerprint_root.iterdir():
            if child.name not in {"instances", "bundle.lock.json"}:
                unknown.append(child)
        instances = fingerprint_root / "instances"
        if not instances.exists():
            continue
        if not instances.is_dir():
            unknown.append(instances)
            continue
        for instance in instances.iterdir():
            if instance.is_dir() and INSTANCE_PATTERN.fullmatch(instance.name):
                identities.add((fingerprint_root.name, instance.name))
            else:
                unknown.append(instance)
    return identities, unknown


def _assess_cache_entry(
    fingerprint: str,
    instance_id: str,
    *,
    cache_root: Path,
    index: Mapping[str, Any],
    index_error: str | None,
    snapshot: OpenNotebookPathSnapshot,
) -> CleanupAssessment:
    target = (cache_root / fingerprint / "instances" / instance_id).resolve()
    key = f"{fingerprint}:{instance_id}"
    index_entry = index.get("entries", {}).get(key) if not index_error else None
    typed_identity = (
        FINGERPRINT_PATTERN.fullmatch(fingerprint) is not None
        and INSTANCE_PATTERN.fullmatch(instance_id) is not None
    )
    exact_typed_path = (
        typed_identity
        and target.parent.name == "instances"
        and _inside(target, cache_root)
    )
    checks: dict[str, Any] = {
        "typed_identity": typed_identity,
        "exact_typed_path": exact_typed_path,
        "not_cache_root": not _same_path(target, cache_root),
        "target_exists": exact_typed_path and target.is_dir(),
        "index_valid": index_error is None,
        "index_entry_present": isinstance(index_entry, dict),
        "snapshot_complete": snapshot.status == "complete",
    }
    plain, reparse = (
        _plain_tree(target) if exact_typed_path and target.exists() else (False, None)
    )
    checks["plain_tree"] = plain
    checks["reparse_point"] = reparse
    entry: dict[str, Any] | None = None
    entry_path = target / "bundle-entry.json"
    if exact_typed_path and entry_path.is_file():
        try:
            entry = _read_json(entry_path)
        except RunnerFailure:
            entry = None
    checks["entry_metadata_owned"] = bool(
        entry
        and entry.get("schema_version") == CACHE_SCHEMA_VERSION
        and entry.get("fingerprint") == fingerprint
        and entry.get("template_instance_id") == instance_id
        and isinstance(entry.get("roles"), list)
        and bool(entry.get("roles"))
    )
    checks["index_identity_owned"] = bool(
        isinstance(index_entry, dict)
        and index_entry.get("fingerprint") == fingerprint
        and index_entry.get("template_instance_id") == instance_id
        and entry is not None
        and index_entry.get("state") == entry.get("state")
    )
    template_paths: list[Path] = []
    inventory_valid = checks["entry_metadata_owned"]
    if entry is not None and checks["entry_metadata_owned"]:
        inventories = {}
        for role in entry["roles"]:
            role_value = entry.get("role_entries", {}).get(role)
            expected = target / "notebooks" / str(role) / "template-notebook"
            if (
                not isinstance(role_value, dict)
                or not _filesystem_path(expected).is_dir()
                or not _same_path(
                    _canonical_path(str(role_value.get("template_path", ""))), expected
                )
            ):
                inventory_valid = False
                continue
            template_paths.append(expected.resolve())
            try:
                observed = inventory_directory(_filesystem_path(expected))
            except (OSError, RunnerFailure):
                inventory_valid = False
                continue
            if entry.get("role_inventories", {}).get(role) != observed.as_dict():
                inventory_valid = False
            inventories[str(role)] = observed
        if inventories and entry.get("bundle_inventory_digest") != bundle_inventory(inventories):
            inventory_valid = False
    checks["inventory_valid"] = inventory_valid and bool(template_paths)
    open_matches = snapshot.any_exact(template_paths) if snapshot.status == "complete" else []
    checks["template_paths"] = [str(path) for path in template_paths]
    checks["open_templates"] = open_matches
    checks["template_not_open"] = snapshot.status == "complete" and not open_matches
    if not checks["typed_identity"] or not checks["exact_typed_path"]:
        reason = "refused_outside_exact_cache_entry"
    elif not target.exists() or not checks["index_entry_present"]:
        reason = "refused_index_disk_mismatch"
    elif not plain:
        reason = "refused_reparse_point"
    elif not checks["entry_metadata_owned"] or not checks["index_identity_owned"]:
        reason = "refused_unowned"
    elif not checks["inventory_valid"]:
        reason = "refused_inventory_mismatch"
    elif snapshot.status != "complete":
        reason = "refused_open_snapshot_failed"
    elif open_matches:
        reason = "refused_open"
    else:
        reason = "all_cache_entry_cleanup_checks_passed"
    return CleanupAssessment(
        "cache_entry",
        target,
        {"fingerprint": fingerprint, "template_instance_id": instance_id},
        checks,
        "would_delete" if reason == "all_cache_entry_cleanup_checks_passed" else "refused",
        reason,
    )


def _assess_special_cache_target(
    target: Path,
    *,
    cache_root: Path,
    kind: str,
    snapshot: OpenNotebookPathSnapshot,
) -> CleanupAssessment:
    target = target.resolve()
    checks: dict[str, Any] = {
        "direct_cache_child": target.parent == cache_root,
        "not_cache_root": not _same_path(target, cache_root),
        "snapshot_complete": snapshot.status == "complete",
    }
    plain, reparse = _plain_tree(target)
    checks["plain_tree"] = plain
    checks["reparse_point"] = reparse
    if kind == "legacy_working_leases":
        checks["owned_name"] = target.name == "working-leases"
        open_matches: list[dict[str, str]] = []
    else:
        checks["owned_name"] = STAGING_PATTERN.fullmatch(target.name) is not None
        entry_path = target / "bundle-entry.json"
        entry = _read_json(entry_path) if entry_path.is_file() else None
        checks["staging_entry_owned"] = bool(
            entry
            and entry.get("schema_version") == CACHE_SCHEMA_VERSION
            and FINGERPRINT_PATTERN.fullmatch(str(entry.get("fingerprint", "")))
            and INSTANCE_PATTERN.fullmatch(str(entry.get("template_instance_id", "")))
            and isinstance(entry.get("roles"), list)
            and entry.get("roles")
        )
        template_paths = [
            target / "notebooks" / str(role) / "template-notebook"
            for role in (entry or {}).get("roles", [])
        ]
        checks["template_paths_exist"] = bool(template_paths) and all(
            path.is_dir() for path in template_paths
        )
        open_matches = snapshot.any_exact(template_paths) if snapshot.status == "complete" else []
    checks["open_templates"] = open_matches
    checks["template_not_open"] = snapshot.status == "complete" and not open_matches
    owned = checks["owned_name"] and (
        kind == "legacy_working_leases"
        or (checks.get("staging_entry_owned") and checks.get("template_paths_exist"))
    )
    if not checks["direct_cache_child"] or not owned:
        reason = "refused_unowned"
    elif not plain:
        reason = "refused_reparse_point"
    elif snapshot.status != "complete":
        reason = "refused_open_snapshot_failed"
    elif open_matches:
        reason = "refused_open"
    else:
        reason = "all_special_cache_cleanup_checks_passed"
    return CleanupAssessment(
        kind,
        target,
        {"name": target.name},
        checks,
        "would_delete" if reason == "all_special_cache_cleanup_checks_passed" else "refused",
        reason,
    )


def _discover(
    action: str,
    *,
    validation_root: Path,
    workspace_root: Path,
    snapshot: OpenNotebookPathSnapshot,
) -> tuple[list[CleanupAssessment], dict[str, Any] | None, str | None]:
    assessments: list[CleanupAssessment] = []
    index: dict[str, Any] | None = None
    index_error: str | None = None
    if action in {"clear-runs", "clear-all"} and validation_root.exists():
        for target in sorted(validation_root.iterdir(), key=lambda path: path.name):
            if target.is_dir() and target.name.startswith("run-"):
                assessments.append(
                    _assess_run(
                        target,
                        validation_root=validation_root,
                        workspace_root=workspace_root,
                        snapshot=snapshot,
                    )
                )
    if action in {"clear-cache", "clear-all"}:
        cache_root = validation_root / "fixture-cache"
        if cache_root.exists():
            marker_path = cache_root / MANAGED_MARKER
            marker_valid = False
            if marker_path.is_file():
                marker = _read_json(marker_path)
                marker_valid = (
                    marker.get("schema_version") == CACHE_SCHEMA_VERSION
                    and marker.get("purpose") == "local-onenote-mcp-fixture-cache"
                )
            if not marker_valid or _is_reparse_point(cache_root):
                assessments.append(
                    CleanupAssessment(
                        "cache_root",
                        cache_root,
                        {},
                        {"managed_marker_valid": marker_valid, "reparse_point_free": False},
                        "refused",
                        "refused_unmanaged_cache_root",
                    )
                )
                return assessments, None, "Managed cache root is invalid."
            index, index_error = _load_cache_index(cache_root)
            disk_identities, unknown = _disk_cache_identities(cache_root)
            index_identities: set[tuple[str, str]] = set()
            if index_error is None:
                for value in index.get("entries", {}).values():
                    if not isinstance(value, dict):
                        continue
                    fingerprint = str(value.get("fingerprint", ""))
                    instance_id = str(value.get("template_instance_id", ""))
                    if value.get("state") != "tombstone":
                        index_identities.add((fingerprint, instance_id))
            for fingerprint, instance_id in sorted(disk_identities | index_identities):
                assessments.append(
                    _assess_cache_entry(
                        fingerprint,
                        instance_id,
                        cache_root=cache_root,
                        index=index or {},
                        index_error=index_error,
                        snapshot=snapshot,
                    )
                )
            for target in unknown:
                assessments.append(
                    CleanupAssessment(
                        "cache_unknown",
                        target.resolve(),
                        {},
                        {"owned": False},
                        "refused",
                        "refused_unowned",
                    )
                )
            legacy = cache_root / "working-leases"
            if legacy.exists():
                assessments.append(
                    _assess_special_cache_target(
                        legacy,
                        cache_root=cache_root,
                        kind="legacy_working_leases",
                        snapshot=snapshot,
                    )
                )
            for staging in sorted(cache_root.glob(".staging-*"), key=lambda path: path.name):
                assessments.append(
                    _assess_special_cache_target(
                        staging,
                        cache_root=cache_root,
                        kind="cache_staging",
                        snapshot=snapshot,
                    )
                )
    return assessments, index, index_error


def _ensure_validation_marker(validation_root: Path) -> None:
    marker = validation_root / VALIDATION_MARKER
    if marker.exists():
        return
    _atomic_json(
        marker,
        {
            "schema_version": 1,
            "purpose": VALIDATION_PURPOSE,
            "created_at": utc_now(),
            "local_only": True,
        },
    )


def _delete_assessment(
    assessment: CleanupAssessment,
    *,
    action: str,
    validation_root: Path,
    delete_tree: Callable[[Path], None],
) -> tuple[str, str]:
    receipt_id = uuid.uuid4().hex
    receipt_path = validation_root / f"{RECEIPT_PREFIX}{receipt_id}.json"
    receipt = {
        "schema_version": 1,
        "receipt_id": receipt_id,
        "action": action,
        "status": "pending",
        "target": assessment.as_dict(),
        "created_at": utc_now(),
    }
    try:
        _atomic_json(receipt_path, receipt)
    except Exception as exc:
        return "failed", f"pending_receipt_failed: {type(exc).__name__}: {exc}"
    try:
        delete_tree(assessment.target)
    except Exception as exc:
        receipt.update(status="failed", error=f"{type(exc).__name__}: {exc}", failed_at=utc_now())
        try:
            _atomic_json(receipt_path, receipt)
        except Exception:
            pass
        return "failed", str(receipt_path)
    receipt.update(status="deleted", deleted_at=utc_now())
    try:
        _atomic_json(receipt_path, receipt)
    except Exception as exc:
        return "failed", f"final_receipt_failed: {type(exc).__name__}: {exc}"
    return "deleted", str(receipt_path)


def _rebuild_cache_index(
    validation_root: Path,
    index: dict[str, Any] | None,
    deleted: Iterable[CleanupAssessment],
) -> int:
    if index is None:
        return 0
    entries = dict(index.get("entries", {}))
    removed_keys: set[str] = set()
    for assessment in deleted:
        if assessment.kind != "cache_entry":
            continue
        key = (
            f"{assessment.identity['fingerprint']}:"
            f"{assessment.identity['template_instance_id']}"
        )
        if key in entries:
            removed_keys.add(key)
    cache_root = validation_root / "fixture-cache"
    for key, value in entries.items():
        if not isinstance(value, dict) or value.get("state") != "tombstone":
            continue
        fingerprint = str(value.get("fingerprint", ""))
        instance_id = str(value.get("template_instance_id", ""))
        if (
            FINGERPRINT_PATTERN.fullmatch(fingerprint)
            and INSTANCE_PATTERN.fullmatch(instance_id)
            and not (cache_root / fingerprint / "instances" / instance_id).exists()
        ):
            removed_keys.add(key)
    if removed_keys:
        for key in removed_keys:
            entries.pop(key, None)
        index["entries"] = entries
        _atomic_json(validation_root / "fixture-cache" / "index.json", index)
    return len(removed_keys)


def _cache_scaffold_plan(cache_root: Path) -> list[Path]:
    """Return only typed cache directories that become empty leaf-by-leaf."""

    planned: list[Path] = []
    if not cache_root.is_dir():
        return planned
    for fingerprint_root in sorted(cache_root.iterdir(), key=lambda path: path.name):
        if (
            not fingerprint_root.is_dir()
            or FINGERPRINT_PATTERN.fullmatch(fingerprint_root.name) is None
            or _is_reparse_point(fingerprint_root)
        ):
            continue
        children = list(fingerprint_root.iterdir())
        if not children:
            planned.append(fingerprint_root)
            continue
        if len(children) != 1 or children[0].name != "instances":
            continue
        instances = children[0]
        if (
            not instances.is_dir()
            or _is_reparse_point(instances)
            or any(instances.iterdir())
        ):
            continue
        planned.extend((instances, fingerprint_root))
    return planned


def _prune_empty_cache_scaffolds(paths: Iterable[Path]) -> tuple[list[str], list[str]]:
    pruned: list[str] = []
    failures: list[str] = []
    for path in paths:
        try:
            path.rmdir()
            pruned.append(str(path))
        except FileNotFoundError:
            continue
        except OSError as exc:
            failures.append(f"{path}: {type(exc).__name__}: {exc}")
    return pruned, failures


def _receipt_compaction_plan(
    validation_root: Path,
) -> tuple[list[tuple[Path, list[Path], list[str]]], list[str]]:
    """Find deleted receipts whose full result is embedded in a durable summary."""

    groups: list[tuple[Path, list[Path], list[str]]] = []
    failures: list[str] = []
    if not validation_root.is_dir():
        return groups, failures
    for summary_path in sorted(validation_root.glob(f"{SUMMARY_PREFIX}*.json")):
        if summary_path.parent.resolve() != validation_root:
            continue
        try:
            summary = _read_json(summary_path)
        except RunnerFailure as exc:
            failures.append(str(exc))
            continue
        action = str(summary.get("action", ""))
        targets = {
            str(target.get("target"))
            for target in summary.get("targets", [])
            if isinstance(target, dict) and target.get("decision") == "deleted"
        }
        eligible: list[Path] = []
        retained: list[str] = []
        for raw_path in summary.get("receipt_paths", []):
            receipt_path = Path(str(raw_path)).resolve()
            if (
                receipt_path.parent != validation_root
                or not receipt_path.name.startswith(RECEIPT_PREFIX)
                or not receipt_path.name.endswith(".json")
            ):
                retained.append(str(raw_path))
                continue
            if not receipt_path.exists():
                continue
            try:
                receipt = _read_json(receipt_path)
            except RunnerFailure:
                retained.append(str(receipt_path))
                continue
            receipt_target = receipt.get("target", {})
            if (
                receipt.get("schema_version") == 1
                and receipt.get("status") == "deleted"
                and receipt.get("action") == action
                and isinstance(receipt_target, dict)
                and str(receipt_target.get("target")) in targets
            ):
                eligible.append(receipt_path)
            else:
                retained.append(str(receipt_path))
        if eligible or retained != list(summary.get("receipt_paths", [])):
            groups.append((summary_path, eligible, retained))
    return groups, failures


def _compact_receipts(
    groups: Iterable[tuple[Path, list[Path], list[str]]],
) -> tuple[list[str], list[str]]:
    compacted: list[str] = []
    failures: list[str] = []
    for summary_path, eligible, retained in groups:
        removed_for_summary: list[str] = []
        for receipt_path in eligible:
            try:
                receipt_path.unlink()
                removed_for_summary.append(str(receipt_path))
                compacted.append(str(receipt_path))
            except FileNotFoundError:
                continue
            except OSError as exc:
                retained.append(str(receipt_path))
                failures.append(f"{receipt_path}: {type(exc).__name__}: {exc}")
        try:
            summary = _read_json(summary_path)
            summary["receipt_paths"] = sorted(set(retained))
            summary["receipt_compaction"] = {
                "compacted_count": len(removed_for_summary),
                "remaining_count": len(summary["receipt_paths"]),
                "target_evidence_embedded": True,
                "compacted_at": utc_now(),
            }
            _atomic_json(summary_path, summary)
        except Exception as exc:
            failures.append(f"{summary_path}: {type(exc).__name__}: {exc}")
    return compacted, failures


def run_maintenance(
    args: argparse.Namespace,
    *,
    validation_root: Path | None = None,
    workspace_root: Path | None = None,
    snapshot: OpenNotebookPathSnapshot | None = None,
    delete_tree: Callable[[Path], None] = _remove_tree,
    confirmation_reader: Callable[[str], str] | None = None,
) -> tuple[dict[str, Any], int]:
    if str(args.command) != MAINTENANCE_COMMAND or str(args.clear_action) not in MAINTENANCE_ACTIONS:
        raise RunnerFailure("Unknown maintenance action.")
    action = f"clear-{args.clear_action}"
    dry_run = bool(args.dry_run)
    selected_root = validation_root or (Path(__file__).resolve().parents[3] / ".local-validation")
    resolved_root, workspace_root, root_checks = _validate_root(
        selected_root,
        workspace_root=workspace_root,
    )
    if dry_run:
        return _run_maintenance_locked(
            action=action,
            dry_run=True,
            resolved_root=resolved_root,
            workspace_root=workspace_root,
            root_checks=root_checks,
            snapshot=snapshot,
            delete_tree=delete_tree,
            confirmation_reader=confirmation_reader,
        )
    resolved_root.mkdir(parents=False, exist_ok=True)
    with _working_open_lock(resolved_root):
        return _run_maintenance_locked(
            action=action,
            dry_run=False,
            resolved_root=resolved_root,
            workspace_root=workspace_root,
            root_checks=root_checks,
            snapshot=snapshot,
            delete_tree=delete_tree,
            confirmation_reader=confirmation_reader,
        )


def _run_maintenance_locked(
    *,
    action: str,
    dry_run: bool,
    resolved_root: Path,
    workspace_root: Path,
    root_checks: dict[str, Any],
    snapshot: OpenNotebookPathSnapshot | None,
    delete_tree: Callable[[Path], None],
    confirmation_reader: Callable[[str], str] | None,
) -> tuple[dict[str, Any], int]:
    open_snapshot = snapshot or OpenNotebookPathSnapshot.capture()
    assessments, index, index_error = _discover(
        action,
        validation_root=resolved_root,
        workspace_root=workspace_root,
        snapshot=open_snapshot,
    )
    receipt_groups, receipt_plan_failures = _receipt_compaction_plan(resolved_root)
    receipt_files_eligible = sum(len(group[1]) for group in receipt_groups)
    scaffold_plan = (
        _cache_scaffold_plan(resolved_root / "fixture-cache")
        if action in {"clear-cache", "clear-all"}
        else []
    )
    index_tombstones_eligible = 0
    if action in {"clear-cache", "clear-all"} and index is not None:
        cache_root = resolved_root / "fixture-cache"
        for value in index.get("entries", {}).values():
            if not isinstance(value, dict) or value.get("state") != "tombstone":
                continue
            fingerprint = str(value.get("fingerprint", ""))
            instance_id = str(value.get("template_instance_id", ""))
            if (
                FINGERPRINT_PATTERN.fullmatch(fingerprint)
                and INSTANCE_PATTERN.fullmatch(instance_id)
                and not (cache_root / fingerprint / "instances" / instance_id).exists()
            ):
                index_tombstones_eligible += 1
    finalization_plan = {
        "successful_receipts_eligible": receipt_files_eligible,
        "empty_cache_directories_eligible": len(scaffold_plan),
        "cache_index_tombstones_eligible": index_tombstones_eligible,
        "assessment_failures": receipt_plan_failures,
    }
    deleted: list[CleanupAssessment] = []
    receipts: list[str] = []
    index_entries_removed = 0
    pruned_directories: list[str] = []
    finalization_failures = list(receipt_plan_failures)
    if not dry_run:
        planned_before_confirmation = sum(
            assessment.decision == "would_delete" for assessment in assessments
        )
        _require_interactive_confirmation(
            action,
            planned_count=planned_before_confirmation,
            residue_count=(
                receipt_files_eligible
                + len(scaffold_plan)
                + index_tombstones_eligible
            ),
            reader=confirmation_reader,
        )
        _ensure_validation_marker(resolved_root)
        for assessment in assessments:
            if assessment.decision != "would_delete":
                continue
            status, detail = _delete_assessment(
                assessment,
                action=action,
                validation_root=resolved_root,
                delete_tree=delete_tree,
            )
            if status == "deleted":
                assessment.decision = "deleted"
                assessment.reason = "deleted_after_all_checks_passed"
                deleted.append(assessment)
                receipts.append(detail)
            else:
                assessment.decision = "failed"
                assessment.reason = detail
        try:
            index_entries_removed = _rebuild_cache_index(resolved_root, index, deleted)
        except Exception as exc:
            assessments.append(
                CleanupAssessment(
                    "cache_index",
                    resolved_root / "fixture-cache" / "index.json",
                    {},
                    {"index_error_before": index_error},
                    "failed",
                    f"index_rebuild_failed: {type(exc).__name__}: {exc}",
                )
            )
        if action in {"clear-cache", "clear-all"}:
            # Recompute after target deletion so newly empty typed scaffolds are included.
            pruned_directories, prune_failures = _prune_empty_cache_scaffolds(
                _cache_scaffold_plan(resolved_root / "fixture-cache")
            )
            finalization_failures.extend(prune_failures)
    refused = [item for item in assessments if item.decision == "refused"]
    failed = [item for item in assessments if item.decision == "failed"]
    planned = [item for item in assessments if item.decision == "would_delete"]
    ok = not refused and not failed and not finalization_failures
    result: dict[str, Any] = {
        "action": action,
        "dry_run": dry_run,
        "human_confirmation_required": not dry_run,
        "confirmation_mode": "interactive_stdin" if not dry_run else None,
        "confirmation_value_recorded": False,
        "managed_roots": {
            "validation": str(resolved_root),
            "cache": str(resolved_root / "fixture-cache"),
            "workspace": str(workspace_root),
        },
        "root_checks": root_checks,
        "open_path_snapshot": open_snapshot.as_dict(),
        "counts": {
            "discovered": len(assessments),
            "planned": len(planned),
            "deleted": len(deleted),
            "refused": len(refused),
            "failed": len(failed),
        },
        "targets": [assessment.as_dict() for assessment in assessments],
        "receipt_paths": receipts,
        "finalization_plan": finalization_plan,
        "finalization": {
            "receipts_compacted": 0,
            "empty_cache_directories_pruned": len(pruned_directories),
            "pruned_cache_directories": pruned_directories,
            "cache_index_entries_removed": index_entries_removed,
            "failures": list(finalization_failures),
        },
        "validation_root_deleted": False,
        "cache_marker_deleted": False,
        "ok": ok,
    }
    if not dry_run:
        summary_path = resolved_root / f"{SUMMARY_PREFIX}{uuid.uuid4().hex}.json"
        summary = {
            "schema_version": 1,
            **result,
            "created_at": utc_now(),
            "summary_path": str(summary_path),
        }
        try:
            _atomic_json(summary_path, summary)
            result["summary_tombstone_path"] = str(summary_path)
            current_groups, plan_failures = _receipt_compaction_plan(resolved_root)
            compacted, compaction_failures = _compact_receipts(current_groups)
            finalization_failures.extend(plan_failures)
            finalization_failures.extend(compaction_failures)
            finalization_failures = list(dict.fromkeys(finalization_failures))
            result["receipt_paths"] = [path for path in receipts if Path(path).exists()]
            result["finalization"].update(
                receipts_compacted=len(compacted),
                failures=list(finalization_failures),
            )
            if finalization_failures:
                result["ok"] = False
                result["counts"]["failed"] += len(finalization_failures)
            durable = _read_json(summary_path)
            durable.update(result)
            durable["summary_path"] = str(summary_path)
            _atomic_json(summary_path, durable)
        except Exception as exc:
            result["ok"] = False
            result["summary_tombstone_error"] = f"{type(exc).__name__}: {exc}"
            result["counts"]["failed"] += 1
    return result, 0 if result["ok"] else EXIT_INVARIANT


__all__ = [
    "MAINTENANCE_ACTIONS",
    "MAINTENANCE_COMMAND",
    "CleanupAssessment",
    "OpenNotebookPathSnapshot",
    "register_maintenance_parsers",
    "run_maintenance",
]
