"""Content-free progress, compact output, and diagnostic bounds."""

from __future__ import annotations

import builtins
import json

import pytest

from tests.manual_validation.progress import (
    RunProgressReporter,
    bounded_terminal_text,
    print_compact_scenario_result,
)
from tests.manual_validation.runner import main


def test_quiet_filters_detail_but_keeps_major_phases_and_failure(tmp_path) -> None:
    lines: list[str] = []
    reporter = RunProgressReporter("quiet", writer=lines.append, clock=lambda: 10.0)

    reporter.run_started("rename", tmp_path)
    reporter.phase_started("scenario", 3, 5)
    reporter.unit_started("case", "rename", 1, 1)
    reporter.tool_started("rename_section", 1, mutation=True)
    reporter.tool_completed(
        "rename_section",
        1,
        mutation=True,
        elapsed_seconds=0.2,
        envelope={"ok": True},
    )
    reporter.phase_completed("scenario", elapsed_seconds=0.3)
    reporter.failure(
        "boom section_id={12345678-1234-1234-1234-123456789ABC} "
        "query=private-search <one:Page>secret</one:Page>",
        run_dir=tmp_path,
    )

    assert lines[0].startswith("RUN rename artifacts=")
    assert lines[1] == "[3/5] scenario ..."
    assert lines[-1].startswith("FAIL phase=scenario error=boom section_id=[redacted]")
    assert "12345678" not in lines[-1]
    assert "private-search" not in lines[-1]
    assert "one:Page" not in lines[-1]
    assert "secret" not in lines[-1]
    assert not any("case" in line or "mutation" in line or "DONE" in line for line in lines)


def test_normal_reports_scenario_mutations_but_hides_fixture_mutations() -> None:
    lines: list[str] = []
    reporter = RunProgressReporter("normal", writer=lines.append)

    reporter.phase_started("fixture", 2, 5)
    reporter.tool_started("create_page", 1, mutation=True)
    reporter.tool_completed(
        "create_page",
        1,
        mutation=True,
        elapsed_seconds=0.1,
        envelope={"ok": True},
    )
    reporter.phase_started("scenario", 3, 5)
    reporter.tool_started("rename_section", 1, mutation=True)
    reporter.tool_completed(
        "rename_section",
        1,
        mutation=True,
        elapsed_seconds=0.1,
        envelope={"ok": True},
    )

    rendered = "\n".join(lines)
    assert "create_page" not in rendered
    assert "mutation rename_section ..." in rendered
    assert "mutation PASS rename_section" in rendered
    assert "attempt=" not in rendered


def test_verbose_batches_reads_and_reports_only_content_free_mutation_scalars() -> None:
    lines: list[str] = []
    reporter = RunProgressReporter("verbose", writer=lines.append, read_batch_size=3)
    reporter.phase_started("scenario", 3, 5)

    secret = "NEVER-PRINT-CONTENT"
    for _ in range(3):
        reporter.tool_completed(
            "get_page_content",
            1,
            mutation=False,
            elapsed_seconds=0.01,
            envelope={"ok": True, "content": secret},
        )
    reporter.tool_completed(
        "rename_section",
        2,
        mutation=True,
        elapsed_seconds=0.25,
        envelope={
            "ok": True,
            "arguments": {"section_id": secret},
            "convergence": {"attempts": 4, "stable_observations": 2, "xml": secret},
            "reconciliation": {"state": "applied", "response": secret},
        },
    )

    rendered = "\n".join(lines)
    assert "reads +3 (total=3)" in rendered
    assert "attempt=2 elapsed=0.25s observe=4 stable=2 reconciliation=applied" in rendered
    assert secret not in rendered
    assert "section_id" not in rendered


