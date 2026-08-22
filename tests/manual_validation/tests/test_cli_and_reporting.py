"""CLI dry-run, failure handoff, and report tests."""

from __future__ import annotations

import argparse
import asyncio
import json
from types import SimpleNamespace

import pytest

from tests.manual_validation import test_utils
from tests.manual_validation import runner as runner_module
from tests.manual_validation.runner import build_parser, main
from tests.manual_validation.runtime import EXIT_MCP, EXIT_RESTORE
from tests.manual_validation.scenarios.common.report import run_report
from tests.manual_validation.scenarios.common.orchestrator import record_failure
from tests.manual_validation.scenarios.common.registry import SCENARIO_REGISTRY

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


def test_use_cache_is_the_single_default_off_cache_flag_for_named_and_all() -> None:
    parser = build_parser()
    for scenario in SCENARIO_REGISTRY.public_names:
        assert parser.parse_args([scenario]).use_cache is False
        assert parser.parse_args([scenario, "--use-cache"]).use_cache is True
    assert parser.parse_args(["all"]).use_cache is False
    assert parser.parse_args(["all", "--use-cache"]).use_cache is True
    with pytest.raises(SystemExit):
        parser.parse_args(["copy-page", "--reuse-fixture-cache"])


def test_named_verbosity_defaults_to_normal_while_all_defaults_to_quiet() -> None:
    parser = build_parser()

    assert parser.parse_args(["rename"]).verbosity == "normal"
    assert parser.parse_args(["rename", "--verbosity", "quiet"]).verbosity == "quiet"
    assert parser.parse_args(["rename", "--verbosity", "verbose"]).verbosity == "verbose"
    assert parser.parse_args(["all"]).verbosity == "quiet"
    with pytest.raises(SystemExit):
        parser.parse_args(["rename", "--verbosity", "trace"])


def test_page_reorder_uses_explicit_reorder_page_entry_only() -> None:
    parser = build_parser()

    args = parser.parse_args(["reorder-page", "--dry-run"])
    assert args.command == "reorder-page"
    assert args.page_level == 2
    with pytest.raises(SystemExit):
        parser.parse_args(["reorder", "--dry-run"])


def test_unsupported_section_group_reorder_has_no_public_scenario() -> None:
    parser = build_parser()

    assert "reorder-section-group" not in SCENARIO_REGISTRY.public_names
    with pytest.raises(SystemExit):
        parser.parse_args(["reorder-section-group", "--dry-run"])


def test_p2_scenarios_default_to_copy_execute_timeout() -> None:
    parser = build_parser()
    copy_args = parser.parse_args(
        ["copy-notebook", "--run-dir", "run"]
    )
    move_args = parser.parse_args(
        ["move-page", "--run-dir", "run"]
    )
    rename_args = parser.parse_args(
        ["rename", "--run-dir", "run"]
    )

    assert copy_args.timeout == 1_800
    assert move_args.timeout == 1_800
    assert rename_args.timeout == 180


def test_rename_has_no_fixture_target_selector() -> None:
    parser = build_parser()
    args = parser.parse_args(["rename", "--dry-run"])
    assert not hasattr(args, "target")

    with pytest.raises(SystemExit):
        parser.parse_args(["rename", "--target", "section_target", "--dry-run"])


def test_keep_worksite_is_available_to_every_named_action_but_not_all() -> None:
    parser = build_parser()
    for scenario in SCENARIO_REGISTRY.public_names:
        args = parser.parse_args([scenario, "--keep-worksite"])
        assert args.keep_worksite is True

    with pytest.raises(SystemExit):
        parser.parse_args(["all", "--keep-worksite"])


def test_keep_worksite_dry_run_preserves_targets_and_source_notebook(tmp_path, capsys) -> None:
    run_dir = tmp_path / "run"
    assert main(
        [
            "copy-page",
            "--run-dir",
            str(run_dir),
            "--keep-worksite",
            "--dry-run",
            "--json",
        ]
    ) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["lifecycle"] == "keep"
    assert payload["worksite"] == {
        "preserved": True,
        "target_cleanup": "preserve-active-copy-targets",
    }
    assert payload["scenario_spec"]["mutation_policy"]["deletes_enabled"] is False
    assert not {
        "delete_page",
        "delete_section",
        "delete_section_group",
    } & set(payload["scenario_spec"]["tool_allowlist"])
    assert [step["step"] for step in payload["ordered_steps"]] == [
        "create-notebook-bundle",
        "copy-page",
        "report",
    ]
    assert not run_dir.exists()


