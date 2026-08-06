"""Narrow trusted wrapper for source Notebook create/get/close lifecycle only."""

from __future__ import annotations

from pathlib import Path
import time
from typing import Any

from local_onenote_mcp.bridge import OneNoteBridge
from local_onenote_mcp.constants import CREATE_FILE_TYPES
from local_onenote_mcp.hierarchy import display_name
from local_onenote_mcp.services.hierarchy import HierarchyService

from .runner import RestoreFailure, RunnerFailure, read_json, stable_item, utc_now, write_json


class NotebookLifecycleWrapper:
    """Expose no arbitrary COM operation beyond exact source Notebook lifecycle."""

    def __init__(
        self,
        run_dir: Path,
        *,
        timeout_seconds: int,
        bridge: OneNoteBridge | None = None,
    ) -> None:
        self.run_dir = run_dir.resolve()
        self.notebook_root = (self.run_dir / "notebooks").resolve()
        self.lease_path = self.run_dir / "lifecycle-lease.json"
        self._bridge = bridge or OneNoteBridge(
            timeout_seconds=timeout_seconds,
            audit_path=self.run_dir / "lifecycle-bridge-calls.jsonl",
        )
        self._hierarchy = HierarchyService(self._bridge)

    def create_fresh_notebook(self, name: str) -> tuple[dict[str, Any], dict[str, Any]]:
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
        return notebook, lease

    def _read_lease(self) -> dict[str, Any]:
        lease = read_json(self.lease_path)
        if lease.get("schema_version") != 1:
            raise RestoreFailure("Unsupported lifecycle lease schema.")
        return lease

    def get_exact_notebook(self, lease: dict[str, Any] | None = None) -> dict[str, Any]:
        lease = lease or self._read_lease()
        notebook_id = str(lease.get("notebook_id", ""))
        expected_name = str(lease.get("expected_name", ""))
        expected_path = Path(str(lease.get("expected_local_path", ""))).resolve()
        if not notebook_id or not expected_name:
            raise RestoreFailure("Lifecycle lease is missing exact Notebook identity fields.")
        if expected_path.parent != self.notebook_root or expected_path.name != expected_name:
            raise RestoreFailure("Lifecycle lease path/name is outside this run's exact binding.")
        if not expected_path.exists():
            raise RestoreFailure("Lifecycle lease local Notebook path no longer exists.")
        try:
            current = self._hierarchy.resource(notebook_id, "notebook")
        except ValueError as exc:
            raise RestoreFailure("Lifecycle lease Notebook ID is no longer active.") from exc
        if str(current.get("id")) != notebook_id or display_name(current) != expected_name:
            raise RestoreFailure("Lifecycle lease ID/name binding no longer matches OneNote state.")
        return current

    def close_exact_notebook(self) -> dict[str, Any]:
        lease = self._read_lease()
        if lease.get("state") != "active":
            raise RestoreFailure("Lifecycle lease is not active; refusing source Notebook close.")
        current = self.get_exact_notebook(lease)
        started = time.perf_counter()
        try:
            self._bridge.call("close_notebook", notebook_id=str(current["id"]), force=False)
            final_state: dict[str, Any] | None = None
            for attempt in range(8):
                try:
                    final_state = self._hierarchy.resource(str(current["id"]), "notebook")
                except ValueError:
                    final_state = None
                if final_state is None or final_state.get("is_open") is False:
                    elapsed = round(time.perf_counter() - started, 6)
                    result = {
                        "closed": True,
                        "source_notebook_id": str(current["id"]),
                        "close_before": stable_item(current),
                        "final_state": stable_item(final_state) if final_state else None,
                        "elapsed_seconds": elapsed,
                        "filesystem_deleted": False,
                    }
                    lease.update(
                        state="closed",
                        closed_at=utc_now(),
                        close_result=result,
                    )
                    write_json(self.lease_path, lease)
                    return result
                if attempt < 7:
                    time.sleep(0.5)
            raise RestoreFailure("Close returned success but the exact source Notebook remains open.")
        except Exception as exc:
            lease.update(state="close_failed", close_failed_at=utc_now(), close_error=str(exc))
            write_json(self.lease_path, lease)
            if isinstance(exc, RestoreFailure):
                raise
            raise RestoreFailure(f"Exact source Notebook close failed: {exc}") from exc

__all__ = ["NotebookLifecycleWrapper"]
