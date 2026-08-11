"""Local-only opaque Notebook bundle cache with exact-path safety gates.

This module never opens OneNote and never interprets ``.one`` files.  It only
copies bytes belonging to closed, disposable Notebook directories created by
the manual-validation runner.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
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

from ...runtime import InvariantFailure, RunnerFailure
from ...test_utils import utc_now
from ..fixture_recipes.recipe_base import RecipeBase


CACHE_SCHEMA_VERSION = 1
MANAGED_MARKER = ".managed-fixture-cache.json"
FINGERPRINT_PATTERN = re.compile(r"[0-9a-f]{64}")
INSTANCE_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")
EXACT_ENTRY_STATES = frozenset({"ready", "evidence_only", "invalid", "cleanup_failed"})


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


def _assert_plain_tree(root: Path) -> None:
    if _is_reparse_point(root):
        raise InvariantFailure(f"Managed cache path is a reparse point: {root}")
    for current, directories, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in (*directories, *files):
            candidate = current_path / name
            if _is_reparse_point(candidate):
                raise InvariantFailure(f"Managed cache tree contains a reparse point: {candidate}")


def inventory_directory(root: Path) -> ByteInventory:
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise InvariantFailure(f"Notebook template path is not a directory: {root}")
    _assert_plain_tree(root)
    files: list[tuple[str, int, str]] = []
    for path in sorted((item for item in root.rglob("*") if item.is_file()), key=str):
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


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InvariantFailure(f"Invalid fixture cache metadata: {path}") from exc
    if not isinstance(value, dict):
        raise InvariantFailure(f"Fixture cache metadata must be an object: {path}")
    return value


class BundleCacheStore:
    """Own one configured cache root; callers cannot supply cache entry paths."""

    def __init__(self, cache_root: Path) -> None:
        self.cache_root = cache_root.resolve()
        self.marker_path = self.cache_root / MANAGED_MARKER
        self.lease_root = self.cache_root / "working-leases"
        self.tombstone_path = self.cache_root / "cleanup-tombstones.jsonl"
        self.quarantine_path = self.cache_root / "quarantine-evidence.jsonl"
        self.recovery_path = self.cache_root / "recovery-evidence.jsonl"

    def initialize(self) -> None:
        self.cache_root.mkdir(parents=True, exist_ok=True)
        if self.cache_root.parent == self.cache_root:
            raise RunnerFailure("Fixture cache root cannot be a filesystem root.")
        if _is_reparse_point(self.cache_root):
            raise RunnerFailure("Fixture cache root cannot be a reparse point.")
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
            )

    def _identity_parts(self, fingerprint: str, instance_id: str) -> tuple[str, str]:
        if FINGERPRINT_PATTERN.fullmatch(fingerprint) is None:
            raise RunnerFailure("Cache fingerprint must be a canonical SHA-256 digest.")
        if INSTANCE_PATTERN.fullmatch(instance_id) is None:
            raise RunnerFailure("Template instance ID is not a safe typed identifier.")
        return fingerprint, instance_id

    def instance_path(self, fingerprint: str, instance_id: str) -> Path:
        fingerprint, instance_id = self._identity_parts(fingerprint, instance_id)
        return (
            self.cache_root / fingerprint / "instances" / instance_id
        ).resolve()

    def _assert_owned_instance(self, fingerprint: str, instance_id: str, path: Path) -> None:
        expected = self.instance_path(fingerprint, instance_id)
        if path.resolve() != expected or self.cache_root not in expected.parents:
            raise RunnerFailure("Cache operation escaped the exact typed instance path.")
        if expected in {self.cache_root, self.cache_root.parent}:
            raise RunnerFailure("Cache operation resolved to a broad root.")
        if expected.exists():
            _assert_plain_tree(expected)

    @contextmanager
    def lock(self, fingerprint: str, *, run_id: str, timeout_seconds: int = 30) -> Iterator[None]:
        self._identity_parts(fingerprint, "lock")
        lock_path = self.cache_root / fingerprint / "bundle.lock.json"
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

    def lookup(self, recipe: RecipeBase, instance_id: str) -> CacheHit | None:
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
        if entry.get("fingerprint") != recipe.cache_fingerprint or entry.get(
            "template_instance_id"
        ) != instance_id:
            raise RunnerFailure(
                "Cache entry ownership metadata does not match its typed path."
            )
        state = entry.get("state")
        if state not in EXACT_ENTRY_STATES:
            raise RunnerFailure(
                f"Exact fixture cache instance has unsupported state {state!r}; "
                "cleanup and rebuild are blocked."
            )
        return str(state)

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
        self._update_index(recipe.cache_fingerprint, instance_id, "ready")
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
        state: str = "ready",
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
        final = self.instance_path(recipe.cache_fingerprint, instance_id)
        self._assert_owned_instance(recipe.cache_fingerprint, instance_id, final)
        if final.exists():
            raise RunnerFailure("Refusing to overwrite an existing fixture cache instance.")
        instances = final.parent
        instances.mkdir(parents=True, exist_ok=True)
        # Keep staging at the managed root to retain Windows path-length headroom;
        # atomic publication still occurs on the same filesystem.
        staging = self.cache_root / f".staging-{uuid.uuid4().hex}"
        staging.mkdir()
        try:
            inventories: dict[str, ByteInventory] = {}
            role_entries: dict[str, Any] = {}
            for role in roles:
                source = source_paths[role].resolve(strict=True)
                _assert_plain_tree(source)
                target = staging / "notebooks" / role / "template-notebook"
                shutil.copytree(source, target)
                source_inventory = inventory_directory(source)
                copied_inventory = inventory_directory(target)
                if copied_inventory != source_inventory:
                    raise InvariantFailure(f"Opaque template copy mismatch for role {role}.")
                inventories[role] = copied_inventory
                role_entries[role] = {
                    "template_path": str((final / "notebooks" / role / "template-notebook").resolve()),
                    "source_notebook": dict(source_notebooks[role]),
                    "closed_before_publish": True,
                }
                _atomic_json(
                    staging / "notebooks" / role / "byte-inventory.json",
                    copied_inventory.as_dict(),
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
                        )
            entry = {
                "schema_version": CACHE_SCHEMA_VERSION,
                "state": state,
                "fingerprint": recipe.cache_fingerprint,
                "template_instance_id": instance_id,
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
            }
            _atomic_json(staging / "bundle-entry.json", entry)
            os.replace(staging, final)
            self._update_index(recipe.cache_fingerprint, instance_id, state)
            return CacheHit(recipe.cache_fingerprint, instance_id, final, entry)
        except Exception:
            if staging.exists():
                shutil.rmtree(staging)
            raise

    def _update_index(self, fingerprint: str, instance_id: str, state: str) -> None:
        path = self.cache_root / "index.json"
        index = _read_json(path) if path.exists() else {
            "schema_version": CACHE_SCHEMA_VERSION,
            "entries": {},
        }
        entries = dict(index.get("entries", {}))
        entries[f"{fingerprint}:{instance_id}"] = {
            "fingerprint": fingerprint,
            "template_instance_id": instance_id,
            "state": state,
            "updated_at": utc_now(),
        }
        index["entries"] = entries
        _atomic_json(path, index)

    def materialize(
        self,
        hit: CacheHit,
        run_dir: Path,
        *,
        working_names: Mapping[str, str] | None = None,
    ) -> MaterializedBundle:
        run_dir = run_dir.resolve()
        working_root = run_dir / "notebooks"
        working_root.mkdir(parents=True, exist_ok=True)
        staging = run_dir / f".materializing-{uuid.uuid4().hex}"
        staging.mkdir()
        template_paths: dict[str, Path] = {}
        working_paths: dict[str, Path] = {}
        evidence_roles: dict[str, Any] = {}
        try:
            roles = tuple(str(role) for role in hit.entry["roles"])
            selected_names = (
                dict(working_names)
                if working_names is not None
                else {role: f"{role}-working-copy" for role in roles}
            )
            if set(selected_names) != set(roles):
                raise RunnerFailure("Cache materialization names must cover every role exactly.")
            for role in hit.entry["roles"]:
                template = hit.entry_path / "notebooks" / role / "template-notebook"
                template = template.resolve(strict=True)
                working_name = str(selected_names[role])
                if (
                    not working_name
                    or Path(working_name).name != working_name
                    or working_name in {".", ".."}
                ):
                    raise RunnerFailure(
                        f"Cache materialization received an unsafe Notebook name for role {role}."
                    )
                working = (working_root / working_name).resolve()
                if working.exists():
                    raise RunnerFailure(
                        f"Run-scoped working path already exists for role {role}."
                    )
                copied = staging / role
                shutil.copytree(template, copied)
                template_inventory = inventory_directory(template)
                copied_inventory = inventory_directory(copied)
                if copied_inventory != template_inventory:
                    raise InvariantFailure(f"Working copy inventory mismatch for role {role}.")
                template_paths[role] = template
                working_paths[role] = working
                evidence_roles[role] = {
                    "template_path": str(template),
                    "working_name": working_name,
                    "working_path": str(working),
                    "template_inventory": template_inventory.as_dict(),
                    "working_inventory_before_open": copied_inventory.as_dict(),
                    "opened_template": False,
                }
            for role in hit.entry["roles"]:
                os.replace(staging / role, working_paths[role])
            evidence = {
                "schema_version": CACHE_SCHEMA_VERSION,
                "fingerprint": hit.fingerprint,
                "template_instance_id": hit.template_instance_id,
                "decision": "validated_hit",
                "roles": evidence_roles,
                "bundle_inventory_digest": hit.entry["bundle_inventory_digest"],
                "materialized_at": utc_now(),
                "templates_opened": False,
            }
            evidence_path = run_dir / "cache-materialization.json"
            _atomic_json(evidence_path, evidence)
            return MaterializedBundle(
                hit.fingerprint,
                hit.template_instance_id,
                template_paths,
                working_paths,
                evidence_path,
            )
        except Exception:
            for working in working_paths.values():
                if working.exists():
                    shutil.rmtree(working)
            raise
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    def claim_working_bundle(
        self,
        materialized: MaterializedBundle,
        *,
        run_id: str,
        notebook_ids: Mapping[str, str],
        open_state_probe: Callable[[Mapping[str, Any]], bool] | None = None,
    ) -> Path:
        if set(notebook_ids) != set(materialized.working_paths):
            raise RunnerFailure("Working lease requires every materialized role ID.")
        self.lease_root.mkdir(parents=True, exist_ok=True)
        if open_state_probe is not None:
            self.reconcile_stale_working_leases(open_state_probe)
        for lease_path in self.lease_root.glob("*.json"):
            lease = _read_json(lease_path)
            if lease.get("state") != "active":
                continue
            claimed = set(str(value) for value in lease.get("notebook_ids", {}).values())
            collision = claimed & set(notebook_ids.values())
            if collision:
                working_paths = ", ".join(
                    f"{role}={path}"
                    for role, path in sorted(lease.get("working_paths", {}).items())
                )
                raise RunnerFailure(
                    "Active fixture working lease conflict: "
                    f"run_id={lease.get('run_id')}; {working_paths}. "
                    "Close that exact working Notebook in OneNote, then retry."
                )
        if len(set(notebook_ids.values())) != len(notebook_ids):
            raise RunnerFailure("Two Notebook roles resolved to the same Notebook ID.")
        lease_path = self.lease_root / f"{run_id}.json"
        if lease_path.exists():
            raise RunnerFailure("This run already owns a fixture cache working lease.")
        _atomic_json(
            lease_path,
            {
                "schema_version": CACHE_SCHEMA_VERSION,
                "state": "active",
                "run_id": run_id,
                "fingerprint": materialized.fingerprint,
                "template_instance_id": materialized.template_instance_id,
                "notebook_ids": dict(notebook_ids),
                "working_paths": {
                    role: str(path) for role, path in materialized.working_paths.items()
                },
                "working_names": {
                    role: path.name for role, path in materialized.working_paths.items()
                },
                "created_at": utc_now(),
            },
        )
        return lease_path

    def reconcile_stale_working_leases(
        self,
        open_state_probe: Callable[[Mapping[str, Any]], bool],
    ) -> list[dict[str, Any]]:
        """Mark only leases whose exact IDs and working paths are no longer open."""

        reconciled: list[dict[str, Any]] = []
        if not self.lease_root.exists():
            return reconciled
        for lease_path in self.lease_root.glob("*.json"):
            lease = _read_json(lease_path)
            if lease.get("state") != "active" or open_state_probe(lease):
                continue
            lease.update(
                state="stale_closed_observed",
                reconciled_at=utc_now(),
                reconciliation="no active Notebook ID or working path",
            )
            _atomic_json(lease_path, lease)
            reconciled.append(
                {
                    "run_id": lease.get("run_id"),
                    "fingerprint": lease.get("fingerprint"),
                    "template_instance_id": lease.get("template_instance_id"),
                    "state": lease["state"],
                }
            )
        return reconciled

    def bind_working_bundle_notebook_ids(
        self,
        lease_path: Path,
        *,
        notebook_ids: Mapping[str, str],
        open_state_probe: Callable[[Mapping[str, Any]], bool] | None = None,
    ) -> None:
        lease_path = lease_path.resolve(strict=True)
        if lease_path.parent != self.lease_root.resolve(strict=True):
            raise RunnerFailure("Working lease ID binding escaped the managed lease root.")
        lease = _read_json(lease_path)
        if lease.get("state") != "active":
            raise RunnerFailure("Working lease is not active during Notebook ID binding.")
        if set(notebook_ids) != set(lease.get("working_paths", {})):
            raise RunnerFailure("Working lease ID binding must cover every materialized role.")
        if len(set(notebook_ids.values())) != len(notebook_ids):
            raise RunnerFailure("Materialized roles resolved to the same live Notebook ID.")
        if open_state_probe is not None:
            self.reconcile_stale_working_leases(open_state_probe)
        for other_path in self.lease_root.glob("*.json"):
            if other_path.resolve() == lease_path:
                continue
            other = _read_json(other_path)
            if other.get("state") != "active":
                continue
            collision = set(str(value) for value in other.get("notebook_ids", {}).values()) & set(
                notebook_ids.values()
            )
            if collision:
                raise RunnerFailure("A live materialized Notebook ID has another active lease.")
        lease["template_notebook_ids"] = dict(lease.get("notebook_ids", {}))
        lease["notebook_ids"] = dict(notebook_ids)
        lease["live_ids_bound_at"] = utc_now()
        _atomic_json(lease_path, lease)

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

    def release_working_bundle(self, lease_path: Path) -> None:
        lease_path = lease_path.resolve(strict=True)
        if lease_path.parent != self.lease_root.resolve(strict=True):
            raise RunnerFailure("Working lease release escaped the managed lease root.")
        lease = _read_json(lease_path)
        lease.update(state="closed", closed_at=utc_now())
        _atomic_json(lease_path, lease)

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
        if entry.get("fingerprint") != recipe.cache_fingerprint or entry.get(
            "template_instance_id"
        ) != instance_id:
            raise RunnerFailure("Cache quarantine ownership metadata does not match its typed path.")
        entry.update(
            state="invalid",
            invalid_reason=reason,
            invalidated_at=utc_now(),
            invalidated_by_run=run_id,
        )
        _atomic_json(entry_path, entry)
        self._update_index(recipe.cache_fingerprint, instance_id, "invalid")
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
        if entry.get("fingerprint") != recipe.cache_fingerprint or entry.get(
            "template_instance_id"
        ) != instance_id:
            raise RunnerFailure("Cache cleanup ownership metadata does not match its typed path.")
        active_leases = []
        if self.lease_root.exists():
            active_leases = [
                lease
                for lease in self.lease_root.glob("*.json")
                if _read_json(lease).get("state") == "active"
                and _read_json(lease).get("fingerprint") == recipe.cache_fingerprint
                and _read_json(lease).get("template_instance_id") == instance_id
            ]
        if active_leases:
            raise RunnerFailure("Cannot clean a cache instance with an active working lease.")
        if open_state_probe is not None and open_state_probe(entry):
            raise RunnerFailure("Cannot clean a cache instance while a source Notebook is open.")
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
            "no_active_lease": True,
            "deleted": False,
            "created_at": utc_now(),
        }
        try:
            self._assert_owned_instance(recipe.cache_fingerprint, instance_id, path)
            shutil.rmtree(path)
            cleanup.update(deleted=True, completed_at=utc_now())
            self._update_index(recipe.cache_fingerprint, instance_id, "tombstone")
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
