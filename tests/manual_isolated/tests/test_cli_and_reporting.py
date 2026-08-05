"""CLI dry-run, failure handoff, and report tests."""

from __future__ import annotations

from types import SimpleNamespace

from tests.manual_isolated import runner
from tests.manual_isolated.runner import EXIT_MCP, build_parser, main
from tests.manual_isolated.scenarios.validation import record_failure

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

    record_failure(args, "copy only", EXIT_MCP)

    failure = runner.read_json(out / "failure.json")
    assert failure["status"] == "needs_manual_cleanup"
    assert failure["last_successful_step"] == "execute_mutation"
    assert failure["created_ids"] == ["new-page"]
    assert failure["id_map"] == {"old-page": "new-page"}

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
