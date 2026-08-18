"""Audited MCP stdio client used by the manual isolated smoke tests."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from local_onenote_mcp.bridge import OneNoteBridge
from local_onenote_mcp.constants import PAGE_INFO, XML_SCHEMA_2013
from local_onenote_mcp.tool_surface import INTERNAL_CAPABILITY_NAMES

from .progress import RunProgressReporter


POLICY_ENV_NAMES = {
    "writes_enabled": "LOCAL_ONENOTE_ENABLE_WRITES",
    "deletes_enabled": "LOCAL_ONENOTE_ENABLE_DELETES",
    "organize_enabled": "LOCAL_ONENOTE_ENABLE_ORGANIZE",
    "create_enabled": "LOCAL_ONENOTE_ENABLE_CREATE",
    "local_file_io_enabled": "LOCAL_ONENOTE_ENABLE_LOCAL_FILE_IO",
    "ui_control_enabled": "LOCAL_ONENOTE_ENABLE_UI_CONTROL",
    "notebook_lifecycle_enabled": "LOCAL_ONENOTE_ENABLE_NOTEBOOK_LIFECYCLE",
}
COPY_BUDGET_ENV = {
    "max_resources": ("LOCAL_ONENOTE_MAX_COPY_RESOURCES", 1_000),
    "max_pages": ("LOCAL_ONENOTE_MAX_COPY_PAGES", 200),
    "max_content_objects": ("LOCAL_ONENOTE_MAX_COPY_CONTENT_OBJECTS", 10_000),
    "max_page_xml_bytes": ("LOCAL_ONENOTE_MAX_COPY_PAGE_XML_BYTES", 32 * 1024 * 1024),
    "max_total_xml_bytes": ("LOCAL_ONENOTE_MAX_COPY_TOTAL_XML_BYTES", 256 * 1024 * 1024),
    "max_plan_seconds": ("LOCAL_ONENOTE_MAX_COPY_PLAN_SECONDS", 300),
    "max_execute_seconds": ("LOCAL_ONENOTE_MAX_COPY_EXECUTE_SECONDS", 1_800),
}
BATCH_MUTATION_BUDGET_ENV = {
    "max_catalog_resources": ("LOCAL_ONENOTE_MAX_BATCH_CATALOG_RESOURCES", 100_000),
    "max_effective_resources": ("LOCAL_ONENOTE_MAX_BATCH_EFFECTIVE_RESOURCES", 1_000),
    "max_effective_pages": ("LOCAL_ONENOTE_MAX_BATCH_EFFECTIVE_PAGES", 200),
    "max_direct_siblings": ("LOCAL_ONENOTE_MAX_BATCH_DIRECT_SIBLINGS", 1_000),
    "max_page_content_chars": ("LOCAL_ONENOTE_MAX_BATCH_PAGE_CONTENT_CHARS", 500_000),
}
SEARCH_BUDGET_ENV = {
    "max_pages": ("LOCAL_ONENOTE_MAX_SEARCH_PAGES", 1_000),
    "max_page_chars": ("LOCAL_ONENOTE_MAX_SEARCH_PAGE_CHARS", 100_000),
    "max_total_chars": ("LOCAL_ONENOTE_MAX_SEARCH_TOTAL_CHARS", 2_000_000),
    "max_seconds": ("LOCAL_ONENOTE_MAX_SEARCH_SECONDS", 30),
    "snippet_chars": ("LOCAL_ONENOTE_MAX_SEARCH_SNIPPET_CHARS", 400),
}
MUTATION_TOOL_PREFIXES = (
    "add_",
    "append_",
    "close_",
    "copy_",
    "create_",
    "delete_",
    "export_",
    "merge_",
    "move_",
    "navigate_",
    "open_",
    "publish_",
    "reparent_",
    "rename_",
    "reorder_",
    "request_",
    "replace_",
    "set_",
    "sort_",
    "sync_",
    "update_",
)


def is_mutation_tool(name: str) -> bool:
    return name.startswith(MUTATION_TOOL_PREFIXES)


@dataclass(frozen=True)
class ScenarioPolicy:
    writes_enabled: bool = False
    deletes_enabled: bool = False
    organize_enabled: bool = False
    create_enabled: bool = False
    local_file_io_enabled: bool = False
    ui_control_enabled: bool = False
    notebook_lifecycle_enabled: bool = False

    def as_dict(self) -> dict[str, bool]:
        return {
            "writes_enabled": self.writes_enabled,
            "deletes_enabled": self.deletes_enabled,
            "organize_enabled": self.organize_enabled,
            "create_enabled": self.create_enabled,
            "local_file_io_enabled": self.local_file_io_enabled,
            "ui_control_enabled": self.ui_control_enabled,
            "notebook_lifecycle_enabled": self.notebook_lifecycle_enabled,
        }


READ_ONLY_POLICY = ScenarioPolicy()
WRITE_POLICY = ScenarioPolicy(create_enabled=True, writes_enabled=True)
RICH_WRITE_POLICY = ScenarioPolicy(
    create_enabled=True,
    writes_enabled=True,
    local_file_io_enabled=True,
)
REPARENT_POLICY = ScenarioPolicy(
    create_enabled=True,
    writes_enabled=True,
    organize_enabled=True,
)
RICH_REPARENT_POLICY = ScenarioPolicy(
    create_enabled=True,
    writes_enabled=True,
    organize_enabled=True,
    local_file_io_enabled=True,
)
REORDER_SECTION_POLICY = ScenarioPolicy(create_enabled=True, writes_enabled=True)
REORDER_SECTION_GROUP_POLICY = ScenarioPolicy(create_enabled=True, writes_enabled=True)
DELETE_POLICY = ScenarioPolicy(deletes_enabled=True)
COPY_POLICY = ScenarioPolicy(
    writes_enabled=True,
    deletes_enabled=True,
    create_enabled=True,
)
RICH_COPY_POLICY = ScenarioPolicy(
    writes_enabled=True,
    deletes_enabled=True,
    create_enabled=True,
    local_file_io_enabled=True,
)
COPY_NO_DELETE_POLICY = ScenarioPolicy(
    writes_enabled=True,
    create_enabled=True,
)
RICH_COPY_NO_DELETE_POLICY = ScenarioPolicy(
    writes_enabled=True,
    create_enabled=True,
    local_file_io_enabled=True,
)
RICH_COPY_NOTEBOOK_POLICY = ScenarioPolicy(
    writes_enabled=True,
    create_enabled=True,
    local_file_io_enabled=True,
    notebook_lifecycle_enabled=True,
)
MOVE_PAGE_POLICY = ScenarioPolicy(
    writes_enabled=True,
    deletes_enabled=True,
    create_enabled=True,
)
MOVE_CONTAINERS_POLICY = ScenarioPolicy(
    writes_enabled=True,
    deletes_enabled=True,
    create_enabled=True,
)


class ClientFailure(RuntimeError):
    """Raised when transport, policy, allowlist, or tool validation fails."""

    def __init__(self, message: str, *, envelope: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.envelope = envelope

    @property
    def error_code(self) -> str | None:
        """Return the public error code from the nested MCP failure envelope."""

        envelope = self.envelope or {}
        error = envelope.get("error")
        value = error.get("code") if isinstance(error, dict) else envelope.get("code")
        return str(value) if value is not None else None

    @property
    def error_message(self) -> str:
        """Return the public error message without assuming a legacy flat envelope."""

        envelope = self.envelope or {}
        error = envelope.get("error")
        if isinstance(error, dict):
            value = error.get("message")
        else:
            value = error if error is not None else envelope.get("message")
        return str(value) if value is not None else str(self)


def build_server_env(
    policy: ScenarioPolicy,
    temp_dir: Path,
    timeout_seconds: int = 180,
    bridge_audit_path: Path | None = None,
    search_budget: dict[str, int] | None = None,
    batch_mutation_budget: dict[str, int] | None = None,
) -> dict[str, str]:
    """Build a complete child env, overriding every mutation switch exactly."""

    temp_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    for field, env_name in POLICY_ENV_NAMES.items():
        env[env_name] = "true" if getattr(policy, field) else "false"
    for _field, (env_name, value) in COPY_BUDGET_ENV.items():
        env[env_name] = str(value)
    unknown_batch_fields = set(batch_mutation_budget or {}) - set(
        BATCH_MUTATION_BUDGET_ENV
    )
    if unknown_batch_fields:
        raise ValueError(
            "Unknown Batch Mutation budget fields: "
            + ", ".join(sorted(unknown_batch_fields))
        )
    for field, (env_name, default) in BATCH_MUTATION_BUDGET_ENV.items():
        env[env_name] = str((batch_mutation_budget or {}).get(field, default))
    unknown_search_fields = set(search_budget or {}) - set(SEARCH_BUDGET_ENV)
    if unknown_search_fields:
        raise ValueError(
            "Unknown Search budget fields: " + ", ".join(sorted(unknown_search_fields))
        )
    for field, (env_name, default) in SEARCH_BUDGET_ENV.items():
        env[env_name] = str((search_budget or {}).get(field, default))
    env["TEMP"] = str(temp_dir.resolve())
    env["TMP"] = str(temp_dir.resolve())
    env["LOCAL_ONENOTE_MCP_TIMEOUT"] = str(timeout_seconds)
    if bridge_audit_path is not None:
        env["LOCAL_ONENOTE_BRIDGE_AUDIT_PATH"] = str(bridge_audit_path.resolve())
    else:
        env.pop("LOCAL_ONENOTE_BRIDGE_AUDIT_PATH", None)
    return env


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump())
    return repr(value)


def summarize(value: Any, *, key: str = "") -> Any:
    """Return a bounded audit representation without content/XML/base64 data."""

    sensitive = {"xml", "base64", "content", "text", "html", "query", "snippet"}
    if key.casefold() in sensitive and isinstance(value, str):
        return {
            "redacted": True,
            "chars": len(value),
            "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
        }
    if isinstance(value, dict):
        return {str(name): summarize(item, key=str(name)) for name, item in value.items()}
    if isinstance(value, list):
        if len(value) > 50:
            return {
                "items": [summarize(item) for item in value[:50]],
                "total_items": len(value),
                "truncated": True,
            }
        return [summarize(item) for item in value]
    if isinstance(value, str) and len(value) > 1000:
        return {
            "redacted": True,
            "chars": len(value),
            "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
        }
    return _json_safe(value)


def parse_tool_result(result: Any) -> dict[str, Any]:
    """Extract FastMCP's structured envelope with a text-content fallback."""

    if getattr(result, "isError", False):
        raise ClientFailure("MCP returned a protocol-level tool error.")
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        if set(structured) == {"result"} and isinstance(structured["result"], dict):
            return structured["result"]
        return structured
    for block in getattr(result, "content", []):
        text = getattr(block, "text", None)
        if not isinstance(text, str):
            continue
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ClientFailure("MCP tool returned no JSON object envelope.")


