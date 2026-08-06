"""Audited MCP stdio client used by the manual isolated smoke tests."""

from __future__ import annotations

import asyncio
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


POLICY_ENV_NAMES = {
    "writes_enabled": "LOCAL_ONENOTE_ENABLE_WRITES",
    "deletes_enabled": "LOCAL_ONENOTE_ENABLE_DELETES",
    "permanent_deletes_enabled": "LOCAL_ONENOTE_ENABLE_PERMANENT_DELETES",
    "experimental_move_section_enabled": "LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_MOVE_SECTION",
    "experimental_copy_enabled": "LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_COPY",
    "reconstructive_move_page_enabled": "LOCAL_ONENOTE_ENABLE_RECONSTRUCTIVE_MOVE_PAGE",
    "raw_xml_enabled": "LOCAL_ONENOTE_ENABLE_RAW_XML",
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
MUTATION_TOOL_PREFIXES = (
    "add_",
    "append_",
    "close_",
    "copy_",
    "create_",
    "delete_",
    "merge_",
    "move_",
    "navigate_",
    "open_",
    "publish_",
    "reconstructive_",
    "rename_",
    "reorder_",
    "replace_",
    "set_",
    "sync_",
    "update_",
)


def is_mutation_tool(name: str) -> bool:
    return name.startswith(MUTATION_TOOL_PREFIXES)


@dataclass(frozen=True)
class ScenarioPolicy:
    writes_enabled: bool = False
    deletes_enabled: bool = False
    permanent_deletes_enabled: bool = False
    experimental_move_section_enabled: bool = False
    experimental_copy_enabled: bool = False
    reconstructive_move_page_enabled: bool = False
    raw_xml_enabled: bool = False

    def as_dict(self) -> dict[str, bool]:
        return {
            "writes_enabled": self.writes_enabled,
            "deletes_enabled": self.deletes_enabled,
            "permanent_deletes_enabled": self.permanent_deletes_enabled,
            "experimental_move_section_enabled": self.experimental_move_section_enabled,
            "experimental_copy_enabled": self.experimental_copy_enabled,
            "reconstructive_move_page_enabled": self.reconstructive_move_page_enabled,
            "raw_xml_enabled": self.raw_xml_enabled,
        }


READ_ONLY_POLICY = ScenarioPolicy()
WRITE_POLICY = ScenarioPolicy(writes_enabled=True)
MOVE_POLICY = ScenarioPolicy(writes_enabled=True, experimental_move_section_enabled=True)
DELETE_POLICY = ScenarioPolicy(deletes_enabled=True)
COPY_POLICY = ScenarioPolicy(
    writes_enabled=True,
    deletes_enabled=True,
    experimental_copy_enabled=True,
)
COPY_NO_DELETE_POLICY = ScenarioPolicy(
    writes_enabled=True,
    experimental_copy_enabled=True,
)
RECONSTRUCTIVE_MOVE_PAGE_POLICY = ScenarioPolicy(
    writes_enabled=True,
    deletes_enabled=True,
    experimental_copy_enabled=True,
    reconstructive_move_page_enabled=True,
)


class ClientFailure(RuntimeError):
    """Raised when transport, policy, allowlist, or tool validation fails."""

    def __init__(self, message: str, *, envelope: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.envelope = envelope


def build_server_env(
    policy: ScenarioPolicy,
    temp_dir: Path,
    timeout_seconds: int = 180,
    bridge_audit_path: Path | None = None,
) -> dict[str, str]:
    """Build a complete child env, overriding every mutation switch exactly."""

    temp_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    for field, env_name in POLICY_ENV_NAMES.items():
        env[env_name] = "true" if getattr(policy, field) else "false"
    for _field, (env_name, value) in COPY_BUDGET_ENV.items():
        env[env_name] = str(value)
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

    sensitive = {"xml", "base64", "content", "text"}
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
    ) -> None:
        self.policy = policy
        self.allowed_tools = set(allowed_tools) | {"health_check"}
        self.run_dir = run_dir
        self.timeout_seconds = timeout_seconds
        self._stack = AsyncExitStack()
        self._session: ClientSession | None = None
        self.process_started = False
        self.available_tools: set[str] = set()
        self.health_result: dict[str, Any] | None = None

    async def __aenter__(self) -> "MCPStdioClient":
        try:
            self.run_dir.mkdir(parents=True, exist_ok=True)
            stderr_file = (self.run_dir / "server.stderr.log").open("a", encoding="utf-8")
            self._stack.callback(stderr_file.close)
            parameters = StdioServerParameters(
                command=sys.executable,
                args=["-m", "local_onenote_mcp.server"],
                cwd=Path(__file__).resolve().parents[2],
                env=build_server_env(
                    self.policy,
                    self.run_dir / "temp",
                    self.timeout_seconds,
                    self.run_dir / "bridge-calls.jsonl",
                ),
                encoding="utf-8",
                encoding_error_handler="replace",
            )
            read_stream, write_stream = await self._stack.enter_async_context(
                stdio_client(parameters, errlog=stderr_file)
            )
            self.process_started = True
            self._session = await self._stack.enter_async_context(ClientSession(read_stream, write_stream))
            await asyncio.wait_for(self._session.initialize(), timeout=self.timeout_seconds)
            listed = await asyncio.wait_for(self._session.list_tools(), timeout=self.timeout_seconds)
            self.available_tools = {tool.name for tool in listed.tools}
            missing = sorted(self.allowed_tools - self.available_tools)
            if missing:
                raise ClientFailure(f"Server is missing required tools: {', '.join(missing)}")
            health = await self.call_tool("health_check", {}, retry_read=False)
            self.health_result = health
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
            return self
        except BaseException:
            await self._stack.aclose()
            raise

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
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
        mutation = is_mutation_tool(name)
        attempts = 1 if mutation or not retry_read else 2
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            started = asyncio.get_running_loop().time()
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
                if envelope.get("ok") is not True or envelope.get("complete") is not True:
                    code = envelope.get("code", "operation_failed")
                    message = envelope.get("error", "tool call did not complete")
                    raise ClientFailure(
                        f"{name} failed ({code}): {message}",
                        envelope=envelope,
                    )
                return envelope
            except ClientFailure as exc:
                if not recorded:
                    record["completed_at"] = datetime.now(timezone.utc).isoformat()
                    record["elapsed_seconds"] = round(
                        asyncio.get_running_loop().time() - started,
                        6,
                    )
                    record["client_error"] = f"{type(exc).__name__}: {exc}"
                    self._append_audit(record)
                raise
            except Exception as exc:  # transport errors need a clean audit trail
                last_error = exc
                record["completed_at"] = datetime.now(timezone.utc).isoformat()
                record["elapsed_seconds"] = round(asyncio.get_running_loop().time() - started, 6)
                record["transport_error"] = f"{type(exc).__name__}: {exc}"
                self._append_audit(record)
                if attempt == attempts:
                    break
        raise ClientFailure(f"{name} transport failed after {attempts} attempt(s): {last_error}")

    def _append_audit(self, record: dict[str, Any]) -> None:
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
        yield existing
        return
    async with client_factory(
        policy=policy,
        allowed_tools=allowed_tools,
        run_dir=run_dir,
        timeout_seconds=timeout_seconds,
    ) as created:
        yield created
