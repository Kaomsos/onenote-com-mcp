"""CLI dry-run, failure handoff, and report tests."""

from __future__ import annotations

import argparse
import asyncio
import json
from types import SimpleNamespace

from tests.manual_validation import test_utils
from tests.manual_validation.runner import build_parser, main
from tests.manual_validation.runtime import EXIT_MCP
from tests.manual_validation.scenarios.common.report import run_report
from tests.manual_validation.scenarios.common.orchestrator import record_failure

def test_failure_handoff_surfaces_partial_copy_targets(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    test_utils.write_json(
        run_dir / "manifest.json",
        {
            "schema_version": 1,
            "notebook": {"id": "notebook-id", "name": "Notebook"},
            "structure": {"parent_page": {"id": "old-page", "resource_type": "page"}},
        },
    )
    out = test_utils.scenario_dir(run_dir, "copy-page")
    test_utils.write_json(
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
        command="copy-page",
        run_dir=run_dir,
        scenario="copy-page",
    )

    record_failure(args, "copy only", EXIT_MCP)

    failure = test_utils.read_json(out / "failure.json")
    assert failure["status"] == "needs_manual_cleanup"
    assert failure["last_successful_step"] == "execute_mutation"
    assert failure["created_ids"] == ["new-page"]
    assert failure["id_map"] == {"old-page": "new-page"}

def test_parser_has_no_permission_expansion_flags() -> None:
    parser = build_parser()
    help_text = parser.format_help()
    assert "HUMAN-GATED" in help_text
    assert "isolated" in help_text
    assert "least-privilege" in help_text
    assert "--enable-writes" not in help_text
    assert "--enable-deletes" not in help_text
    assert "--yes" not in help_text

def test_p2_scenarios_default_to_copy_execute_timeout() -> None:
    parser = build_parser()
    copy_args = parser.parse_args(
        ["copy-notebook", "--run-dir", "run"]
    )
    move_args = parser.parse_args(
        ["reconstructive-move-page", "--run-dir", "run"]
    )
    rename_args = parser.parse_args(
        ["rename", "--run-dir", "run"]
    )

    assert copy_args.timeout == 1_800
    assert move_args.timeout == 1_800
    assert rename_args.timeout == 180

def test_dry_run_does_not_start_mcp(tmp_path, capsys) -> None:
    exit_code = main(
        [
            "rename",
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

def test_scenario_dry_run_needs_no_prepared_manifest(tmp_path, capsys) -> None:
    run_dir = tmp_path / "run"
    exit_code = main(["rename", "--run-dir", str(run_dir), "--dry-run", "--json"])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["server_started"] is False
    assert payload["fixture_profile"]["name"] == "rename-target"
    assert payload["expected_mcp_process_starts"] == 1
    assert not run_dir.exists()

def test_copy_dry_runs_use_named_scenarios_and_static_policies(tmp_path, capsys) -> None:
    for scenario, profile in (
        ("copy-page", "rich-page-copy"),
        ("copy-section", "rich-section-copy"),
        ("copy-section-group", "rich-group-copy"),
        ("copy-notebook", "rich-notebook-copy"),
        ("reconstructive-move-page", "disposable-page-move"),
    ):
        run_dir = tmp_path / scenario
        exit_code = main(
            [scenario, "--run-dir", str(run_dir), "--dry-run", "--json"]
        )
        assert exit_code == 0
        output = capsys.readouterr().out
        payload = json.loads(output)
        assert payload["server_started"] is False
        assert payload["fixture_profile"]["name"] == profile
        assert payload["expected_mcp_process_starts"] == 1
        if scenario.startswith("copy-") or scenario == "reconstructive-move-page":
            assert '"timeout_seconds": 1800' in output
            assert '"max_pages": 200' in output

        assert not run_dir.exists()

def test_internal_report_records_manual_environment_without_mcp(tmp_path) -> None:
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
    test_utils.write_json(
        scenario / "plan.json",
        {
            "content_capabilities": ["Image", "Outline", "RichText", "Table"],
            "copyability": {"lossless_candidate": False},
        },
    )
    test_utils.write_json(
        scenario / "copy-result.json",
        {"copy_report": {"verified": True, "lossless": False}},
    )
    test_utils.write_json(
        scenario / "result.json",
        {"scenario": "copy-page", "status": "passed", "target_id": "new-page", "restored": True},
    )
    result = asyncio.run(
        run_report(
            argparse.Namespace(
                run_dir=run_dir,
                onenote_version="16.0-test",
                office_channel="Current",
            )
        )
    )
    assert result["command"] == "report"
    manifest = (run_dir / "manifest.json").read_text(encoding="utf-8")
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert '"onenote_version": "16.0-test"' in manifest
    assert "OneNote version: `16.0-test`" in report
    assert "Automated content: `rich_text, table, image`" in report
    assert "Planned content capabilities: `Image, Outline, RichText, Table`" in report
    assert "Copy verified: `True`" in report