class MCPStdioClient:
    """One scenario-scoped server process with a strict local tool allowlist."""

    def __init__(
        self,
        *,
        policy: ScenarioPolicy,
        allowed_tools: set[str],
        run_dir: Path,
        timeout_seconds: int,
        search_budget: dict[str, int] | None = None,
        batch_mutation_budget: dict[str, int] | None = None,
        progress: RunProgressReporter | None = None,
        require_desktop_ready: bool = True,
        persist_runtime_logs: bool = True,
    ) -> None:
        self.policy = policy
        self.allowed_tools = set(allowed_tools) | {"health_check"}
        self.run_dir = run_dir
        self.timeout_seconds = timeout_seconds
        self.search_budget = {
            field: (search_budget or {}).get(field, default)
            for field, (_env_name, default) in SEARCH_BUDGET_ENV.items()
        }
        self.batch_mutation_budget = {
            field: (batch_mutation_budget or {}).get(field, default)
            for field, (_env_name, default) in BATCH_MUTATION_BUDGET_ENV.items()
        }
        self.progress = progress or RunProgressReporter.disabled()
        self.require_desktop_ready = require_desktop_ready
        self.persist_runtime_logs = persist_runtime_logs
        self._stack = AsyncExitStack()
        self._session: ClientSession | None = None
        self.process_started = False
        self.available_tools: set[str] = set()
        self.health_result: dict[str, Any] | None = None
        self.health_failure_envelope: dict[str, Any] | None = None
        self._internal_bridge = OneNoteBridge(
            timeout_seconds=timeout_seconds,
            audit_path=(
                run_dir / "internal-capability-calls.jsonl"
                if persist_runtime_logs
                else None
            ),
        )
        self._scenario_before_snapshots: dict[str, dict[str, Any]] = {}
        self._scenario_before_handoff: dict[str, Any] | None = None
        self._scenario_before_handoff_path: Path | None = None

    async def __aenter__(self) -> "MCPStdioClient":
        try:
            self.run_dir.mkdir(parents=True, exist_ok=True)
            if self.persist_runtime_logs:
                stderr_target = (self.run_dir / "server.stderr.log").open(
                    "a", encoding="utf-8"
                )
                self._stack.callback(stderr_target.close)
            else:
                stderr_target = sys.stderr
            parameters = StdioServerParameters(
                command=sys.executable,
                args=["-m", "local_onenote_mcp.server"],
                cwd=Path(__file__).resolve().parents[2],
                env=build_server_env(
                    self.policy,
                    self.run_dir / "temp",
                    self.timeout_seconds,
                    (
                        self.run_dir / "bridge-calls.jsonl"
                        if self.persist_runtime_logs
                        else None
                    ),
                    self.search_budget,
                    self.batch_mutation_budget,
                ),
                encoding="utf-8",
                encoding_error_handler="replace",
            )
            read_stream, write_stream = await self._stack.enter_async_context(
                stdio_client(parameters, errlog=stderr_target)
            )
            self.process_started = True
            self._session = await self._stack.enter_async_context(ClientSession(read_stream, write_stream))
            await asyncio.wait_for(self._session.initialize(), timeout=self.timeout_seconds)
            listed = await asyncio.wait_for(self._session.list_tools(), timeout=self.timeout_seconds)
            self.available_tools = {tool.name for tool in listed.tools}
            missing = sorted(
                self.allowed_tools - self.available_tools - INTERNAL_CAPABILITY_NAMES
            )
            if missing:
                raise ClientFailure(f"Server is missing required tools: {', '.join(missing)}")
            health = await self.call_health_preflight(
                allow_desktop_not_running=not self.require_desktop_ready
            )
            self.health_result = health
            if self.health_failure_envelope is not None:
                return self
            self.validate_health_contract(
                health,
                require_desktop_ready=self.require_desktop_ready,
            )
            return self
        except BaseException:
            await self._stack.aclose()
            raise

    def validate_health_contract(
        self,
        health: dict[str, Any],
        *,
        require_desktop_ready: bool,
    ) -> None:
        """Validate a successful health result against this frozen client profile."""

        desktop = health.get("onenote_desktop")
        if not isinstance(desktop, dict):
            raise ClientFailure(
                "OneNote Desktop health preflight returned malformed readiness evidence."
            )
        if require_desktop_ready and desktop.get("ready") is not True:
            raise ClientFailure(
                "OneNote Desktop health preflight did not prove an existing visible GUI."
            )
        actual = health.get("mutation_policy")
        expected = self.policy.as_dict()
        if actual != expected:
            raise ClientFailure(f"Mutation policy mismatch: expected {expected}, received {actual}")
        if health.get("timeout_seconds") != self.timeout_seconds:
            raise ClientFailure(
                "Server bridge timeout mismatch: "
                f"expected {self.timeout_seconds}, received {health.get('timeout_seconds')}"
            )
        expected_copy_budget = {
            field: value for field, (_env_name, value) in COPY_BUDGET_ENV.items()
        }
        if health.get("copy_budget") != expected_copy_budget:
            raise ClientFailure(
                "Copy budget mismatch: "
                f"expected {expected_copy_budget}, received {health.get('copy_budget')}"
            )
        if health.get("search_budget") != self.search_budget:
            raise ClientFailure(
                "Search budget mismatch: "
                f"expected {self.search_budget}, received {health.get('search_budget')}"
            )
        if health.get("batch_mutation_budget") != self.batch_mutation_budget:
            raise ClientFailure(
                "Batch Mutation budget mismatch: "
                f"expected {self.batch_mutation_budget}, "
                f"received {health.get('batch_mutation_budget')}"
            )

    @staticmethod
    def _expected_desktop_not_running_health(
        exc: ClientFailure,
    ) -> dict[str, Any] | None:
        envelope = exc.envelope
        if not isinstance(envelope, dict):
            return None
        error = envelope.get("error")
        if not isinstance(error, dict) or error.get("code") != "onenote_desktop_not_running":
            return None
        details = error.get("details")
        desktop = details.get("onenote_desktop") if isinstance(details, dict) else None
        execution = envelope.get("execution")
        if (
            not isinstance(desktop, dict)
            or desktop.get("process_running") is not False
            or desktop.get("visible_window_present") is not False
            or desktop.get("ready") is not False
            or not isinstance(execution, dict)
            or execution.get("operation") != "health_check"
            or execution.get("backend_calls") != 0
        ):
            return None
        return {
            "ok": False,
            "onenote_desktop": dict(desktop),
            "expected_failure_envelope": envelope,
        }

    async def call_health_preflight(
        self,
        *,
        allow_desktop_not_running: bool,
    ) -> dict[str, Any]:
        """Call health, optionally admitting its exact fully-stopped typed failure."""

        try:
            health = await self.call_tool("health_check", {}, retry_read=False)
        except ClientFailure as exc:
            stopped = self._expected_desktop_not_running_health(exc)
            if not allow_desktop_not_running or stopped is None:
                raise
            self.health_failure_envelope = exc.envelope
            return stopped
        self.health_failure_envelope = None
        return health

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self._discard_scenario_before_snapshots("client_exit")
        await self._stack.aclose()

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        retry_read: bool = True,
    ) -> dict[str, Any]:
        if name not in self.allowed_tools:
            raise ClientFailure(f"Tool '{name}' is outside this scenario's allowlist.")
        if self._session is None:
            raise ClientFailure("MCP session is not initialized.")
        arguments = arguments or {}
        if name == "get_page_xml":
            return await self._call_internal_page_xml(arguments)
        mutation = is_mutation_tool(name)
        if mutation and self._scenario_before_snapshots:
            pending_count = len(self._scenario_before_snapshots)
            self._discard_scenario_before_snapshots(
                f"mutation_blocked_before_snapshot_consumption:{name}"
            )
            raise ClientFailure(
                f"Mutation '{name}' was blocked because {pending_count} materialized "
                "scenario before snapshot role(s) had not been consumed. The mutation "
                "was not called."
            )
        attempts = 1 if mutation or not retry_read else 2
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            started = asyncio.get_running_loop().time()
            self.progress.tool_started(name, attempt, mutation=mutation)
            recorded = False
            record: dict[str, Any] = {
                "tool": name,
                "attempt": attempt,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "arguments": summarize(arguments),
            }
            try:
                result = await self._session.call_tool(
                    name,
                    arguments,
                    read_timeout_seconds=timedelta(seconds=self.timeout_seconds),
                )
                envelope = parse_tool_result(result)
                record["completed_at"] = datetime.now(timezone.utc).isoformat()
                record["elapsed_seconds"] = round(asyncio.get_running_loop().time() - started, 6)
                record["result"] = summarize(envelope)
                self._append_audit(record)
                recorded = True
                self.progress.tool_completed(
                    name,
                    attempt,
                    mutation=mutation,
                    elapsed_seconds=float(record["elapsed_seconds"]),
                    envelope=envelope,
                )
                if envelope.get("ok") is not True:
                    error_payload = envelope.get("error")
                    if not isinstance(error_payload, dict):
                        error_payload = {}
                    code = error_payload.get("code", "operation_failed")
                    message = error_payload.get("message", "tool call failed")
                    raise ClientFailure(
                        f"{name} failed ({code}): {message}",
                        envelope=envelope,
                    )
                payload = envelope.get("result")
                if not isinstance(payload, dict):
                    raise ClientFailure(
                        f"{name} returned a malformed success result.",
                        envelope=envelope,
                    )
                # Scenario implementations predate the public response envelope and
                # intentionally consume business-result fields at the top level. Keep
                # that compatibility surface, but do not discard the envelope metadata:
                # convergence validation needs the production Operation Runtime record.
                flattened = dict(payload)
                flattened["ok"] = True
                flattened["warnings"] = envelope.get("warnings", [])
                flattened["execution"] = envelope.get("execution", {})
                return flattened
            except ClientFailure as exc:
                if not recorded:
                    record["completed_at"] = datetime.now(timezone.utc).isoformat()
                    record["elapsed_seconds"] = round(
                        asyncio.get_running_loop().time() - started,
                        6,
                    )
                    record["client_error"] = f"{type(exc).__name__}: {exc}"
                    self._append_audit(record)
                    self.progress.tool_failed(
                        name,
                        attempt,
                        mutation=mutation,
                        elapsed_seconds=float(record["elapsed_seconds"]),
                        error_type=type(exc).__name__,
                    )
                raise
            except Exception as exc:  # transport errors need a clean audit trail
                last_error = exc
                record["completed_at"] = datetime.now(timezone.utc).isoformat()
                record["elapsed_seconds"] = round(asyncio.get_running_loop().time() - started, 6)
                record["transport_error"] = f"{type(exc).__name__}: {exc}"
                self._append_audit(record)
                self.progress.tool_failed(
                    name,
                    attempt,
                    mutation=mutation,
                    elapsed_seconds=float(record["elapsed_seconds"]),
                    error_type=type(exc).__name__,
                )
                if attempt == attempts:
                    break
        raise ClientFailure(f"{name} transport failed after {attempts} attempt(s): {last_error}")

    async def _call_internal_page_xml(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Read raw Page XML solely for isolated validation evidence.

        This capability deliberately bypasses MCP registration. It remains local to the
        manual-validation harness and is covered by the scenario allowlist and bridge audit.
        """

        page_id = str(arguments.get("page_id", "")).strip()
        page_info = str(arguments.get("page_info", "all")).strip().casefold()
        if not page_id:
            raise ClientFailure("Internal get_page_xml requires a non-empty page_id.")
        if page_info not in PAGE_INFO:
            raise ClientFailure(f"Internal get_page_xml received invalid page_info: {page_info}")
        started = asyncio.get_running_loop().time()
        self.progress.tool_started("get_page_xml", 1, mutation=False)
        record: dict[str, Any] = {
            "tool": "get_page_xml",
            "surface": "internal_validation_capability",
            "attempt": 1,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "arguments": summarize(arguments),
        }
        try:
            payload = await asyncio.to_thread(
                self._internal_bridge.call,
                "get_page_content",
                page_id=page_id,
                page_info=PAGE_INFO[page_info],
                schema=XML_SCHEMA_2013,
            )
            record["completed_at"] = datetime.now(timezone.utc).isoformat()
            record["elapsed_seconds"] = round(
                asyncio.get_running_loop().time() - started,
                6,
            )
            record["result"] = summarize(payload)
            self._append_audit(record)
            self.progress.tool_completed(
                "get_page_xml",
                1,
                mutation=False,
                elapsed_seconds=float(record["elapsed_seconds"]),
                envelope={"ok": True, "result": payload},
            )
            return payload
        except Exception as exc:
            record["completed_at"] = datetime.now(timezone.utc).isoformat()
            record["elapsed_seconds"] = round(
                asyncio.get_running_loop().time() - started,
                6,
            )
            record["internal_error"] = f"{type(exc).__name__}: {exc}"
            self._append_audit(record)
            self.progress.tool_failed(
                "get_page_xml",
                1,
                mutation=False,
                elapsed_seconds=float(record["elapsed_seconds"]),
                error_type=type(exc).__name__,
            )
            raise ClientFailure(f"Internal get_page_xml failed: {exc}") from exc

    def stage_scenario_before_snapshots(
        self,
        role_snapshots: dict[str, dict[str, Any]],
        role_notebook_ids: dict[str, str],
        evidence_path: Path,
    ) -> None:
        """Stage one exact, content-validated before snapshot per materialized role."""

        if self._scenario_before_snapshots or self._scenario_before_handoff is not None:
            raise ClientFailure("Scenario before snapshot handoff was already staged.")
        if not role_snapshots or set(role_snapshots) != set(role_notebook_ids):
            raise ClientFailure("Scenario before snapshot handoff must cover every role exactly.")
        notebook_ids = [str(value) for value in role_notebook_ids.values()]
        if any(not value for value in notebook_ids) or len(notebook_ids) != len(set(notebook_ids)):
            raise ClientFailure("Scenario before snapshot handoff Notebook IDs must be unique.")
        roles: dict[str, Any] = {}
        staged_snapshots: dict[str, dict[str, Any]] = {}
        for role, snapshot in role_snapshots.items():
            notebook_id = str(role_notebook_ids[role])
            if str(snapshot.get("notebook_id", "")) != notebook_id:
                raise ClientFailure(
                    f"Scenario before snapshot role {role} is bound to the wrong Notebook ID."
                )
            payload = json.dumps(
                snapshot,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            staged_snapshots[notebook_id] = deepcopy(snapshot)
            roles[role] = {
                "notebook_id": notebook_id,
                "snapshot_sha256": hashlib.sha256(payload).hexdigest(),
                "page_count": len(snapshot.get("page_hashes", {})),
                "consumed": False,
            }
        self._scenario_before_snapshots = staged_snapshots
        self._scenario_before_handoff_path = evidence_path
        self._scenario_before_handoff = {
            "schema_version": 1,
            "status": "staged",
            "source": "reopen_scenario_before_snapshot",
            "single_use": True,
            "roles": roles,
            "staged_at": datetime.now(timezone.utc).isoformat(),
            "content_exposed": False,
        }
        self._persist_scenario_before_handoff()

    def consume_scenario_before_snapshot(self, notebook_id: str) -> dict[str, Any] | None:
        snapshot = self._scenario_before_snapshots.get(str(notebook_id))
        if snapshot is None:
            if self._scenario_before_snapshots:
                raise ClientFailure(
                    "Scenario requested a before snapshot for an unbound Notebook while an "
                    "exact materialized handoff was pending."
                )
            return None
        if self._scenario_before_handoff is None:
            raise ClientFailure("Scenario before snapshot handoff metadata is missing.")
        matched_role: dict[str, Any] | None = None
        for role in self._scenario_before_handoff["roles"].values():
            if role["notebook_id"] == str(notebook_id):
                matched_role = role
                break
        if matched_role is None:
            raise ClientFailure("Scenario before snapshot handoff has no matching role metadata.")
        payload = json.dumps(
            snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if hashlib.sha256(payload).hexdigest() != matched_role["snapshot_sha256"]:
            raise ClientFailure("Scenario before snapshot handoff digest mismatch.")
        self._scenario_before_snapshots.pop(str(notebook_id))
        matched_role["consumed"] = True
        matched_role["consumed_at"] = datetime.now(timezone.utc).isoformat()
        remaining = len(self._scenario_before_snapshots)
        self._scenario_before_handoff["status"] = (
            "consumed" if remaining == 0 else "partially_consumed"
        )
        self._scenario_before_handoff["remaining_roles"] = remaining
        self._persist_scenario_before_handoff()
        return deepcopy(snapshot)

    def _discard_scenario_before_snapshots(self, reason: str) -> None:
        if not self._scenario_before_snapshots:
            return
        pending = set(self._scenario_before_snapshots)
        self._scenario_before_snapshots.clear()
        if self._scenario_before_handoff is not None:
            for role in self._scenario_before_handoff["roles"].values():
                if role["notebook_id"] in pending and role.get("consumed") is not True:
                    role["discarded"] = True
            self._scenario_before_handoff.update(
                status="discarded",
                discard_reason=reason,
                discarded_at=datetime.now(timezone.utc).isoformat(),
                remaining_roles=0,
            )
            self._persist_scenario_before_handoff()

    def _persist_scenario_before_handoff(self) -> None:
        if self._scenario_before_handoff_path is None or self._scenario_before_handoff is None:
            return
        from .test_utils import write_json

        write_json(self._scenario_before_handoff_path, self._scenario_before_handoff)

    def _append_audit(self, record: dict[str, Any]) -> None:
        if not self.persist_runtime_logs:
            return
        with (self.run_dir / "calls.jsonl").open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


@asynccontextmanager
async def scenario_client(
    existing: MCPStdioClient | None,
    *,
    policy: ScenarioPolicy,
    allowed_tools: set[str],
    run_dir: Path,
    timeout_seconds: int,
    batch_mutation_budget: dict[str, int] | None = None,
    client_factory: type[MCPStdioClient] = MCPStdioClient,
):
    """Reuse the one scenario process without allowing policy/tool expansion."""

    if existing is not None:
        missing = (set(allowed_tools) | {"health_check"}) - existing.allowed_tools
        if missing:
            raise ClientFailure(
                "Existing scenario client is missing required tools: "
                + ", ".join(sorted(missing))
            )
        expected = policy.as_dict()
        actual = existing.policy.as_dict()
        expanded = sorted(name for name, required in expected.items() if required and not actual[name])
        if expanded:
            raise ClientFailure(
                "Existing scenario client policy cannot satisfy required permissions: "
                + ", ".join(expanded)
            )
        if existing.timeout_seconds != timeout_seconds:
            raise ClientFailure("Existing scenario client timeout does not match the scenario contract.")
        if batch_mutation_budget is not None:
            expected_budget = {
                field: batch_mutation_budget.get(field, default)
                for field, (_env_name, default) in BATCH_MUTATION_BUDGET_ENV.items()
            }
            if existing.batch_mutation_budget != expected_budget:
                raise ClientFailure(
                    "Existing scenario client Batch Mutation budget does not match the scenario contract."
                )
        yield existing
        return
    async with client_factory(
        policy=policy,
        allowed_tools=allowed_tools,
        run_dir=run_dir,
        timeout_seconds=timeout_seconds,
        batch_mutation_budget=batch_mutation_budget,
    ) as created:
        yield created
