"""Narrow trusted wrapper for one exact Notebook role lifecycle only."""

from __future__ import annotations

from contextlib import contextmanager
import msvcrt
from pathlib import Path
import stat
import time
from typing import Any, Iterator, Mapping
from urllib.parse import unquote, urlparse
import xml.etree.ElementTree as ET

from local_onenote_mcp.bridge import OneNoteBridge, OneNoteBridgeError
from local_onenote_mcp.constants import CREATE_FILE_TYPES, HIERARCHY_SCOPES, XML_SCHEMA_2013
from local_onenote_mcp.hierarchy import display_name, parse_hierarchy
from local_onenote_mcp.onenote_errors import transient_read_error
from local_onenote_mcp.services.convergence import DEFAULT_CONVERGENCE, converge
from local_onenote_mcp.services.hierarchy import HierarchyService

from .runtime import EXIT_MCP, RestoreFailure, RunnerFailure
from .progress import RunProgressReporter
from .test_utils import read_json, stable_item, utc_now, write_json


MAX_MATERIALIZED_HIERARCHY_ENTRIES = 256
MATERIALIZED_HIERARCHY_RETRIES = 8
MATERIALIZED_HIERARCHY_DELAY_SECONDS = 0.75
MATERIALIZED_ACTIVATION_RETRY_HRESULT = "0x8004201D"


def _is_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & flag)


