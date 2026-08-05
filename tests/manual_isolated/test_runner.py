"""Pure tests for the manual runner; these never start MCP or OneNote."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from local_onenote_mcp.page import image_dimensions
from tests.manual_isolated.mcp_stdio_client import (
    ClientFailure,
    COPY_NO_DELETE_POLICY,
    COPY_POLICY,
    COPY_BUDGET_ENV,
    DELETE_POLICY,
    MOVE_POLICY,
    POLICY_ENV_NAMES,
    READ_ONLY_POLICY,
    RECONSTRUCTIVE_MOVE_PAGE_POLICY,
    WRITE_POLICY,
    MCPStdioClient,
    build_server_env,
    is_mutation_tool,
    parse_tool_result,
    summarize,
)
from tests.manual_isolated import runner
from tests.manual_isolated.runner import (
    InvariantFailure,
    RuntimeOptions,
    assert_valid_page_tree,
    build_parser,
    comparable_snapshot,
    main,
    is_descendant_of,
    run_rename,
)


def test_static_policy_matrix_is_minimal() -> None:
    assert READ_ONLY_POLICY.as_dict() == {
        "writes_enabled": False,
        "deletes_enabled": False,
        "permanent_deletes_enabled": False,
        "experimental_move_section_enabled": False,
        "experimental_copy_enabled": False,
        "reconstructive_move_page_enabled": False,
        "raw_xml_enabled": False,
    }
    assert WRITE_POLICY.writes_enabled is True
    assert WRITE_POLICY.deletes_enabled is False
    assert MOVE_POLICY.writes_enabled is True
    assert MOVE_POLICY.experimental_move_section_enabled is True
    assert DELETE_POLICY.deletes_enabled is True
    assert DELETE_POLICY.writes_enabled is False
    assert COPY_POLICY.experimental_copy_enabled is True
    assert COPY_POLICY.deletes_enabled is True
    assert COPY_NO_DELETE_POLICY.deletes_enabled is False
    assert RECONSTRUCTIVE_MOVE_PAGE_POLICY.reconstructive_move_page_enabled is True
    for policy in (
        READ_ONLY_POLICY,
        WRITE_POLICY,
        MOVE_POLICY,
        DELETE_POLICY,
        COPY_POLICY,
        COPY_NO_DELETE_POLICY,
        RECONSTRUCTIVE_MOVE_PAGE_POLICY,
    ):
        assert policy.permanent_deletes_enabled is False
        assert policy.raw_xml_enabled is False


def test_child_env_overrides_hostile_parent_values(monkeypatch, tmp_path) -> None:
    for env_name in POLICY_ENV_NAMES.values():
        monkeypatch.setenv(env_name, "true")
    for env_name, _value in COPY_BUDGET_ENV.values():
        monkeypatch.setenv(env_name, "999999999")
    env = build_server_env(DELETE_POLICY, tmp_path / "temp", 1_800)
    assert env["LOCAL_ONENOTE_ENABLE_WRITES"] == "false"
    assert env["LOCAL_ONENOTE_ENABLE_DELETES"] == "true"
    assert env["LOCAL_ONENOTE_ENABLE_PERMANENT_DELETES"] == "false"
    assert env["LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_MOVE_SECTION"] == "false"
    assert env["LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_COPY"] == "false"
    assert env["LOCAL_ONENOTE_ENABLE_RECONSTRUCTIVE_MOVE_PAGE"] == "false"
    assert env["LOCAL_ONENOTE_ENABLE_RAW_XML"] == "false"
    assert env["TEMP"] == env["TMP"]
    assert env["LOCAL_ONENOTE_MCP_TIMEOUT"] == "1800"
    for env_name, value in COPY_BUDGET_ENV.values():
        assert env[env_name] == str(value)


def test_non_read_only_tool_classification_never_retries_publish_or_copy() -> None:
    assert is_mutation_tool("publish_object") is True
    assert is_mutation_tool("copy_page") is True
    assert is_mutation_tool("reconstructive_move_page") is True
    assert is_mutation_tool("plan_copy") is False
    assert is_mutation_tool("get_page_xml") is False


def test_audit_summary_redacts_page_payloads() -> None:
    result = summarize({"xml": "<xml>secret</xml>", "content": "private", "id": "safe-id"})
    assert result["xml"]["redacted"] is True
    assert result["content"]["redacted"] is True
    assert result["id"] == "safe-id"
    assert "secret" not in str(result)
    assert "private" not in str(result)


def test_tool_result_prefers_structured_envelope() -> None:
    result = SimpleNamespace(
        isError=False,
        structuredContent={"result": {"ok": True, "complete": True, "item": {"id": "x"}}},
        content=[],
    )
    assert parse_tool_result(result)["item"]["id"] == "x"


def test_client_failure_preserves_structured_partial_envelope(tmp_path) -> None:
    partial = {
        "ok": False,
        "complete": False,
        "code": "partial_failure",
        "outcome": "copy_only",
        "created_ids": ["new-page"],
        "copy_report": {"id_map": {"old-page": "new-page"}},
        "error": "source deletion was blocked",
    }

    class FakeSession:
        async def call_tool(self, *_args, **_kwargs):
            return SimpleNamespace(
                isError=False,
                structuredContent={"result": partial},
                content=[],
            )

    client = MCPStdioClient(
        policy=READ_ONLY_POLICY,
        allowed_tools={"reconstructive_move_page"},
        run_dir=tmp_path,
        timeout_seconds=10,
    )
    client._session = FakeSession()

    with pytest.raises(Exception) as caught:
        asyncio.run(client.call_tool("reconstructive_move_page", {}, retry_read=False))

    assert isinstance(caught.value, ClientFailure)
    assert caught.value.envelope == partial


def test_result_evidence_is_written_for_structured_partial_failure(tmp_path) -> None:
    partial = {
        "ok": False,
        "complete": False,
        "code": "partial_failure",
        "outcome": "copy_only",
        "created_ids": ["new-page"],
    }

    class FakeClient:
        async def call_tool(self, *_args, **_kwargs):
            raise ClientFailure("partial", envelope=partial)

    evidence = tmp_path / "copy-result.json"
    with pytest.raises(ClientFailure):
        asyncio.run(
            runner._call_with_result_evidence(
                FakeClient(),
                "copy_page",
                {},
                evidence,
            )
        )

    assert runner.read_json(evidence) == partial


def test_failure_handoff_surfaces_partial_copy_targets(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    runner.write_json(
        run_dir / "manifest.json",
        {
            "schema_version": 1,
            "notebook": {"id": "notebook-id", "name": "Notebook"},
            "structure": {"parent_page": {"id": "old-page", "resource_type": "page"}},
        },
    )
    out = runner.scenario_dir(run_dir, "copy-page")
    runner.write_json(
        out / "copy-result.json",
        {
            "ok": False,
            "complete": False,
            "code": "partial_failure",
            "outcome": "copy_only",
            "created_ids": ["new-page"],
            "copy_report": {"id_map": {"old-page": "new-page"}},
        },
    )
    args = SimpleNamespace(
        command="validate",
        run_dir=run_dir,
        scenario="copy-page",
    )

    runner.record_validate_failure(args, "copy only", runner.EXIT_MCP)

    failure = runner.read_json(out / "failure.json")
    assert failure["status"] == "needs_manual_cleanup"
    assert failure["last_successful_step"] == "execute_mutation"
    assert failure["created_ids"] == ["new-page"]
    assert failure["id_map"] == {"old-page": "new-page"}


def test_call_audit_has_start_and_completion_timestamps(tmp_path) -> None:
    class FakeSession:
        async def call_tool(self, *_args, **_kwargs):
            return SimpleNamespace(
                isError=False,
                structuredContent={"result": {"ok": True, "complete": True}},
                content=[],
            )

    client = MCPStdioClient(
        policy=READ_ONLY_POLICY,
        allowed_tools={"health_check"},
        run_dir=tmp_path,
        timeout_seconds=10,
    )
    client._session = FakeSession()
    asyncio.run(client.call_tool("health_check", {}, retry_read=False))
    audit = (tmp_path / "calls.jsonl").read_text(encoding="utf-8")
    assert '"started_at"' in audit
    assert '"completed_at"' in audit


def test_protocol_level_tool_error_is_audited_once(tmp_path) -> None:
    class FakeSession:
        async def call_tool(self, *_args, **_kwargs):
            return SimpleNamespace(isError=True, structuredContent=None, content=[])

    client = MCPStdioClient(
        policy=READ_ONLY_POLICY,
        allowed_tools={"health_check"},
        run_dir=tmp_path,
        timeout_seconds=10,
    )
    client._session = FakeSession()

    with pytest.raises(ClientFailure):
        asyncio.run(client.call_tool("health_check", {}, retry_read=False))

    records = (tmp_path / "calls.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(records) == 1
    assert '"client_error"' in records[0]


def test_snapshot_comparison_ignores_capture_time_and_item_order() -> None:
    first = {
        "captured_at": "before",
        "notebook_id": "n",
        "items": [{"id": "b"}, {"id": "a"}],
        "page_hashes": {"p": "hash"},
        "page_objects": {"p": []},
    }
    second = {**first, "captured_at": "after", "items": list(reversed(first["items"]))}
    assert comparable_snapshot(first) == comparable_snapshot(second)


def test_page_tree_and_delete_sandbox_ancestry_checks() -> None:
    snapshot = {
        "items": [
            {"id": "sandbox", "resource_type": "section_group", "parent_id": "notebook"},
            {"id": "section", "resource_type": "section", "parent_id": "sandbox"},
            {
                "id": "parent",
                "resource_type": "page",
                "section_id": "section",
                "parent_id": "section",
                "parent_page_id": None,
                "page_level": 1,
                "order": 0,
            },
            {
                "id": "child",
                "resource_type": "page",
                "section_id": "section",
                "parent_id": "parent",
                "parent_page_id": "parent",
                "page_level": 2,
                "order": 1,
            },
        ]
    }
    assert_valid_page_tree(snapshot, "section")
    assert runner.page_topology(snapshot, "section") == [
        ("parent", "section", 0, 1, None),
        ("child", "section", 1, 2, "parent"),
    ]
    assert is_descendant_of(snapshot, "section", "sandbox") is True
    assert is_descendant_of(snapshot, "section", "unrelated") is False


def test_parser_has_no_permission_expansion_flags() -> None:
    parser = build_parser()
    help_text = parser.format_help()
    assert "--enable-writes" not in help_text
    assert "--enable-deletes" not in help_text
    assert "--yes" not in help_text


def test_p2_scenarios_default_to_copy_execute_timeout() -> None:
    parser = build_parser()
    copy_args = parser.parse_args(
        ["validate", "copy-notebook", "--run-dir", "run"]
    )
    move_args = parser.parse_args(
        ["validate", "reconstructive-move-page", "--run-dir", "run"]
    )
    rename_args = parser.parse_args(
        ["validate", "rename", "--run-dir", "run"]
    )

    assert copy_args.timeout == 1_800
    assert move_args.timeout == 1_800
    assert rename_args.timeout == 180


def test_dry_run_does_not_start_mcp(tmp_path, capsys) -> None:
    exit_code = main(
        [
            "create",
            "--notebook-name",
            "__DRY_RUN__",
            "--run-dir",
            str(tmp_path / "run"),
            "--dry-run",
            "--json",
        ]
    )
    assert exit_code == 0
    output = capsys.readouterr().out
    assert '"server_started": false' in output
    assert not (tmp_path / "run").exists()


def test_validate_dry_run_resolves_manifest_target_without_mcp(tmp_path, capsys) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(
        """{
  "schema_version": 1,
  "notebook": {"id": "notebook-id", "name": "Notebook"},
  "structure": {
    "move_source": {"id": "section-id", "resource_type": "section"}
  }
}
""",
        encoding="utf-8",
    )
    exit_code = main(["validate", "rename", "--run-dir", str(run_dir), "--dry-run", "--json"])
    assert exit_code == 0
    output = capsys.readouterr().out
    assert '"server_started": false' in output
    assert '"target_id": "section-id"' in output
    assert not (run_dir / "scenarios").exists()


def test_copy_validate_dry_runs_use_named_scenarios_and_static_policies(tmp_path, capsys) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(
        """{
  "schema_version": 1,
  "notebook": {"id": "notebook-id", "name": "Notebook"},
  "structure": {
    "parent_page": {"id": "parent-id", "resource_type": "page"},
    "move_source": {"id": "section-id", "resource_type": "section"},
    "group_a": {"id": "group-id", "resource_type": "section_group"},
    "disposable_page": {"id": "disposable-id", "resource_type": "page"}
  }
}
""",
        encoding="utf-8",
    )

    for scenario, target_id in (
        ("copy-page", "parent-id"),
        ("copy-section", "section-id"),
        ("copy-section-group", "group-id"),
        ("copy-notebook", "notebook-id"),
        ("reconstructive-move-page", "disposable-id"),
    ):
        exit_code = main(
            ["validate", scenario, "--run-dir", str(run_dir), "--dry-run", "--json"]
        )
        assert exit_code == 0
        output = capsys.readouterr().out
        assert '"server_started": false' in output
        assert f'"target_id": "{target_id}"' in output
        if scenario.startswith("copy-") or scenario == "reconstructive-move-page":
            assert '"timeout_seconds": 1800' in output
            assert '"max_pages": 200' in output

    assert not (run_dir / "scenarios").exists()


def test_notebook_copy_requires_exact_manifest_allowlisted_root(tmp_path) -> None:
    manifest = {
        "schema_version": 1,
        "notebook": {"id": "notebook-id", "name": "Notebook"},
        "structure": {},
        "disposable_targets": {
            "notebook_copy_root": str((tmp_path / "different-root").resolve()),
        },
    }

    with pytest.raises(runner.RunnerFailure, match="exact disposable Notebook Copy root"):
        runner._copy_spec("copy-notebook", manifest, tmp_path / "run")


def test_copy_cleanup_uses_exact_ids_leaf_to_root_with_fresh_reads() -> None:
    notebook = {"resource_type": "notebook", "id": "n", "name": "Notebook"}
    targets = [
        {
            "resource_type": "section_group",
            "id": "g",
            "name": "Root",
            "parent_id": "n",
            "notebook_id": "n",
        },
        {
            "resource_type": "section_group",
            "id": "ig",
            "name": "Inner",
            "parent_id": "g",
            "notebook_id": "n",
        },
        {
            "resource_type": "section",
            "id": "s1",
            "name": "First",
            "parent_id": "g",
            "notebook_id": "n",
        },
        {
            "resource_type": "section",
            "id": "s2",
            "name": "Second",
            "parent_id": "ig",
            "notebook_id": "n",
        },
        {
            "resource_type": "page",
            "id": "p1",
            "title": "Parent",
            "parent_id": "s2",
            "section_id": "s2",
            "notebook_id": "n",
            "order": 0,
        },
        {
            "resource_type": "page",
            "id": "p2",
            "title": "Child",
            "parent_id": "s2",
            "section_id": "s2",
            "notebook_id": "n",
            "order": 1,
        },
    ]

    class FakeClient:
        def __init__(self):
            self.active = {item["id"]: dict(item) for item in targets}
            self.deleted = []
            self.reads = 0

        async def call_tool(self, name, arguments):
            if name == "get_tree":
                self.reads += 1
                return {
                    "tree": {
                        "item": notebook,
                        "children": [
                            {"item": item, "children": []} for item in self.active.values()
                        ],
                    }
                }
            target_id = next(
                arguments[key]
                for key in ("page_id", "section_id", "section_group_id")
                if key in arguments
            )
            assert arguments["permanently"] is False
            self.deleted.append(target_id)
            self.active.pop(target_id)
            return {"permanently": False}

    client = FakeClient()
    snapshot = {"items": targets}
    copied = {
        "item": targets[0],
        "copy_report": {"id_map": {f"source-{item['id']}": item["id"] for item in targets}},
    }

    deleted = asyncio.run(runner._cleanup_copy(client, snapshot, copied))

    assert deleted == ["p2", "p1", "s2", "s1", "ig", "g"]
    assert client.deleted == deleted
    assert client.reads == len(deleted)


def test_copy_rich_fixture_is_idempotent_and_records_automated_types(tmp_path) -> None:
    state = {
        "xml": (
            '<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" '
            'ID="page-id"><one:Title><one:OE><one:T>Parent</one:T></one:OE></one:Title></one:Page>'
        ),
        "objects": [],
    }
    page = {
        "resource_type": "page",
        "id": "page-id",
        "title": "Parent",
        "section_id": "section-id",
        "modified": "m1",
    }

    class FakeClient:
        def __init__(self):
            self.mutations = []

        async def call_tool(self, name, arguments):
            if name == "get_page_xml":
                return {"xml": state["xml"]}
            if name == "get_page_objects":
                return {"objects": state["objects"]}
            if name == "list_pages":
                return {"pages": [page]}
            if name == "append_to_page":
                self.mutations.append(name)
                state["xml"] = (
                    '<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" '
                    'ID="page-id"><one:Title><one:OE><one:T>Parent</one:T></one:OE></one:Title>'
                    '<one:Outline><one:OEChildren><one:OE><one:T>'
                    f"{runner.COPY_FIXTURE_MARKER}"
                    '</one:T></one:OE><one:Table/></one:OEChildren></one:Outline></one:Page>'
                )
                return {"appended": True}
            if name == "add_image_to_page":
                self.mutations.append(name)
                state["objects"] = [{"type": "Image", "object_id": "image-id"}]
                return {"image_path": arguments["image_path"]}
            raise AssertionError(name)

    client = FakeClient()
    _, first = asyncio.run(runner.ensure_copy_rich_fixture(client, page, tmp_path))
    _, second = asyncio.run(runner.ensure_copy_rich_fixture(client, page, tmp_path))

    assert client.mutations == ["append_to_page", "add_image_to_page"]
    assert first == second
    assert first["automated_content"] == ["rich_text", "table", "image"]
    assert first["manual_content"] == ["file_attachment", "ink", "media"]
    assert image_dimensions(tmp_path / "fixture-assets" / "copy-fixture-1x1.png") == (1, 1)


def test_runner_independently_validates_page_copy_mapping_and_topology() -> None:
    before = {
        "items": [
            {"resource_type": "section", "id": "source-section", "name": "Source", "parent_id": "n"},
            {
                "resource_type": "page",
                "id": "parent",
                "title": "Parent",
                "section_id": "source-section",
                "parent_id": "source-section",
                "parent_page_id": None,
                "page_level": 2,
                "order": 0,
            },
            {
                "resource_type": "page",
                "id": "child",
                "title": "Child",
                "section_id": "source-section",
                "parent_id": "source-section",
                "parent_page_id": "parent",
                "page_level": 3,
                "order": 1,
            },
            {
                "resource_type": "page",
                "id": "sibling",
                "title": "Sibling",
                "section_id": "source-section",
                "parent_id": "source-section",
                "parent_page_id": None,
                "page_level": 2,
                "order": 2,
            },
            {"resource_type": "section", "id": "destination", "name": "Destination", "parent_id": "n"},
        ]
    }
    after = {
        "items": [
            *before["items"],
            {
                "resource_type": "page",
                "id": "new-parent",
                "title": "Copied Parent",
                "section_id": "destination",
                "parent_id": "destination",
                "parent_page_id": None,
                "page_level": 1,
                "order": 4,
            },
            {
                "resource_type": "page",
                "id": "new-child",
                "title": "Child",
                "section_id": "destination",
                "parent_id": "destination",
                "parent_page_id": "new-parent",
                "page_level": 2,
                "order": 5,
            },
        ]
    }
    copied = {
        "copy_report": {"id_map": {"parent": "new-parent", "child": "new-child"}}
    }

    runner.assert_copy_mapping(
        before,
        after,
        "parent",
        "destination",
        "Copied Parent",
        copied,
    )

    broken = {"items": [dict(item) for item in after["items"]]}
    next(item for item in broken["items"] if item["id"] == "new-child")["parent_page_id"] = None
    with pytest.raises(InvariantFailure, match="parent relation"):
        runner.assert_copy_mapping(
            before,
            broken,
            "parent",
            "destination",
            "Copied Parent",
            copied,
        )


def test_copy_fixture_capability_gate_runs_before_mutation() -> None:
    runner.assert_copy_fixture_capabilities(
        {"content_capabilities": ["Image", "Outline", "RichText", "Table"]}
    )
    with pytest.raises(InvariantFailure, match="missing automated fixture capabilities"):
        runner.assert_copy_fixture_capabilities(
            {"content_capabilities": ["Image", "Outline", "Table"]}
        )


def test_report_records_manual_environment_without_mcp(tmp_path, capsys) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(
        """{
  "schema_version": 1,
  "run_id": "run",
  "notebook": {"id": "notebook-id", "name": "Notebook"},
  "structure": {},
  "copy_fixture": {
    "page_id": "page-id",
    "automated_content": ["rich_text", "table", "image"],
    "manual_content": ["file_attachment", "ink", "media"],
    "observed_object_types": ["Image", "Outline"]
  }
}
""",
        encoding="utf-8",
    )
    scenario = run_dir / "scenarios" / "copy-page"
    scenario.mkdir(parents=True)
    runner.write_json(
        scenario / "plan.json",
        {
            "content_capabilities": ["Image", "Outline", "RichText", "Table"],
            "copyability": {"lossless_candidate": False},
        },
    )
    runner.write_json(
        scenario / "copy-result.json",
        {"copy_report": {"verified": True, "lossless": False}},
    )
    runner.write_json(
        scenario / "result.json",
        {"scenario": "copy-page", "status": "passed", "target_id": "new-page", "restored": True},
    )
    exit_code = main(
        [
            "report",
            "--run-dir",
            str(run_dir),
            "--onenote-version",
            "16.0-test",
            "--office-channel",
            "Current",
            "--json",
        ]
    )
    assert exit_code == 0
    capsys.readouterr()
    manifest = (run_dir / "manifest.json").read_text(encoding="utf-8")
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert '"onenote_version": "16.0-test"' in manifest
    assert "OneNote version: `16.0-test`" in report
    assert "Automated content: `rich_text, table, image`" in report
    assert "Planned content capabilities: `Image, Outline, RichText, Table`" in report
    assert "Copy verified: `True`" in report


def test_rename_attempts_restore_before_reporting_invariant_failure(monkeypatch, tmp_path) -> None:
    target = {
        "resource_type": "section",
        "id": "section-id",
        "name": "Move-Source",
        "path": "Notebook/Group-A/Move-Source",
        "parent_id": "group-a",
    }
    notebook = {"resource_type": "notebook", "id": "notebook-id", "name": "Notebook"}
    manifest = {"schema_version": 1, "notebook": notebook, "structure": {"move_source": target}}
    before = {
        "captured_at": "before",
        "notebook_id": "notebook-id",
        "items": [target],
        "page_hashes": {"page": "before-hash"},
        "page_objects": {"page": []},
    }
    changed = {**target, "name": "Move-Source-Smoke-Renamed", "path": "renamed"}
    after = {**before, "captured_at": "after", "items": [changed], "page_hashes": {"page": "changed-hash"}}
    restored = {**before, "captured_at": "restored"}

    class FakeClient:
        calls: list[tuple[str, dict]] = []

        def __init__(self, **_: object) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def call_tool(self, name: str, arguments: dict, **_: object) -> dict:
            self.calls.append((name, arguments))
            item = changed if arguments["new_name"].endswith("Renamed") else target
            return {"ok": True, "complete": True, "item": item}

    snapshots = iter([before, after, restored])

    async def fake_snapshot(_client, _notebook_id):
        return next(snapshots)

    monkeypatch.setattr(runner, "MCPStdioClient", FakeClient)
    monkeypatch.setattr(runner, "capture_snapshot", fake_snapshot)
    monkeypatch.setattr(runner, "render_report", lambda _run_dir: None)
    args = SimpleNamespace(target="move_source", new_name=None, notebook_name=None)
    options = RuntimeOptions(tmp_path, 10, False, False)
    with pytest.raises(InvariantFailure):
        asyncio.run(run_rename(args, options, manifest))
    assert [call[1]["new_name"] for call in FakeClient.calls] == [
        "Move-Source-Smoke-Renamed",
        "Move-Source",
    ]