def test_default_writer_flushes_each_progress_line(monkeypatch) -> None:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def fake_print(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(builtins, "print", fake_print)
    reporter = RunProgressReporter("quiet")
    reporter.phase_started("scenario", 1, 1)

    assert calls == [(("[1/1] scenario ...",), {"flush": True})]


def test_compact_non_json_result_never_expands_nested_payload(tmp_path, capsys) -> None:
    secret = "SECRET-BODY-AND-XML"
    result = {
        "status": "passed",
        "scenario": "rename",
        "run_dir": str(tmp_path),
        "scenario_result": {
            "restored": True,
            "worksite_preserved": False,
            "response": {"content": secret, "xml": f"<{secret}/>"},
        },
        "metrics": {
            "observed_mcp_process_starts": 1,
            "observed_mcp_tool_calls": 7,
            "phases_seconds": {"total": 1.25, "scenario": 0.5},
        },
        "lifecycle": {"status": "closed"},
        "cache": {"decision": "fresh"},
    }

    print_compact_scenario_result(result, verbosity="verbose", dry_run=False)

    output = capsys.readouterr().out
    assert len(output.splitlines()) <= 7
    assert "PASS rename (1.25s)" in output
    assert "mcp_processes=1 mcp_tool_calls=7" in output
    assert "run-result.json" in output
    assert secret not in output
    assert "response" not in output


def test_ready_interactive_bootstrap_prints_cache_reuse_hint(tmp_path, capsys) -> None:
    instance_id = "authored-" + "a" * 24
    result = {
        "status": "passed",
        "scenario": "interactive-move-page-content",
        "run_dir": str(tmp_path),
        "notebook_label": "move-page-content",
        "scenario_result": {
            "interactive_bootstrap": True,
            "template_published": True,
            "template_state": "ready",
            "template_instance_id": instance_id,
        },
        "metrics": {
            "observed_mcp_process_starts": 1,
            "observed_mcp_tool_calls": 7,
        },
        "lifecycle": {"status": "closed"},
        "cache": {"decision": "bootstrap_published"},
    }

    print_compact_scenario_result(result, verbosity="normal", dry_run=False)

    output = capsys.readouterr().out
    assert f"cache_reuse: template_instance_id={instance_id}" in output
    assert "--use-cache" in output
    assert "next command:" not in output


@pytest.mark.parametrize("verbosity", ["quiet", "normal", "verbose"])
def test_named_dry_run_non_json_is_compact_and_side_effect_free(
    verbosity, tmp_path, capsys
) -> None:
    run_dir = tmp_path / verbosity

    assert main(
        [
            "rename",
            "--run-dir",
            str(run_dir),
            "--dry-run",
            "--verbosity",
            verbosity,
        ]
    ) == 0

    output = capsys.readouterr().out
    assert len(output.splitlines()) <= (1 if verbosity == "quiet" else 6)
    assert output.startswith("DRY-RUN rename")
    assert "server_started" not in output
    assert "ordered_steps\": [" not in output
    assert not run_dir.exists()


def test_json_wins_over_verbose_and_remains_one_document(tmp_path, capsys) -> None:
    assert main(
        [
            "rename",
            "--run-dir",
            str(tmp_path / "json"),
            "--dry-run",
            "--json",
            "--verbosity",
            "verbose",
        ]
    ) == 0

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["ok"] is True
    assert payload["server_started"] is False
    assert not output.startswith("RUN ")
    assert len([line for line in output.splitlines() if line.strip()]) == 1


def test_bounded_terminal_text_applies_line_and_byte_limits() -> None:
    source = "\n".join(f"line-{index}-" + "x" * 200 for index in range(1_000))

    quiet = bounded_terminal_text(source, verbosity="quiet")
    normal = bounded_terminal_text(source, verbosity="normal")
    verbose = bounded_terminal_text(source, verbosity="verbose")

    assert len(quiet) <= 4_200
    assert len(normal) <= 16_500
    assert len(verbose) <= 65_700
    assert quiet.count("\n") <= 24
    assert normal.count("\n") <= 100
    assert verbose.count("\n") <= 400
    assert all("output truncated" in value for value in (quiet, normal, verbose))