class NotebookLifecycleWrapper:
    """Expose no arbitrary COM operation beyond one frozen Notebook role lifecycle."""

    def __init__(
        self,
        run_dir: Path,
        *,
        timeout_seconds: int,
        bridge: OneNoteBridge | None = None,
        role: str = "source",
        progress: RunProgressReporter | None = None,
    ) -> None:
        self.run_dir = run_dir.resolve()
        self.notebook_root = (self.run_dir / "notebooks").resolve()
        self.role = role
        self.progress = progress or RunProgressReporter.disabled()
        suffix = "" if role == "source" else f"-{role}"
        self.lease_path = self.run_dir / f"lifecycle-lease{suffix}.json"
        self.materialized_evidence_path = (
            self.run_dir / f"materialized-hierarchy-open{suffix}.json"
        )
        self._bridge = bridge or OneNoteBridge(
            timeout_seconds=timeout_seconds,
            audit_path=self.run_dir / "lifecycle-bridge-calls.jsonl",
        )
        self._hierarchy = HierarchyService(self._bridge)

    def create_fresh_notebook(self, name: str) -> tuple[dict[str, Any], dict[str, Any]]:
        self.progress.unit_started("lifecycle", f"{self.role} create", 1, 1)
        if self.lease_path.exists():
            raise RunnerFailure("Lifecycle lease already exists; refusing to create another source Notebook.")
        exact = [
            item
            for item in self._hierarchy.list_notebooks(include_recycle_bin=True)["notebooks"]
            if display_name(item).casefold() == name.casefold()
        ]
        if exact:
            raise RunnerFailure(
                "Isolated scenario requires a fresh Notebook, but an exact-name Notebook already exists."
            )
        target_path = (self.notebook_root / name).resolve()
        if target_path.parent != self.notebook_root:
            raise RunnerFailure("Notebook lifecycle target escaped the run-scoped Notebook root.")
        if target_path.exists():
            raise RunnerFailure("Run-scoped Notebook path already exists; refusing lifecycle reuse.")
        self.notebook_root.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        opened = self._bridge.call(
            "open_hierarchy",
            path=str(target_path),
            relative_to_id="",
            create_file_type=CREATE_FILE_TYPES["notebook"],
        )
        notebook = self._hierarchy.wait_for_created(
            name,
            "notebook",
            str(opened["object_id"]),
        )
        if notebook is None:
            raise RunnerFailure("Notebook lifecycle create could not verify the new exact Notebook.")
        if display_name(notebook) != name:
            raise RunnerFailure("Notebook lifecycle create returned an unexpected Notebook name.")
        elapsed = round(time.perf_counter() - started, 6)
        lease = {
            "schema_version": 1,
            "run_id": self.run_dir.name,
            "role": self.role,
            "notebook_id": str(notebook["id"]),
            "expected_name": name,
            "expected_local_path": str(target_path),
            "created_at": utc_now(),
            "create_result": {
                "object_id": str(opened["object_id"]),
                "item": stable_item(notebook),
                "elapsed_seconds": elapsed,
            },
            "state": "active",
            "filesystem_deleted": False,
        }
        write_json(self.lease_path, lease)
        self.progress.unit_completed(
            "lifecycle",
            f"{self.role} create",
            1,
            1,
            elapsed_seconds=elapsed,
        )
        return notebook, lease

    def _reported_notebook_directory(self, notebook_id: str) -> Path:
        result = self._bridge.call(
            "get_hierarchy",
            start_id=notebook_id,
            scope=HIERARCHY_SCOPES["self"],
            schema=XML_SCHEMA_2013,
        )
        try:
            root = ET.fromstring(str(result["xml"]))
        except (KeyError, ET.ParseError) as exc:
            raise RestoreFailure("Lifecycle could not read the opened Notebook's COM path.") from exc
        node = next(
            (
                candidate
                for candidate in root.iter()
                if candidate.tag.rsplit("}", 1)[-1] == "Notebook"
                and candidate.attrib.get("ID") == notebook_id
            ),
            None,
        )
        reported = "" if node is None else str(node.attrib.get("path", ""))
        if not reported:
            raise RestoreFailure("Opened Notebook hierarchy did not report a local path.")
        if reported.casefold().startswith("file:"):
            parsed = urlparse(reported)
            reported = unquote(parsed.path).lstrip("/") if parsed.scheme else reported
        path = Path(reported).resolve()
        if path.suffix.casefold() == ".onetoc2":
            path = path.parent
        return path

    @contextmanager
    def working_notebook_open_lock(
        self,
        *,
        timeout_seconds: int = 30,
    ) -> Iterator[None]:
        """Serialize run-local identity checks with materialized Notebook opens."""

        lock_path = self.run_dir.parent / "working-notebook-open.lock"
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
                            "Another run is opening a materialized working Notebook."
                        )
                    time.sleep(0.05)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)

    def assert_no_active_working_conflict(
        self,
        *,
        notebook_ids: Mapping[str, str] | None,
        working_paths: Mapping[str, Path],
        open_notebooks: Mapping[str, Path],
    ) -> None:
        """Reject live collisions using one caller-owned OneNote snapshot."""

        notebook_ids = notebook_ids or {}
        requested_id_values = [str(value) for value in notebook_ids.values() if value]
        requested_ids = set(requested_id_values)
        requested_paths = {path.resolve() for path in working_paths.values()}
        if notebook_ids and len(requested_id_values) != len(notebook_ids):
            raise RunnerFailure("Working identity check requires every role Notebook ID.")
        if notebook_ids and len(requested_ids) != len(notebook_ids):
            raise RunnerFailure("Two working Notebook roles resolved to the same Notebook ID.")
        validation_root = self.run_dir.parent.resolve()
        for candidate_run in validation_root.iterdir():
            if (
                not candidate_run.is_dir()
                or candidate_run.resolve() == self.run_dir
                or candidate_run.parent.resolve() != validation_root
            ):
                continue
            for lease_path in candidate_run.glob("lifecycle-lease*.json"):
                try:
                    lease = read_json(lease_path)
                except Exception as exc:
                    raise RunnerFailure(
                        f"Run-local lifecycle lease is unreadable: {lease_path}"
                    ) from exc
                claimed_path_value = str(
                    lease.get("actual_local_path")
                    or lease.get("expected_local_path")
                    or ""
                )
                if lease.get("state") == "active" and not claimed_path_value:
                    raise RunnerFailure(
                        "Active run-local lifecycle lease is missing its exact working path: "
                        f"{lease_path}"
                    )
                claimed_path = (
                    Path(claimed_path_value).resolve() if claimed_path_value else None
                )
                active_ids = {
                    notebook_id
                    for notebook_id, actual_path in open_notebooks.items()
                    if actual_path == claimed_path
                }
                if lease.get("state") != "active" or not active_ids:
                    continue
                if active_ids & requested_ids or claimed_path in requested_paths:
                    raise RunnerFailure(
                        "Active working Notebook conflict: "
                        f"run_id={candidate_run.name}; "
                        f"notebook_ids={','.join(sorted(active_ids))}; "
                        f"working_path={claimed_path_value}. "
                        "Close that exact working Notebook in OneNote, then retry."
                    )

    def open_working_notebook(
        self,
        name: str,
        working_path: Path,
        *,
        template_paths: tuple[Path, ...],
        role: str = "source",
        lease_archive_reason: str = "cold-build",
        activate_hierarchy: bool = True,
        _allow_activation_retry: bool = True,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Open one materialized working directory and prove no template was opened."""

        opened_started = time.perf_counter()
        self.progress.unit_started("lifecycle", f"{self.role} open working copy", 1, 1)

        if role != self.role:
            raise RunnerFailure("Lifecycle role argument differs from its frozen wrapper role.")
        working_path = working_path.resolve(strict=True)
        if working_path.parent != self.notebook_root:
            raise RunnerFailure("Working Notebook path escaped the run-scoped Notebook root.")
        templates = tuple(path.resolve(strict=True) for path in template_paths)
        if working_path in templates:
            raise RunnerFailure("Lifecycle refuses to open a cache template path.")
        if lease_archive_reason not in {
            "activation-retry",
            "cold-build",
            "materialized-import-checkpoint",
            "persistence-import-checkpoint",
            "persistence-checkpoint",
        }:
            raise RunnerFailure("Lifecycle lease archive reason is not allowlisted.")
        if not activate_hierarchy and lease_archive_reason not in {
            "materialized-import-checkpoint",
            "persistence-import-checkpoint",
        }:
            raise RunnerFailure(
                "Hierarchy activation may only be deferred for an import checkpoint."
            )
        if self.lease_path.exists():
            previous = self._read_lease()
            if previous.get("state") != "closed":
                raise RunnerFailure("Lifecycle lease is active; refusing a second Notebook open.")
            archived = self.run_dir / (
                f"lifecycle-{lease_archive_reason}-lease.json"
                if self.role == "source"
                else f"lifecycle-{lease_archive_reason}-lease-{self.role}.json"
            )
            if archived.exists():
                raise RunnerFailure("Lifecycle lease archive already exists.")
            self.lease_path.replace(archived)
        opened = self._bridge.call(
            "open_hierarchy",
            path=str(working_path),
            relative_to_id="",
            create_file_type=CREATE_FILE_TYPES["none"],
        )
        notebook = self._hierarchy.wait_for_created(
            name,
            "notebook",
            str(opened["object_id"]),
        )
        if notebook is None or str(notebook.get("id", "")) != str(opened["object_id"]):
            raise RunnerFailure("Lifecycle could not bind the opened working Notebook by exact ID.")
        actual_path = self._reported_notebook_directory(str(notebook["id"]))
        if actual_path != working_path or actual_path in templates:
            raise RunnerFailure("OneNote opened a path other than the exact working copy.")
        lease = {
            "schema_version": 2,
            "run_id": self.run_dir.name,
            "role": role,
            "notebook_id": str(notebook["id"]),
            "expected_name": display_name(notebook),
            "expected_local_path": str(working_path),
            "actual_local_path": str(actual_path),
            "template_paths": [str(path) for path in templates],
            "opened_template": False,
            "opened_hierarchy": [],
            "hierarchy_open_status": "running",
            "opened_at": utc_now(),
            "state": "active",
            "filesystem_deleted": False,
        }
        write_json(self.lease_path, lease)
        if not activate_hierarchy:
            lease.update(
                hierarchy_open_status="deferred_to_fixture_convergence",
                hierarchy_opened_at=utc_now(),
            )
            write_json(self.lease_path, lease)
            self.progress.unit_completed(
                "lifecycle",
                f"{self.role} reopen working copy",
                1,
                1,
                elapsed_seconds=time.perf_counter() - opened_started,
            )
            return notebook, lease
        try:
            opened_hierarchy = self._open_materialized_hierarchy(
                working_path,
                notebook,
            )
        except Exception as exc:
            lease.update(
                hierarchy_open_status="failed",
                hierarchy_open_stage="hierarchy_activation",
                hierarchy_open_error=f"{type(exc).__name__}: {exc}",
                hierarchy_open_failed_at=utc_now(),
            )
            write_json(self.lease_path, lease)
            if _allow_activation_retry and self._materialized_activation_is_retryable():
                return self._retry_materialized_activation(
                    name=name,
                    working_path=working_path,
                    templates=templates,
                    role=role,
                    first_error=exc,
                )
            raise
        lease.update(
            opened_hierarchy=opened_hierarchy,
            hierarchy_open_status="passed",
            hierarchy_opened_at=utc_now(),
        )
        write_json(self.lease_path, lease)
        self.progress.unit_completed(
            "lifecycle",
            f"{self.role} open working copy",
            1,
            1,
            elapsed_seconds=time.perf_counter() - opened_started,
        )
        return notebook, lease

    def _materialized_activation_is_retryable(self) -> bool:
        """Accept only a pure, parent-bound OneNote synchronization lag."""

        try:
            evidence = read_json(self.materialized_evidence_path)
        except Exception:
            return False
        attempts = evidence.get("attempts")
        if evidence.get("status") != "failed" or not isinstance(attempts, list):
            return False
        pending = [
            attempt
            for attempt in attempts
            if isinstance(attempt, dict) and attempt.get("activated") is not True
        ]
        return bool(pending) and all(
            attempt.get("returned_object_id")
            and attempt.get("observed_parent_id") == attempt.get("requested_parent_id")
            and attempt.get("exact_object_probe_hresult")
            == MATERIALIZED_ACTIVATION_RETRY_HRESULT
            and not attempt.get("exact_object_probe_mismatch")
            and not attempt.get("bridge_error_type")
            for attempt in pending
        )

    def _retry_materialized_activation(
        self,
        *,
        name: str,
        working_path: Path,
        templates: tuple[Path, ...],
        role: str,
        first_error: Exception,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Close and reopen the same working copy once; never replay a mutation."""

        suffix = "" if self.role == "source" else f"-{self.role}"
        initial_evidence_path = (
            self.run_dir / f"materialized-hierarchy-open-initial{suffix}.json"
        )
        recovery_path = self.run_dir / f"materialized-activation-recovery{suffix}.json"
        initial_evidence = read_json(self.materialized_evidence_path)
        write_json(initial_evidence_path, initial_evidence)
        recovery: dict[str, Any] = {
            "schema_version": 1,
            "status": "running",
            "reason": "onenote_not_yet_synchronized",
            "mutation_started": False,
            "working_path": str(working_path),
            "initial_evidence": str(initial_evidence_path),
            "started_at": utc_now(),
        }
        write_json(recovery_path, recovery)
        self.progress.unit_started(
            "cache recovery",
            f"{self.role} close/reopen working copy",
            1,
            1,
        )
        try:
            close_result = self.close_exact_notebook()
            notebook, lease = self.open_working_notebook(
                name,
                working_path,
                template_paths=templates,
                role=role,
                lease_archive_reason="activation-retry",
                _allow_activation_retry=False,
            )
        except Exception as retry_exc:
            recovery.update(
                status="failed",
                failed_at=utc_now(),
                first_error_type=type(first_error).__name__,
                retry_error_type=type(retry_exc).__name__,
                retry_error=str(retry_exc),
            )
            write_json(recovery_path, recovery)
            raise RunnerFailure(
                "Materialized hierarchy activation remained unavailable after one exact "
                "working-copy close/reopen recovery; mutation was not started. "
                "Close older preserved validation Notebooks in OneNote, then retry."
            ) from retry_exc
        recovery.update(
            status="passed",
            completed_at=utc_now(),
            close_result=close_result,
            reopened_notebook_id=str(notebook.get("id", "")),
        )
        write_json(recovery_path, recovery)
        lease["activation_recovery"] = {
            "attempted": True,
            "passed": True,
            "reason": recovery["reason"],
            "evidence": str(recovery_path),
        }
        write_json(self.lease_path, lease)
        self.progress.unit_completed(
            "cache recovery",
            f"{self.role} close/reopen working copy",
            1,
            1,
        )
        return notebook, lease

    def _open_materialized_hierarchy(
        self,
        working_path: Path,
        notebook: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Batch-open bounded containers and observe the Notebook in the same COM session."""

        if not getattr(
            self._bridge,
            "supports_hierarchy_batch",
            isinstance(self._bridge, OneNoteBridge),
        ):
            return self._open_materialized_hierarchy_legacy(working_path, notebook)

        requests: list[dict[str, Any]] = []
        attempts: list[dict[str, Any]] = []
        opened: list[dict[str, Any]] = []
        batch_observations: list[dict[str, Any]] = []

        def save(status: str, error: str | None = None) -> None:
            evidence: dict[str, Any] = {
                "schema_version": 2,
                "status": status,
                "working_path": str(working_path),
                "notebook_id": str(notebook["id"]),
                "batch_session_count": len(batch_observations),
                "requests": requests,
                "attempts": attempts,
                "opened": opened,
                "batch_observations": batch_observations,
                "content_saved": False,
                "recorded_at": utc_now(),
            }
            if error is not None:
                evidence["error"] = error
            write_json(self.materialized_evidence_path, evidence)

        def collect(directory: Path, parent_key: str, parent_path: str) -> None:
            children = sorted(directory.iterdir(), key=lambda value: value.name.casefold())
            for child in children:
                if not (child.is_dir() or child.suffix.casefold() == ".one"):
                    continue
                if len(requests) >= MAX_MATERIALIZED_HIERARCHY_ENTRIES:
                    raise RunnerFailure(
                        "Materialized Notebook hierarchy exceeds its bounded budget."
                    )
                resolved = child.resolve(strict=True)
                if working_path not in resolved.parents or _is_reparse_point(child):
                    raise RunnerFailure(
                        "Materialized Notebook hierarchy escaped its exact plain working tree."
                    )
                key = child.relative_to(working_path).as_posix()
                resource_type = "section_group" if child.is_dir() else "section"
                expected_path = HierarchyService.friendly_child_path(parent_path, child.name)
                requests.append(
                    {
                        "key": key,
                        "parent_key": parent_key,
                        "path": str(resolved) if not parent_key else child.name,
                        "path_mode": "absolute" if not parent_key else "parent_relative",
                        "relative_to_id": "",
                        "resource_type": resource_type,
                        "relative_path": key,
                        "absolute_working_path": str(resolved),
                        "expected_path": expected_path,
                        "create_file_type": CREATE_FILE_TYPES["none"],
                    }
                )
                if child.is_dir():
                    collect(child, key, expected_path)

        def observe_batch(
            response: Mapping[str, Any],
            pending: list[dict[str, Any]],
            batch_index: int,
        ) -> list[dict[str, Any]]:
            hierarchy_error = response.get("hierarchy_error")
            xml = response.get("xml")
            if isinstance(xml, str) and xml.strip():
                try:
                    resources = parse_hierarchy(xml)
                except (ET.ParseError, ValueError) as exc:
                    raise RunnerFailure(
                        "Materialized hierarchy batch returned invalid hierarchy XML."
                    ) from exc
            elif isinstance(hierarchy_error, Mapping):
                resources = []
            else:
                raise RunnerFailure(
                    "Materialized hierarchy batch returned neither hierarchy XML nor a typed read error."
                )
            by_id = {str(item.get("id")): item for item in resources if item.get("id")}
            results = {
                str(item.get("key")): item
                for item in response.get("items", ())
                if isinstance(item, Mapping)
            }
            opened_by_key = {
                item["relative_path"]: item["object_id"] for item in opened
            }
            batch_object_ids = {
                key: str(item.get("object_id") or "")
                for key, item in results.items()
                if item.get("object_id")
            }
            missing: list[dict[str, Any]] = []
            for request in pending:
                result = results.get(str(request["key"]), {})
                parent_id = (
                    str(notebook["id"])
                    if not request["parent_key"]
                    else opened_by_key.get(
                        str(request["parent_key"]),
                        batch_object_ids.get(str(request["parent_key"]), ""),
                    )
                )
                attempt: dict[str, Any] = {
                    "batch": batch_index,
                    "relative_path": request["relative_path"],
                    "resource_type": request["resource_type"],
                    "path_mode": request["path_mode"],
                    "requested_parent_id": parent_id,
                    "activated": False,
                }
                object_id = str(result.get("object_id") or "")
                if object_id:
                    attempt["returned_object_id"] = object_id
                error = result.get("error")
                if isinstance(error, Mapping):
                    attempt["bridge_error_type"] = str(
                        error.get("leaf_exception_type") or "OneNoteBridgeError"
                    )
                    attempt["bridge_error_hresult"] = str(error.get("hresult") or "")
                item = by_id.get(object_id)
                if item is not None and not (
                    item.get("resource_type") == request["resource_type"]
                    and str(item.get("parent_id", "")) == parent_id
                    and str(item.get("path", "")).casefold()
                    == str(request["expected_path"]).casefold()
                    and item.get("is_in_recycle_bin") is not True
                ):
                    raise RunnerFailure(
                        "Materialized hierarchy batch observed a deterministic type, parent, "
                        f"path, or recycle-bin conflict for {request['relative_path']}."
                    )
                if item is None:
                    matches = [
                        candidate
                        for candidate in resources
                        if candidate.get("resource_type") == request["resource_type"]
                        and str(candidate.get("path", "")).casefold()
                        == str(request["expected_path"]).casefold()
                        and candidate.get("is_in_recycle_bin") is not True
                    ]
                    if len(matches) > 1:
                        raise RunnerFailure(
                            "Materialized hierarchy batch observed an ambiguous typed path for "
                            f"{request['relative_path']}."
                        )
                    item = matches[0] if len(matches) == 1 else None
                if item is not None and (
                    item.get("resource_type") == request["resource_type"]
                    and str(item.get("parent_id", "")) == parent_id
                    and str(item.get("path", "")).casefold()
                    == str(request["expected_path"]).casefold()
                    and item.get("is_in_recycle_bin") is not True
                ):
                    attempt.update(
                        activated=True,
                        activation_proof="batch_notebook_snapshot",
                        observed_parent_id=str(item.get("parent_id", "")),
                    )
                    opened.append(
                        {
                            "relative_path": request["relative_path"],
                            "resource_type": request["resource_type"],
                            "object_id": str(item["id"]),
                        }
                    )
                    opened_by_key[str(request["key"])] = str(item["id"])
                elif object_id and not isinstance(error, Mapping):
                    attempt.update(
                        activated=True,
                        activation_proof="open_hierarchy_returned_id_pending_fixture_convergence",
                        snapshot_visible=False,
                    )
                    opened.append(
                        {
                            "relative_path": request["relative_path"],
                            "resource_type": request["resource_type"],
                            "object_id": object_id,
                            "snapshot_visible": False,
                        }
                    )
                    opened_by_key[str(request["key"])] = object_id
                else:
                    missing.append(request)
                attempts.append(attempt)
            return missing

        try:
            collect(working_path, "", str(notebook["path"]))
            pending = list(requests)
            for batch_index in range(1, 3):
                if not pending:
                    break
                opened_by_key = {
                    item["relative_path"]: item["object_id"] for item in opened
                }
                batch_requests: list[dict[str, Any]] = []
                for request in pending:
                    parent_key = str(request["parent_key"])
                    parent_already_open = parent_key in opened_by_key
                    batch_requests.append(
                        {
                            "key": request["key"],
                            "parent_key": "" if parent_already_open else parent_key,
                            "path": request["path"],
                            "relative_to_id": (
                                opened_by_key[parent_key]
                                if parent_already_open
                                else request["relative_to_id"]
                            ),
                            "create_file_type": request["create_file_type"],
                        }
                    )
                try:
                    response = self._bridge.call(
                        "open_hierarchy_batch",
                        notebook_id=str(notebook["id"]),
                        requests=batch_requests,
                        scope=HIERARCHY_SCOPES["pages"],
                        schema=XML_SCHEMA_2013,
                    )
                except OneNoteBridgeError as exc:
                    batch_observations.append(
                        {
                            "batch": batch_index,
                            "response_received": False,
                            "error_type": type(exc).__name__,
                            "hresult": str(exc.hresult or ""),
                        }
                    )
                    save("running")
                    if batch_index == 1:
                        continue
                    raise
                hierarchy_error = response.get("hierarchy_error")
                observation: dict[str, Any] = {
                    "batch": batch_index,
                    "response_received": True,
                    "hierarchy_xml_available": bool(response.get("xml")),
                }
                if isinstance(hierarchy_error, Mapping):
                    observation.update(
                        hierarchy_error_type=str(
                            hierarchy_error.get("leaf_exception_type")
                            or "OneNoteBridgeError"
                        ),
                        hierarchy_error_hresult=str(hierarchy_error.get("hresult") or ""),
                    )
                batch_observations.append(observation)
                pending = observe_batch(response, pending, batch_index)
                save("running")
            if pending:
                raise RunnerFailure(
                    "Materialized hierarchy batch did not activate every exact container: "
                    + ", ".join(str(item["relative_path"]) for item in pending)
                )
        except Exception as exc:
            save("failed", f"{type(exc).__name__}: {exc}")
            if isinstance(exc, RunnerFailure):
                raise
            raise RunnerFailure(f"Materialized hierarchy open failed: {exc}") from exc
        save("passed")
        return opened

    def _open_materialized_hierarchy_legacy(
        self,
        working_path: Path,
        notebook: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Open every bounded local Section/SectionGroup in its exact working parent."""

        opened: list[dict[str, Any]] = []
        attempts: list[dict[str, Any]] = []
        evidence_path = self.materialized_evidence_path

        def save_evidence(*, status: str, error: str | None = None) -> None:
            evidence: dict[str, Any] = {
                "schema_version": 1,
                "status": status,
                "working_path": str(working_path),
                "notebook_id": str(notebook["id"]),
                "attempts": attempts,
                "opened": opened,
                "content_saved": False,
                "recorded_at": utc_now(),
            }
            if error is not None:
                evidence["error"] = error
            write_json(evidence_path, evidence)

        def visit(directory: Path, parent: dict[str, Any]) -> None:
            children = sorted(directory.iterdir(), key=lambda value: value.name.casefold())
            hierarchy_children = [
                child for child in children if child.is_dir() or child.suffix.casefold() == ".one"
            ]
            for child in hierarchy_children:
                try:
                    resolved = child.resolve(strict=True)
                except FileNotFoundError as exc:
                    message = (
                        "Materialized hierarchy path changed during activation before its "
                        f"exact child could be verified: {child.relative_to(working_path).as_posix()}"
                    )
                    save_evidence(status="failed", error=message)
                    raise RunnerFailure(message) from exc
                if working_path not in resolved.parents or _is_reparse_point(child):
                    raise RunnerFailure(
                        "Materialized Notebook hierarchy escaped its exact plain working tree."
                    )
                if len(opened) >= MAX_MATERIALIZED_HIERARCHY_ENTRIES:
                    raise RunnerFailure("Materialized Notebook hierarchy exceeds its bounded budget.")
                resource_type = "section_group" if child.is_dir() else "section"
                parent_id = str(parent["id"])
                expected_path = HierarchyService.friendly_child_path(
                    str(parent["path"]),
                    child.name,
                )
                item: dict[str, Any] | None = None
                if parent.get("resource_type") == "notebook":
                    open_modes = (
                        ("absolute", str(resolved), ""),
                        ("parent_relative", child.name, parent_id),
                    )
                else:
                    # A nested absolute Section open can bind to an unrelated live
                    # parent before the exact SectionGroup is supplied.  That first
                    # attachment may also consume/rewrite the disposable working
                    # path, so nested children use only the already-proven parent.
                    open_modes = (("parent_relative", child.name, parent_id),)
                for path_mode, open_path, relative_to_id in open_modes:
                    attempt: dict[str, Any] = {
                        "relative_path": child.relative_to(working_path).as_posix(),
                        "absolute_working_path": str(resolved),
                        "resource_type": resource_type,
                        "path_mode": path_mode,
                        "open_path": open_path,
                        "requested_parent_id": parent_id,
                        "relative_to_id": relative_to_id,
                        "create_file_type": "none",
                        "activated": False,
                    }
                    attempts.append(attempt)
                    save_evidence(status="running")
                    try:
                        result = self._bridge.call(
                            "open_hierarchy",
                            path=open_path,
                            relative_to_id=relative_to_id,
                            create_file_type=CREATE_FILE_TYPES["none"],
                        )
                    except Exception as open_exc:
                        attempt["bridge_error_type"] = type(open_exc).__name__
                        attempt["bridge_error"] = str(open_exc)
                        save_evidence(status="running")
                        continue
                    object_id = str(result["object_id"])
                    attempt["returned_object_id"] = object_id
                    try:
                        item = self._hierarchy.wait_for_created(
                            expected_path,
                            resource_type,
                            object_id,
                            expected_parent_id=parent_id,
                            validate_parent=True,
                            retries=1,
                            delay_seconds=0,
                        )
                    except Exception as snapshot_exc:
                        attempt["global_snapshot_error"] = type(snapshot_exc).__name__
                        if isinstance(snapshot_exc, OneNoteBridgeError):
                            attempt["global_snapshot_hresult"] = snapshot_exc.hresult
                            attempt["global_snapshot_retryability"] = (
                                snapshot_exc.retryability
                            )
                        item = None
                    attempt["global_snapshot_visible"] = item is not None
                    observed_parent_id = (
                        None if item is None else str(item.get("parent_id", ""))
                    )
                    if item is None or observed_parent_id != parent_id:
                        try:
                            observed_parent_id = str(
                                self._bridge.call(
                                    "get_hierarchy_parent",
                                    object_id=object_id,
                                ).get("parent_id", "")
                            )
                        except Exception as parent_exc:
                            attempt["parent_probe_error"] = type(parent_exc).__name__
                        attempt["observed_parent_id"] = observed_parent_id
                        if observed_parent_id == parent_id:
                            item, exact_probe = self._wait_for_materialized_item_activation(
                                object_id=object_id,
                                resource_type=resource_type,
                                expected_name=expected_path.rsplit("/", 1)[-1],
                                expected_path=expected_path,
                                parent_id=parent_id,
                            )
                            attempt.update(exact_probe)
                        else:
                            item = None
                        save_evidence(status="running")
                        if item is None:
                            continue
                    attempt["activated"] = True
                    attempt["observed_parent_id"] = observed_parent_id
                    attempt["activation_proof"] = (
                        "exact_object_and_parent"
                        if attempt.get("exact_object_probe") == "passed"
                        else (
                            "global_snapshot_retry"
                            if attempt.get("global_snapshot_retry_visible") is True
                            else "global_snapshot"
                        )
                    )
                    break
                if item is None:
                    message = (
                        f"Materialized {resource_type} did not become active under its exact "
                        f"working parent: {child.relative_to(working_path).as_posix()}"
                    )
                    save_evidence(status="failed", error=message)
                    raise RunnerFailure(message)
                opened.append(
                    {
                        "relative_path": child.relative_to(working_path).as_posix(),
                        "resource_type": resource_type,
                        "object_id": str(item["id"]),
                    }
                )
                save_evidence(status="running")
                if child.is_dir():
                    visit(child, item)

        try:
            visit(working_path, notebook)
        except Exception as exc:
            save_evidence(
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
            )
            if isinstance(exc, RunnerFailure):
                raise
            raise RunnerFailure(f"Materialized hierarchy open failed: {exc}") from exc
        save_evidence(status="passed")
        return opened

    def _wait_for_materialized_item_activation(
        self,
        *,
        object_id: str,
        resource_type: str,
        expected_name: str,
        expected_path: str,
        parent_id: str,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        """Boundedly recheck global and exact-self activation without weakening proof."""

        expected_tag = {
            "section": "Section",
            "section_group": "SectionGroup",
        }[resource_type]
        probe: dict[str, Any] = {
            "exact_object_probe": "failed",
            "exact_object_probe_attempts": 0,
            "expected_object_type": resource_type,
            "global_snapshot_retry_attempts": 0,
            "global_snapshot_retry_visible": False,
        }
        for attempt in range(MATERIALIZED_HIERARCHY_RETRIES):
            probe["exact_object_probe_attempts"] = attempt + 1
            try:
                result = self._bridge.call(
                    "get_hierarchy",
                    start_id=object_id,
                    scope=HIERARCHY_SCOPES["self"],
                    schema=XML_SCHEMA_2013,
                )
                root = ET.fromstring(str(result["xml"]))
            except Exception as exc:
                probe["exact_object_probe_error"] = type(exc).__name__
                if isinstance(exc, OneNoteBridgeError):
                    probe["exact_object_probe_hresult"] = exc.hresult
                    probe["exact_object_probe_retryability"] = exc.retryability
            else:
                object_node = next(
                    (
                        candidate
                        for candidate in root.iter()
                        if candidate.attrib.get("ID") == object_id
                    ),
                    None,
                )
                if (
                    object_node is not None
                    and object_node.tag.rsplit("}", 1)[-1] != expected_tag
                ):
                    probe["exact_object_probe_mismatch"] = "resource_type"
                    return None, probe
                node = next(
                    (
                        candidate
                        for candidate in root.iter()
                        if candidate.tag.rsplit("}", 1)[-1] == expected_tag
                        and candidate.attrib.get("ID") == object_id
                    ),
                    None,
                )
                observed_name = (
                    ""
                    if node is None
                    else str(node.attrib.get("name") or node.attrib.get("nickname") or "")
                )
                probe["observed_object_name"] = observed_name
                if (
                    node is not None
                    and observed_name.casefold() == expected_name.casefold()
                    and str(node.attrib.get("isInRecycleBin", "false")).casefold()
                    != "true"
                    and str(node.attrib.get("isRecycleBin", "false")).casefold()
                    != "true"
                ):
                    probe["exact_object_probe"] = "passed"
                    probe.pop("exact_object_probe_error", None)
                    probe.pop("exact_object_probe_hresult", None)
                    probe.pop("exact_object_probe_retryability", None)
                    return (
                        {
                            "id": object_id,
                            "name": observed_name,
                            "resource_type": resource_type,
                            "path": expected_path,
                            "parent_id": parent_id,
                            "is_in_recycle_bin": False,
                            "relationship_source": "exact_com_probe",
                        },
                        probe,
                    )
            probe["global_snapshot_retry_attempts"] = attempt + 1
            try:
                global_item = self._hierarchy.wait_for_created(
                    expected_path,
                    resource_type,
                    object_id,
                    expected_parent_id=parent_id,
                    validate_parent=True,
                    retries=1,
                    delay_seconds=0,
                )
            except Exception as exc:
                probe["global_snapshot_retry_error"] = type(exc).__name__
                if isinstance(exc, OneNoteBridgeError):
                    probe["global_snapshot_retry_hresult"] = exc.hresult
                    probe["global_snapshot_retry_retryability"] = exc.retryability
            else:
                if global_item is not None:
                    probe["global_snapshot_retry_visible"] = True
                    probe.pop("global_snapshot_retry_error", None)
                    probe.pop("global_snapshot_retry_hresult", None)
                    probe.pop("global_snapshot_retry_retryability", None)
                    return global_item, probe
            if attempt + 1 < MATERIALIZED_HIERARCHY_RETRIES:
                time.sleep(MATERIALIZED_HIERARCHY_DELAY_SECONDS)
        return None, probe

    def _read_lease(self) -> dict[str, Any]:
        lease = read_json(self.lease_path)
        if lease.get("schema_version") not in {1, 2}:
            raise RestoreFailure("Unsupported lifecycle lease schema.")
        return lease

    def get_exact_notebook(self, lease: dict[str, Any] | None = None) -> dict[str, Any]:
        lease = lease or self._read_lease()
        if lease.get("role", self.role) != self.role:
            raise RestoreFailure("Lifecycle lease role differs from its frozen wrapper role.")
        notebook_id = str(lease.get("notebook_id", ""))
        expected_name = str(lease.get("expected_name", ""))
        expected_path = Path(str(lease.get("expected_local_path", ""))).resolve()
        if not notebook_id or not expected_name:
            raise RestoreFailure("Lifecycle lease is missing exact Notebook identity fields.")
        if expected_path.parent != self.notebook_root:
            raise RestoreFailure("Lifecycle lease path/name is outside this run's exact binding.")
        if lease.get("schema_version") == 1 and expected_path.name != expected_name:
            raise RestoreFailure("Lifecycle lease path/name is outside this run's exact binding.")
        if not expected_path.exists():
            raise RestoreFailure("Lifecycle lease local Notebook path no longer exists.")
        try:
            current = self._hierarchy.resource(notebook_id, "notebook")
        except ValueError as exc:
            raise RestoreFailure("Lifecycle lease Notebook ID is no longer active.") from exc
        if str(current.get("id")) != notebook_id or display_name(current) != expected_name:
            raise RestoreFailure("Lifecycle lease ID/name binding no longer matches OneNote state.")
        if self._reported_notebook_directory(notebook_id) != expected_path:
            raise RestoreFailure("Lifecycle lease Notebook ID no longer reports its exact local path.")
        return current

    def any_cache_template_open(self, entry: dict[str, Any]) -> bool:
        """Fail closed when an exact cache template path may be open in OneNote."""

        template_paths = {
            Path(str(value.get("template_path", ""))).resolve()
            for value in entry.get("role_entries", {}).values()
            if value.get("template_path")
        }
        if not template_paths:
            return True
        notebooks = self._hierarchy.list_notebooks(include_recycle_bin=True)["notebooks"]
        for notebook in notebooks:
            if notebook.get("is_open") is False:
                continue
            notebook_id = str(notebook.get("id", ""))
            if not notebook_id:
                return True
            try:
                if self._reported_notebook_directory(notebook_id) in template_paths:
                    return True
            except RestoreFailure:
                return True
        return False

    def snapshot_open_notebooks(self) -> dict[str, Path]:
        """Capture current open Notebook IDs and actual directories exactly once."""

        try:
            notebooks = self._hierarchy.list_notebooks(include_recycle_bin=True)[
                "notebooks"
            ]
            snapshot: dict[str, Path] = {}
            observed_paths: dict[Path, str] = {}
            for notebook in notebooks:
                if notebook.get("is_open") is False:
                    continue
                notebook_id = str(notebook.get("id", ""))
                if not notebook_id:
                    raise RestoreFailure(
                        "Open Notebook snapshot contains an item without an ID."
                    )
                actual_path = self._reported_notebook_directory(notebook_id)
                previous = snapshot.get(notebook_id)
                if previous is not None and previous != actual_path:
                    raise RestoreFailure(
                        "One live Notebook ID reports multiple local directories."
                    )
                previous_id = observed_paths.get(actual_path)
                if previous_id is not None and previous_id != notebook_id:
                    raise RestoreFailure(
                        "One live Notebook directory reports multiple Notebook IDs."
                    )
                snapshot[notebook_id] = actual_path
                observed_paths[actual_path] = notebook_id
            return snapshot
        except (OneNoteBridgeError, RestoreFailure) as exc:
            raise RunnerFailure(
                "Could not capture the current OneNote Notebook ID/path snapshot; "
                "working open is blocked.",
                EXIT_MCP,
            ) from exc

    def close_exact_notebook(self) -> dict[str, Any]:
        self.progress.unit_started("lifecycle", f"{self.role} close", 1, 1)
        lease = self._read_lease()
        if lease.get("state") != "active":
            raise RestoreFailure("Lifecycle lease is not active; refusing source Notebook close.")
        current = self.get_exact_notebook(lease)
        started = time.perf_counter()
        try:
            self._bridge.call("close_notebook", notebook_id=str(current["id"]), force=False)
            def observe_close():
                try:
                    return self._hierarchy.resource(str(current["id"]), "notebook")
                except ValueError:
                    return None

            convergence = converge(
                observe_close,
                lambda value: value is None or value.get("is_open") is False,
                lambda value: None
                if value is None
                else (
                    value.get("id"),
                    value.get("resource_type"),
                    value.get("path"),
                    value.get("is_open"),
                ),
                config=DEFAULT_CONVERGENCE,
                clock=time.monotonic,
                sleeper=time.sleep,
                transient=transient_read_error,
            )
            if not convergence.converged:
                raise RestoreFailure(
                    "Close returned success but the exact source Notebook did not converge closed."
                )
            final_state = convergence.value
            elapsed = round(time.perf_counter() - started, 6)
            result = {
                "closed": True,
                "source_notebook_id": str(current["id"]),
                "close_before": stable_item(current),
                "final_state": stable_item(final_state) if final_state else None,
                "elapsed_seconds": elapsed,
                "convergence": convergence.summary(),
                "filesystem_deleted": False,
            }
            lease.update(
                state="closed",
                closed_at=utc_now(),
                close_result=result,
            )
            write_json(self.lease_path, lease)
            self.progress.unit_completed(
                "lifecycle",
                f"{self.role} close",
                1,
                1,
                elapsed_seconds=elapsed,
            )
            return result
        except Exception as exc:
            lease.update(state="close_failed", close_failed_at=utc_now(), close_error=str(exc))
            write_json(self.lease_path, lease)
            if isinstance(exc, RestoreFailure):
                raise
            raise RestoreFailure(f"Exact source Notebook close failed: {exc}") from exc

__all__ = [
    "MATERIALIZED_HIERARCHY_DELAY_SECONDS",
    "MATERIALIZED_HIERARCHY_RETRIES",
    "MAX_MATERIALIZED_HIERARCHY_ENTRIES",
    "NotebookLifecycleWrapper",
]
