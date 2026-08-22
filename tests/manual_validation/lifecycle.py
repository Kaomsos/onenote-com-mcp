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
from .bridge_adapter import VALIDATION_BRIDGE_ADAPTER
from local_onenote_mcp.constants import CREATE_FILE_TYPES, HIERARCHY_SCOPES, XML_SCHEMA_2013
from local_onenote_mcp.hierarchy import display_name, parse_hierarchy
from local_onenote_mcp.onenote_errors import transient_read_error
from local_onenote_mcp.services.convergence import DEFAULT_CONVERGENCE, converge
from local_onenote_mcp.services.hierarchy import HierarchyService

from .runtime import EXIT_MCP, RestoreFailure, RunnerFailure
from .path_budget import validate_onenote_open_path, validate_working_name
from .progress import RunProgressReporter
from .test_utils import read_json, stable_item, utc_now, write_json


MAX_MATERIALIZED_HIERARCHY_ENTRIES = 256
ONENOTE_RECYCLE_BIN_DIRECTORY = "OneNote_RecycleBin"


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
        self._owns_bridge = bridge is None
        self._bridge = bridge or OneNoteBridge(
            timeout_seconds=timeout_seconds,
            audit_path=self.run_dir / "lifecycle-bridge-calls.jsonl",
            adapter=VALIDATION_BRIDGE_ADAPTER,
        )
        self._hierarchy = HierarchyService(self._bridge)

    def close_transport(self) -> None:
        if self._owns_bridge:
            self._bridge.close()

    def refresh_com_client(self) -> dict[str, Any]:
        """Refresh this wrapper's COM independently of MCP and internal bridges.

        ``launch_onenote_gui`` only refreshes the child-process persistent client.
        Isolated Page XML uses the harness ``_internal_bridge``. Exact Notebook
        create/get/close uses this third owner and must be refreshed on its own
        after OneNote exits. The same wrapper instance must remain alive until
        exact close finishes.
        """

        try:
            result = self._bridge.refresh_com_client()
        except Exception as exc:
            raise RestoreFailure(f"Lifecycle COM refresh failed: {exc}") from exc
        return result.content_free_projection()

    def create_fresh_notebook(self, name: str) -> tuple[dict[str, Any], dict[str, Any]]:
        self.progress.unit_started("lifecycle", f"{self.role} create", 1, 1)
        validate_working_name(name)
        target_path = (self.notebook_root / name).resolve()
        validate_onenote_open_path(target_path)
        if self.lease_path.exists():
            raise RunnerFailure("Lifecycle lease already exists; refusing to create another source Notebook.")
        exact = [
            item
            for item in self._hierarchy.list_notebooks()["items"]
            if display_name(item).casefold() == name.casefold()
        ]
        if exact:
            raise RunnerFailure(
                "Isolated scenario requires a fresh Notebook, but an exact-name Notebook already exists."
            )
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
        try:
            result = self._bridge.call(
                "get_hierarchy",
                start_id=notebook_id,
                scope=HIERARCHY_SCOPES["self"],
                schema=XML_SCHEMA_2013,
            )
        except OneNoteBridgeError as exc:
            raise RestoreFailure(
                f"Lifecycle could not read the opened Notebook's COM path: {exc}"
            ) from exc
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
        lease_archive_kind: str = "cold-build",
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Open one exact working directory and prove no template path was opened."""

        opened_started = time.perf_counter()
        self.progress.unit_started("lifecycle", f"{self.role} open working copy", 1, 1)

        if role != self.role:
            raise RunnerFailure("Lifecycle role argument differs from its frozen wrapper role.")
        working_path = working_path.resolve(strict=True)
        validate_onenote_open_path(working_path)
        if working_path.parent != self.notebook_root:
            raise RunnerFailure("Working Notebook path escaped the run-scoped Notebook root.")
        templates = tuple(path.resolve(strict=True) for path in template_paths)
        if working_path in templates:
            raise RunnerFailure("Lifecycle refuses to open a cache template path.")
        if lease_archive_kind not in {"cold-build", "index-checkpoint"}:
            raise RunnerFailure("Lifecycle lease archive kind is not allowlisted.")
        if self.lease_path.exists():
            previous = self._read_lease()
            if previous.get("state") != "closed":
                raise RunnerFailure("Lifecycle lease is active; refusing a second Notebook open.")
            archived = self.run_dir / (
                f"lifecycle-{lease_archive_kind}-lease.json"
                if self.role == "source"
                else f"lifecycle-{lease_archive_kind}-lease-{self.role}.json"
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

    def _open_materialized_hierarchy(
        self,
        working_path: Path,
        notebook: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Batch-open bounded containers and observe the Notebook in the same COM session."""

        requests: list[dict[str, Any]] = []
        attempts: list[dict[str, Any]] = []
        opened: list[dict[str, Any]] = []
        batch_observations: list[dict[str, Any]] = []
        ignored_system_paths: list[dict[str, str]] = []

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
                "ignored_system_paths": ignored_system_paths,
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
                resolved = child.resolve(strict=True)
                if working_path not in resolved.parents or _is_reparse_point(child):
                    raise RunnerFailure(
                        "Materialized Notebook hierarchy escaped its exact plain working tree."
                    )
                key = child.relative_to(working_path).as_posix()
                if (
                    not parent_key
                    and child.is_dir()
                    and child.name.casefold()
                    == ONENOTE_RECYCLE_BIN_DIRECTORY.casefold()
                ):
                    ignored_system_paths.append(
                        {
                            "relative_path": key,
                            "reason": "onenote_recycle_bin_not_activation_target",
                        }
                    )
                    continue
                if len(requests) >= MAX_MATERIALIZED_HIERARCHY_ENTRIES:
                    raise RunnerFailure(
                        "Materialized Notebook hierarchy exceeds its bounded budget."
                    )
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
                    "open_requested": str(request["key"]) in results,
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
                        activation_proof=(
                            "batch_notebook_snapshot"
                            if attempt["open_requested"]
                            else "notebook_snapshot_after_parent_batch"
                        ),
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
            open_attempt_counts: dict[str, int] = {}
            # A returned SectionGroup ID is not immediately safe to use as an
            # OpenHierarchy parent in the same COM invocation.  In particular,
            # a copied Notebook can still be materializing the group's TOC.
            # Keep the one persistent COM session, but send each dependency
            # layer in its own bounded batch so the next call uses a parent ID
            # already accepted by the prior batch.
            max_batches = max(2, len(requests) + 1)
            no_progress_batches = 0
            for batch_index in range(1, max_batches + 1):
                if not pending:
                    break
                opened_by_key = {
                    item["relative_path"]: item["object_id"] for item in opened
                }
                ready: list[dict[str, Any]] = []
                deferred: list[dict[str, Any]] = []
                for request in pending:
                    parent_key = str(request["parent_key"])
                    if parent_key and parent_key not in opened_by_key:
                        deferred.append(request)
                    else:
                        request_key = str(request["key"])
                        if open_attempt_counts.get(request_key, 0) >= 2:
                            raise RunnerFailure(
                                "Materialized hierarchy batch exhausted its retry budget for "
                                f"{request['relative_path']}."
                            )
                        ready.append(request)
                if not ready:
                    raise RunnerFailure(
                        "Materialized hierarchy batch has no request whose exact parent "
                        "was activated."
                    )
                batch_requests: list[dict[str, Any]] = []
                for request in ready:
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
                    for request in ready:
                        request_key = str(request["key"])
                        open_attempt_counts[request_key] = (
                            open_attempt_counts.get(request_key, 0) + 1
                        )
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
                    if no_progress_batches == 0:
                        no_progress_batches += 1
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
                # Opening a SectionGroup can make its nested .one Sections
                # visible in the same Notebook hierarchy response.  Treat
                # that exact typed/path-bound observation as activation, and
                # only issue a later OpenHierarchy for nodes still missing.
                # This avoids re-opening an already materialized child solely
                # because it was deferred at the start of this batch.
                pending = observe_batch(response, pending, batch_index)
                if len(pending) < len(ready) + len(deferred):
                    no_progress_batches = 0
                else:
                    no_progress_batches += 1
                save("running")
                if no_progress_batches >= 2:
                    break
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

    def _read_lease(self) -> dict[str, Any]:
        lease = read_json(self.lease_path)
        if lease.get("schema_version") not in {1, 2}:
            raise RestoreFailure("Unsupported lifecycle lease schema.")
        return lease

    def _record_close_not_submitted(
        self,
        lease: dict[str, Any],
        exc: BaseException,
        *,
        phase: str,
    ) -> None:
        lease.update(
            state="active",
            close_not_submitted={
                "status": "close_not_submitted",
                "phase": phase,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "close_submitted": False,
                "recorded_at": utc_now(),
            },
        )
        write_json(self.lease_path, lease)

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
        except OneNoteBridgeError as exc:
            raise RestoreFailure(
                f"Lifecycle could not read the leased Notebook: {exc}"
            ) from exc
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
        notebooks = self._hierarchy.list_notebooks()["items"]
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
            notebooks = self._hierarchy.list_notebooks()["items"]
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

    def close_exact_notebook(
        self,
        *,
        sync_to_disk: bool = False,
    ) -> dict[str, Any]:
        self.progress.unit_started("lifecycle", f"{self.role} close", 1, 1)
        lease = self._read_lease()
        if lease.get("state") != "active":
            raise RestoreFailure("Lifecycle lease is not active; refusing source Notebook close.")
        persistence_sync = {
            "requested": sync_to_disk,
            "accepted": False,
            "completion_proof": "CloseNotebook(force=false)" if sync_to_disk else None,
        }
        started = time.perf_counter()
        try:
            current = self.get_exact_notebook(lease)
        except Exception as exc:
            self._record_close_not_submitted(lease, exc, phase="identity_read")
            if isinstance(exc, RestoreFailure):
                raise
            raise RestoreFailure(f"Exact source Notebook close failed: {exc}") from exc
        if sync_to_disk:
            try:
                self._bridge.call(
                    "sync_hierarchy",
                    object_id=str(current["id"]),
                )
                persistence_sync["accepted"] = True
            except Exception as exc:
                lease.update(
                    persistence_sync_failed_at=utc_now(),
                    persistence_sync_error=f"{type(exc).__name__}: {exc}",
                )
                write_json(self.lease_path, lease)
                raise RestoreFailure(
                    "Exact Notebook cache-publish persistence sync failed before close; "
                    "the active lease was preserved for normal failure finalization."
                ) from exc
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
                "persistence_sync": persistence_sync,
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
            lease.update(
                state="close_failed",
                close_failed_at=utc_now(),
                close_error=str(exc),
                close_submitted=True,
            )
            write_json(self.lease_path, lease)
            if isinstance(exc, RestoreFailure):
                raise
            raise RestoreFailure(f"Exact source Notebook close failed: {exc}") from exc

    def adopt_production_close(self, result: Mapping[str, Any]) -> dict[str, Any]:
        """Seal an active lease from exact production Close Tool evidence."""

        lease = self._read_lease()
        if lease.get("state") != "active":
            raise RestoreFailure(
                "Lifecycle lease is not active; refusing production close handoff."
            )
        if lease.get("role", self.role) != self.role:
            raise RestoreFailure(
                "Lifecycle lease role differs from its frozen wrapper role."
            )
        notebook_id = str(lease.get("notebook_id", ""))
        expected_name = str(lease.get("expected_name", ""))
        expected_path = Path(str(lease.get("expected_local_path", ""))).resolve()
        item = result.get("item")
        final_state = result.get("final_state")
        convergence = result.get("convergence")
        reconciliation = result.get("reconciliation")
        if (
            not notebook_id
            or not expected_name
            or expected_path.parent != self.notebook_root
            or not expected_path.exists()
        ):
            raise RestoreFailure(
                "Production close handoff does not retain the exact local lease binding."
            )
        if (
            result.get("ok") is not True
            or result.get("closed") is not True
            or not isinstance(item, Mapping)
            or str(item.get("id", "")) != notebook_id
            or display_name(item) != expected_name
        ):
            raise RestoreFailure(
                "Production close handoff does not match the exact leased Notebook."
            )
        if final_state is not None and (
            not isinstance(final_state, Mapping)
            or str(final_state.get("id", "")) != notebook_id
            or final_state.get("is_open") is not False
        ):
            raise RestoreFailure(
                "Production close handoff contains an invalid final Notebook state."
            )
        if (
            not isinstance(convergence, Mapping)
            or convergence.get("converged") is not True
            or int(convergence.get("attempts", 0)) < 2
            or int(convergence.get("stable_observations", 0)) < 2
        ):
            raise RestoreFailure(
                "Production close handoff lacks two stable closed-state observations."
            )
        if (
            not isinstance(reconciliation, Mapping)
            or reconciliation.get("state") != "applied"
            or reconciliation.get("mutation_attempted") is not True
            or int(reconciliation.get("mutation_attempts", 0)) != 1
            or reconciliation.get("mutation_replayed") is not False
            or reconciliation.get("observed_outcome") != "applied"
        ):
            raise RestoreFailure(
                "Production close handoff violates the single-attempt reconciliation contract."
            )

        close_result = {
            "closed": True,
            "source_notebook_id": notebook_id,
            "close_before": stable_item(item),
            "final_state": stable_item(final_state) if final_state else None,
            "elapsed_seconds": convergence.get("elapsed_seconds"),
            "convergence": dict(convergence),
            "reconciliation": {
                key: reconciliation.get(key)
                for key in (
                    "state",
                    "mutation_stage",
                    "mutation_attempted",
                    "mutation_attempts",
                    "mutation_replayed",
                    "observed_outcome",
                    "retry_safety",
                    "recommended_action",
                )
            },
            "close_origin": "production_close_notebook",
            "persistence_sync": {
                "requested": False,
                "accepted": False,
                "completion_proof": None,
            },
            "filesystem_deleted": False,
        }
        lease.update(
            state="closed",
            closed_at=utc_now(),
            close_result=close_result,
        )
        write_json(self.lease_path, lease)
        return close_result

__all__ = [
    "MAX_MATERIALIZED_HIERARCHY_ENTRIES",
    "NotebookLifecycleWrapper",
]
