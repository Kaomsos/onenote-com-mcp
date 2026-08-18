"""Contracts for human-gated expected-rejection evidence."""

from __future__ import annotations

import asyncio
import json

import pytest

from tests.manual_validation.mcp_stdio_client import ClientFailure
from tests.manual_validation.runtime import InvariantFailure
from tests.manual_validation.scenarios.common.expected_rejection import (
    expect_mutation_preflight_rejection,
)
from tests.manual_validation.test_utils import read_json


def _rejection_envelope() -> dict:
    return {
        "ok": False,
        "error": {
            "code": "validation_error",
            "message": "child type conflicts with inferred parent type",
            "details": {
                "mutation_stage": "preflight",
                "mutation_attempted": False,
            },
        },
        "execution": {
            "observed_outcome": "not_applied",
            "attempts": 0,
            "backend_calls": 1,
            "replayed": False,
        },
    }


class RejectingClient:
    def __init__(self, run_dir, operation: str = "get_hierarchy") -> None:
        self.run_dir = run_dir
        self.operation = operation

    async def call_tool(self, _name: str, _arguments: dict) -> dict:
        audit_path = self.run_dir / "bridge-calls.jsonl"
        with audit_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps({"operation": self.operation}) + "\n")
        raise ClientFailure("expected rejection", envelope=_rejection_envelope())


def test_expected_rejection_persists_typed_read_only_evidence(tmp_path) -> None:
    run_dir = tmp_path / "scenario-mcp"
    run_dir.mkdir()
    evidence_path = tmp_path / "scenario" / "expected-rejection.json"
    evidence_path.parent.mkdir()

    evidence = asyncio.run(
        expect_mutation_preflight_rejection(
            RejectingClient(run_dir),
            "sort_children",
            {"parent_id": "parent"},
            evidence_path,
            label="sort-child-type-conflict",
            expected_message_fragment="conflicts",
        )
    )

    assert evidence["observed_error_code"] == "validation_error"
    assert evidence["mutation_stage"] == "preflight"
    assert evidence["mutation_attempted"] is False
    assert evidence["bridge_audit"] == {
        "verified": True,
        "bridge_operations": ["get_hierarchy"],
        "mutation_bridge_calls": 0,
        "allowed_operations": ["get_hierarchy"],
    }
    assert read_json(evidence_path) == evidence


def test_expected_rejection_rejects_any_mutation_bridge_call(tmp_path) -> None:
    run_dir = tmp_path / "scenario-mcp"
    run_dir.mkdir()
    evidence_path = tmp_path / "scenario" / "expected-rejection.json"
    evidence_path.parent.mkdir()

    with pytest.raises(InvariantFailure, match="non-read bridge operations"):
        asyncio.run(
            expect_mutation_preflight_rejection(
                RejectingClient(run_dir, "update_hierarchy"),
                "sort_children",
                {"parent_id": "parent"},
                evidence_path,
                label="sort-child-type-conflict",
                expected_message_fragment="conflicts",
            )
        )

    assert not evidence_path.exists()


def test_expected_rejection_requires_typed_preflight_details(tmp_path) -> None:
    class UntypedClient:
        run_dir = None

        async def call_tool(self, _name: str, _arguments: dict) -> dict:
            envelope = _rejection_envelope()
            envelope["error"]["details"]["mutation_attempted"] = True
            raise ClientFailure("wrong rejection", envelope=envelope)

    with pytest.raises(InvariantFailure, match="typed mutation-preflight rejection"):
        asyncio.run(
            expect_mutation_preflight_rejection(
                UntypedClient(),
                "create_section",
                {"parent_id": "parent"},
                tmp_path / "expected-rejection.json",
                label="duplicate-create",
                expected_message_fragment="conflicts",
            )
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("attempts", 1, "zero mutation attempts"),
        ("replayed", True, "zero mutation attempts"),
        ("backend_calls", 2, "backend-call accounting"),
    ],
)
def test_expected_rejection_requires_exact_execution_accounting(
    tmp_path, field, value, message
) -> None:
    class WrongExecutionClient(RejectingClient):
        async def call_tool(self, _name: str, _arguments: dict) -> dict:
            audit_path = self.run_dir / "bridge-calls.jsonl"
            with audit_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps({"operation": "get_hierarchy"}) + "\n")
            envelope = _rejection_envelope()
            envelope["execution"][field] = value
            raise ClientFailure("wrong execution", envelope=envelope)

    run_dir = tmp_path / "scenario-mcp"
    run_dir.mkdir()
    with pytest.raises(InvariantFailure, match=message):
        asyncio.run(
            expect_mutation_preflight_rejection(
                WrongExecutionClient(run_dir),
                "delete_section",
                {"items": []},
                tmp_path / "expected-rejection.json",
                label="batch-budget",
                expected_message_fragment="conflicts",
            )
        )
