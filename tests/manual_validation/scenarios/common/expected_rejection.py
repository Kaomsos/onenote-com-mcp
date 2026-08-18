"""Content-free evidence for expected mutation-preflight rejection probes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ...mcp_stdio_client import ClientFailure, MCPStdioClient
from ...runtime import InvariantFailure
from ...test_utils import write_json


_ALLOWED_PREFLIGHT_BRIDGE_OPERATIONS = frozenset({"get_hierarchy"})


def _bridge_audit_cursor(client: MCPStdioClient) -> tuple[Path | None, int]:
    run_dir = getattr(client, "run_dir", None)
    if run_dir is None:
        return None, 0
    audit_path = Path(run_dir) / "bridge-calls.jsonl"
    if not audit_path.exists():
        return audit_path, 0
    return audit_path, len(
        [line for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    )


def _read_only_bridge_evidence(
    audit_path: Path | None,
    start: int,
    *,
    label: str,
) -> dict[str, Any]:
    if audit_path is None:
        return {"verified": False, "reason": "injected_test_client"}
    if not audit_path.exists():
        raise InvariantFailure(f"{label} rejection bridge audit file was not created.")
    records = [
        json.loads(line)
        for line in audit_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    operations = [str(record.get("operation", "")) for record in records[start:]]
    unexpected = sorted(
        operation
        for operation in operations
        if operation not in _ALLOWED_PREFLIGHT_BRIDGE_OPERATIONS
    )
    if unexpected:
        raise InvariantFailure(
            f"{label} expected rejection invoked non-read bridge operations: "
            + ", ".join(unexpected)
        )
    return {
        "verified": True,
        "bridge_operations": operations,
        "mutation_bridge_calls": 0,
        "allowed_operations": sorted(_ALLOWED_PREFLIGHT_BRIDGE_OPERATIONS),
    }


async def expect_mutation_preflight_rejection(
    client: MCPStdioClient,
    tool_name: str,
    arguments: dict[str, Any],
    evidence_path: Path,
    *,
    label: str,
    expected_message_fragment: str,
) -> dict[str, Any]:
    """Require a typed preflight rejection and prove no mutation bridge call occurred."""

    audit_path, audit_cursor = _bridge_audit_cursor(client)
    try:
        await client.call_tool(tool_name, arguments)
    except ClientFailure as exc:
        envelope = exc.envelope
    else:
        raise InvariantFailure(f"{label} unexpectedly succeeded instead of rejecting.")
    if not isinstance(envelope, dict):
        raise InvariantFailure(f"{label} rejection omitted its public error envelope.")
    error = envelope.get("error")
    details = error.get("details") if isinstance(error, dict) else None
    if (
        not isinstance(error, dict)
        or error.get("code") != "validation_error"
        or not isinstance(details, dict)
        or details.get("mutation_stage") != "preflight"
        or details.get("mutation_attempted") is not False
    ):
        raise InvariantFailure(
            f"{label} did not return the required typed mutation-preflight rejection."
        )
    message = str(error.get("message", ""))
    if expected_message_fragment.casefold() not in message.casefold():
        raise InvariantFailure(f"{label} rejected for an unexpected reason.")
    evidence = {
        "label": label,
        "tool": tool_name,
        "expected_error_code": "validation_error",
        "observed_error_code": error["code"],
        "mutation_stage": details["mutation_stage"],
        "mutation_attempted": details["mutation_attempted"],
        "expected_reason_verified": True,
        "budget": {
            key: details.get(key)
            for key in ("budget_dimension", "observed_count", "configured_limit")
            if key in details
        },
        "execution": envelope.get("execution", {}),
        "bridge_audit": _read_only_bridge_evidence(
            audit_path,
            audit_cursor,
            label=label,
        ),
    }
    write_json(evidence_path, evidence)
    return evidence


__all__ = ["expect_mutation_preflight_rejection"]