@pytest.mark.parametrize(
    "scenario",
    [
        "copy-page",
        "copy-section",
        "copy-section-group",
        "copy-notebook",
        "move-page",
    ],
)
def test_page_copy_dry_runs_declare_layered_automatic_fixture(
    scenario, tmp_path, capsys
) -> None:
    run_dir = tmp_path / "run"
    assert main([scenario, "--run-dir", str(run_dir), "--dry-run", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert "human_checkpoint" not in payload
    if scenario == "move-page":
        assert set(payload["fixture_profile"]["content_capabilities"]) == {
            "Outline",
            "RichText",
            "Table",
            "special-character Page title",
        }
        assert set(payload["cache"]["roles"]) == {"destination", "source"}
        assert [
            case["include_descendants"]
            for case in payload["scenario_spec"]["execution_contract"]["cases"]
        ] == ["omitted", True]
        return
    assert {"Image", "List", "Outline", "RichText", "Table", "Tag"} <= set(
        payload["fixture_profile"]["content_capabilities"]
    )
    assert any(
        ("02-Source-Child" if scenario == "copy-page" else "List-Tag-Page") in path
        for path in payload["fixture_profile"]["expected_structure"]
    )
    assert "reorder_page" in payload["scenario_spec"]["tool_allowlist"]
    assert not run_dir.exists()

def test_dry_run_does_not_start_mcp(tmp_path, capsys) -> None:
    exit_code = main(
        [
            "rename",
            "--notebook-label",
            "dry-run",
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


def test_container_reorder_dry_run_requires_no_environment_metadata(tmp_path, capsys) -> None:
    scenario = "reorder-section"
    run_dir = tmp_path / scenario
    args = build_parser().parse_args(
        [scenario, "--run-dir", str(run_dir), "--dry-run", "--json"]
    )
    assert not hasattr(args, "onenote_version")
    assert not hasattr(args, "office_channel")
    with pytest.raises(SystemExit):
        build_parser().parse_args([scenario, "--dry-run", "--onenote-version", "16.0"])

    assert main([scenario, "--run-dir", str(run_dir), "--dry-run", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert "validation_environment" not in payload
    assert not run_dir.exists()

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
        ("move-page", "disposable-page-move"),
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
        if scenario.startswith("copy-") or scenario == "move-page":
            assert '"timeout_seconds": 1800' in output
            assert '"max_pages": 200' in output

        assert not run_dir.exists()

def test_internal_report_renders_scenario_evidence_without_collecting_environment(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(
        """{
  "schema_version": 1,
  "run_id": "run",
  "notebook": {"id": "notebook-id", "name": "Notebook"},
  "structure": {},
  "copy_scenario": {
    "validated_content_types": [
      "DisplayEquation", "Image", "InkDrawing", "InsertedFile", "List", "MediaFile",
      "Outline", "RichText", "Table", "Tag", "UIShape"
    ]
  },
  "copy_fixture": {
    "page_id": "page-id",
    "automated_content": ["rich_text", "table", "image", "list", "tag"],
    "manual_content": ["ink", "shape", "media"],
    "observed_object_types": ["Image", "Outline"],
    "semantic_page": {
      "page_id": "semantic-page-id",
      "observed_capabilities": ["List", "Tag"]
    }
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
            "content_capabilities": ["Image", "List", "Outline", "RichText", "Table", "Tag"],
            "copyability": {"lossless_candidate": True},
        },
    )
    test_utils.write_json(
        scenario / "copy-result.json",
        {
            "copy_report": {
                "verified": True,
                "lossless": True,
                "page_results": [
                    {
                        "source_page_id": "page-id",
                        "target_page_id": "strict-target",
                        "equivalence": {
                            "verification_tier": "strict_canonical",
                            "equivalent": True,
                        },
                    },
                    {
                        "source_page_id": "semantic-page-id",
                        "target_page_id": "semantic-target",
                        "equivalence": {
                            "verification_tier": "semantic_list_tag",
                            "equivalent": True,
                        },
                    },
                ],
            }
        },
    )
    test_utils.write_json(
        scenario / "plan-root-only-default.json",
        {
            "content_capabilities": ["Image", "Outline", "RichText", "Table"],
            "copyability": {"lossless_candidate": True},
        },
    )
    test_utils.write_json(
        scenario / "copy-result-root-only-default.json",
        {"copy_report": {"verified": True, "lossless": True}},
    )
    test_utils.write_json(
        scenario / "result.json",
        {
            "scenario": "copy-page",
            "status": "passed",
            "target_id": "new-page",
            "restored": False,
            "worksite_preserved": True,
            "remaining_state": {
                "manual_cleanup_required": True,
                "target_ids": ["new-page"],
            },
        },
    )
    result = asyncio.run(run_report(argparse.Namespace(run_dir=run_dir)))
    assert result["command"] == "report"
    manifest = (run_dir / "manifest.json").read_text(encoding="utf-8")
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert '"validation_environment"' not in manifest
    assert "## Validated environment" not in report
    assert "Automated content: `rich_text, table, image, list, tag`" in report
    assert (
        "Validated content types: `DisplayEquation, Image, InkDrawing, InsertedFile, List, "
        "MediaFile, Outline, RichText, Table, Tag, UIShape`"
    ) in report
    assert "Planned content capabilities: `Image, List, Outline, RichText, Table, Tag`" in report
    assert "Semantic Page ID: `semantic-page-id`" in report
    assert "Semantic capabilities: `List, Tag`" in report
    assert "Semantic acceptance tier: `semantic_list_tag`" in report
    assert "Copy verified: `True`" in report
    assert "Copy case `root-only-default` verified: `True`" in report
    assert (
        "Copy case `root-only-default` planned content capabilities: "
        "`Image, Outline, RichText, Table`"
    ) in report
    assert "tier `strict_canonical`, equivalent `True`" in report
    assert "tier `semantic_list_tag`, equivalent `True`" in report
    assert "Worksite preserved: `True`" in report
    assert "Manual cleanup required: `True`" in report
    assert "Preserved target IDs: `new-page`" in report


def test_internal_report_renders_interactive_copy_and_discovery_evidence(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    test_utils.write_json(
        run_dir / "manifest.json",
        {
            "schema_version": 1,
            "run_id": "run",
            "notebook": {"id": "notebook-id", "name": "Notebook"},
            "structure": {},
        },
    )
    copy_out = test_utils.scenario_dir(run_dir, "interactive-copy-ink-drawing")
    test_utils.write_json(
        copy_out / "result.json",
        {
            "scenario": "interactive-copy-ink-drawing",
            "status": "passed",
            "capability": "InkDrawing",
            "target_id": "target-page",
            "machine_comparator_passed": True,
            "human_verdict": "accepted",
            "production_verified": True,
            "production_lossless": False,
            "diagnostic_partial_admitted": True,
            "verification_tier": "semantic_ink_drawing",
            "source_deleted": False,
        },
    )
    discovery_out = test_utils.scenario_dir(run_dir, "bootstrap-shape-fixture")
    test_utils.write_json(
        discovery_out / "result.json",
        {
            "scenario": "bootstrap-shape-fixture",
            "status": "evidence_only",
            "representation_status": "single_candidate_observed",
            "candidate_added_kinds": ["InkDrawing"],
            "candidate_added_capabilities": ["InkDrawing"],
            "interactive_bootstrap": False,
            "template_published": False,
            "mutation_eligible": False,
            "move_source_deletion_allowed": False,
        },
    )

    asyncio.run(run_report(argparse.Namespace(run_dir=run_dir)))

    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "Capability: `InkDrawing`" in report
    assert "Machine comparator passed: `True`" in report
    assert "Human verdict: `accepted`" in report
    assert "Production verified: `True`" in report
    assert "Production lossless: `False`" in report
    assert "Diagnostic partial admitted: `True`" in report
    assert "Verification tier: `semantic_ink_drawing`" in report
    assert "Source deleted: `False`" in report
    assert "Representation status: `single_candidate_observed`" in report
    assert "Candidate added kinds: `InkDrawing`" in report
    assert "Candidate added capabilities: `InkDrawing`" in report
    assert "Interactive bootstrap: `False`" in report
    assert "Template published: `False`" in report
    assert "Mutation eligible: `False`" in report
    assert "Move source deletion allowed: `False`" in report


def test_main_converts_unexpected_bridge_error_to_structured_failure(
    tmp_path, monkeypatch, capsys
) -> None:
    from local_onenote_mcp.onenote_errors import OneNoteBridgeError

    run_dir = tmp_path / "run-structured"
    run_dir.mkdir()
    test_utils.write_json(
        run_dir / "run-state.json",
        {
            "status": "running",
            "current_step": "close-source-notebook",
            "finalization_started": True,
            "completed_steps": [{"step": "com-refresh-mutation"}],
        },
    )

    async def boom(_args):
        raise OneNoteBridgeError(
            "RPC server unavailable (0x800706BA)",
            operation="get_hierarchy",
            hresult=0x800706BA,
        )

    monkeypatch.setattr(runner_module, "dispatch", boom)
    code = main(
        [
            "com-refresh-mutation",
            "--run-dir",
            str(run_dir),
            "--json",
        ]
    )
    captured = capsys.readouterr()
    assert code == EXIT_RESTORE
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err
    failure = test_utils.read_json(run_dir / "run-failure.json")
    assert failure["exit_code"] == EXIT_RESTORE
    assert failure["failed_step"] == "close-source-notebook"
    assert "0x800706BA" in failure["error"]
    assert "failure_finalization" in failure
