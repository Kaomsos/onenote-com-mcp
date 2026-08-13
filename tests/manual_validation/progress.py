"""Content-free terminal progress and compact result rendering."""

from __future__ import annotations

from pathlib import Path
import re
import time
from typing import Any, Callable, Mapping


VERBOSITY_LEVELS = ("quiet", "normal", "verbose")
_VERBOSITY_RANK = {name: index for index, name in enumerate(VERBOSITY_LEVELS)}
_RECONCILIATION_STATES = {
    "applied",
    "indeterminate",
    "not_applied",
    "partially_applied",
}
_GUID_PATTERN = re.compile(
    r"(?:\{[0-9A-Fa-f-]{8,}\})+|"
    r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}(?![0-9A-Fa-f])"
)
_SENSITIVE_ASSIGNMENT_PATTERN = re.compile(
    r"\b(?P<field>(?:[A-Za-z][A-Za-z0-9_]*(?:_id|ID)|query|content|body|xml|binary))"
    r"\s*(?:=|:)\s*"
    r"(?:'[^']*'|\"[^\"]*\"|[^\s,;]+)",
    re.IGNORECASE,
)


def _safe_text(value: Any, *, limit: int = 160) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else f"{text[: max(0, limit - 3)]}..."


def safe_error_text(value: Any) -> str:
    """Redact common OneNote identity/XML shapes before terminal projection."""

    text = str(value)
    if "<" in text and ">" in text[text.index("<") :]:
        text = f"{text[: text.index('<')]}[xml/content-redacted]"
    text = _GUID_PATTERN.sub("[id-redacted]", text)
    return _SENSITIVE_ASSIGNMENT_PATTERN.sub(
        lambda match: f"{match.group('field')}=[redacted]",
        text,
    )


def _bool(value: Any) -> str:
    return str(bool(value)).lower()


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


class RunProgressReporter:
    """Line-oriented progress with no arguments, IDs, content, or response payloads."""

    def __init__(
        self,
        verbosity: str = "normal",
        *,
        enabled: bool = True,
        writer: Callable[[str], None] | None = None,
        clock: Callable[[], float] = time.perf_counter,
        read_batch_size: int = 25,
    ) -> None:
        if verbosity not in VERBOSITY_LEVELS:
            raise ValueError(f"Unknown verbosity: {verbosity}")
        if read_batch_size < 1:
            raise ValueError("read_batch_size must be positive.")
        self.verbosity = verbosity
        self.enabled = bool(enabled)
        self._writer = writer or (lambda line: print(line, flush=True))
        self._clock = clock
        self._read_batch_size = read_batch_size
        self._active_phase = ""
        self._phase_started = 0.0
        self._read_calls = 0
        self._reported_read_calls = 0
        self._mutation_attempts = 0
        self._enabled_policies: tuple[str, ...] = ()
        self._allowlist_tool_count = 0

    @classmethod
    def disabled(cls) -> "RunProgressReporter":
        return cls("quiet", enabled=False)

    def allows(self, minimum: str) -> bool:
        return self.enabled and _VERBOSITY_RANK[self.verbosity] >= _VERBOSITY_RANK[minimum]

    def _write(self, line: str, *, minimum: str = "quiet") -> None:
        if self.allows(minimum):
            self._writer(line)

    def run_started(self, scenario: str, run_dir: Path) -> None:
        self._write(f"RUN {_safe_text(scenario)} artifacts={run_dir.resolve()}")

    def phase_started(self, name: str, index: int, total: int) -> None:
        self.flush_reads()
        self._active_phase = _safe_text(name, limit=60)
        self._phase_started = self._clock()
        self._write(f"[{index}/{total}] {self._active_phase} ...")

    def phase_completed(self, name: str, *, elapsed_seconds: float | None = None) -> None:
        elapsed = (
            max(0.0, self._clock() - self._phase_started)
            if elapsed_seconds is None
            else max(0.0, float(elapsed_seconds))
        )
        self.flush_reads()
        self._write(f"  DONE {_safe_text(name, limit=60)} ({elapsed:.2f}s)", minimum="normal")

    def unit_started(
        self,
        kind: str,
        label: str,
        index: int = 1,
        total: int = 1,
    ) -> None:
        self._write(
            f"  {kind} [{index}/{total}] {_safe_text(label, limit=100)} ...",
            minimum="normal",
        )

    def unit_completed(
        self,
        kind: str,
        label: str,
        index: int = 1,
        total: int = 1,
        *,
        elapsed_seconds: float | None = None,
    ) -> None:
        suffix = "" if elapsed_seconds is None else f" ({max(0.0, elapsed_seconds):.2f}s)"
        self._write(
            f"  {kind} [{index}/{total}] PASS {_safe_text(label, limit=100)}{suffix}",
            minimum="normal",
        )

    def cache_decision(self, decision: str, roles: int) -> None:
        self._write(
            f"  cache={_safe_text(decision, limit=50)} roles={max(0, int(roles))}",
            minimum="normal",
        )

    def server_ready(self, *, enabled_policies: list[str], tool_count: int) -> None:
        self._enabled_policies = tuple(sorted(map(str, enabled_policies)))
        self._allowlist_tool_count = max(0, int(tool_count))
        policies = ",".join(enabled_policies) if enabled_policies else "read-only"
        self._write(
            f"  MCP ready policies={_safe_text(policies)} tools={max(0, int(tool_count))}",
            minimum="verbose",
        )

    def tool_started(self, name: str, attempt: int, *, mutation: bool) -> None:
        if not mutation:
            return
        self._mutation_attempts += 1
        minimum = "normal" if self._active_phase == "scenario" else "verbose"
        detail = f" attempt={attempt}" if self.verbosity == "verbose" else ""
        self._write(f"    mutation {_safe_text(name, limit=80)}{detail} ...", minimum=minimum)

    def tool_completed(
        self,
        name: str,
        attempt: int,
        *,
        mutation: bool,
        elapsed_seconds: float,
        envelope: Mapping[str, Any] | None = None,
    ) -> None:
        if not mutation:
            self._read_calls += 1
            if self._read_calls - self._reported_read_calls >= self._read_batch_size:
                self.flush_reads()
            return
        minimum = "normal" if self._active_phase == "scenario" else "verbose"
        if self.verbosity == "verbose":
            convergence = (envelope or {}).get("convergence")
            reconciliation = (envelope or {}).get("reconciliation")
            details: list[str] = [f"attempt={attempt}", f"elapsed={elapsed_seconds:.2f}s"]
            if isinstance(convergence, Mapping):
                attempts = _nonnegative_int(convergence.get("attempts"))
                stable = _nonnegative_int(convergence.get("stable_observations"))
                if attempts is not None:
                    details.append(f"observe={attempts}")
                if stable is not None:
                    details.append(f"stable={stable}")
            reconciliation_state = (
                str(reconciliation.get("state"))
                if isinstance(reconciliation, Mapping)
                else None
            )
            if reconciliation_state in _RECONCILIATION_STATES:
                details.append(f"reconciliation={reconciliation_state}")
            suffix = " " + " ".join(details)
        else:
            suffix = ""
        status = "PASS" if (envelope or {}).get("ok", True) is True else "FAIL"
        self._write(
            f"    mutation {status} {_safe_text(name, limit=80)}{suffix}",
            minimum=minimum,
        )

    def tool_failed(
        self,
        name: str,
        attempt: int,
        *,
        mutation: bool,
        elapsed_seconds: float,
        error_type: str,
    ) -> None:
        if not mutation:
            self._read_calls += 1
        self._write(
            f"    tool FAIL {_safe_text(name, limit=80)} attempt={attempt} "
            f"elapsed={elapsed_seconds:.2f}s error={_safe_text(error_type, limit=60)}",
            minimum="quiet",
        )

    def flush_reads(self) -> None:
        pending = self._read_calls - self._reported_read_calls
        if pending > 0:
            self._write(
                f"    reads +{pending} (total={self._read_calls})",
                minimum="verbose",
            )
            self._reported_read_calls = self._read_calls

    def failure(self, error: str, *, run_dir: Path | None) -> None:
        self.flush_reads()
        phase = self._active_phase or "preflight"
        suffix = "" if run_dir is None else f" artifacts={run_dir.resolve()}"
        self._write(
            f"FAIL phase={phase} error={_safe_text(safe_error_text(error))}{suffix}"
        )

    def terminal_stats(self) -> dict[str, Any]:
        return {
            "enabled_policies": list(self._enabled_policies),
            "allowlist_tool_count": self._allowlist_tool_count,
            "mutation_attempts": self._mutation_attempts,
            "read_calls": self._read_calls,
        }

    @property
    def current_phase(self) -> str:
        return self._active_phase or "preflight"


def _enabled_policy_names(policy: Mapping[str, Any]) -> list[str]:
    return sorted(name for name, enabled in policy.items() if enabled is True)


def _case_count(scenario_result: Mapping[str, Any]) -> int | None:
    for key in ("case_results", "cases", "operations"):
        value = scenario_result.get(key)
        if isinstance(value, (list, dict)):
            return len(value)
    return None


def print_compact_scenario_result(
    result: Mapping[str, Any],
    *,
    verbosity: str,
    dry_run: bool,
    progress: RunProgressReporter | None = None,
) -> None:
    """Render a bounded human summary; full detail remains in JSON artifacts."""

    scenario = _safe_text(
        result.get("scenario") or result.get("command") or "scenario",
        limit=80,
    )
    run_dir = Path(str(result.get("run_dir", "."))).resolve()
    if dry_run:
        print(f"DRY-RUN {scenario} artifacts={run_dir}", flush=True)
        if verbosity == "quiet":
            return
        steps = [
            str(item.get("step", ""))
            for item in result.get("ordered_steps", [])
            if isinstance(item, dict)
        ]
        cache = result.get("cache") if isinstance(result.get("cache"), Mapping) else {}
        scenario_spec = (
            result.get("scenario_spec")
            if isinstance(result.get("scenario_spec"), Mapping)
            else {}
        )
        policy = (
            scenario_spec.get("mutation_policy")
            if isinstance(scenario_spec.get("mutation_policy"), Mapping)
            else {}
        )
        tools = scenario_spec.get("tool_allowlist", [])
        print(
            f"  plan: steps={len(steps)} cache={_safe_text(cache.get('decision', 'fresh'))} "
            f"lifecycle={_safe_text(result.get('lifecycle', 'unknown'))}",
            flush=True,
        )
        print(
            f"  permissions: enabled={','.join(_enabled_policy_names(policy)) or 'none'} "
            f"tools={len(tools) if isinstance(tools, list) else 0}",
            flush=True,
        )
        if verbosity == "verbose":
            print(f"  ordered_steps: {', '.join(filter(None, steps))}", flush=True)
            if isinstance(tools, list):
                print(f"  tool_allowlist: {', '.join(sorted(map(str, tools)))}", flush=True)
        print("  full plan: rerun with --json", flush=True)
        return

    metrics = result.get("metrics") if isinstance(result.get("metrics"), Mapping) else {}
    elapsed = (
        metrics.get("phases_seconds", {}).get("total")
        if isinstance(metrics.get("phases_seconds"), Mapping)
        else None
    )
    elapsed_text = f" ({float(elapsed):.2f}s)" if isinstance(elapsed, (int, float)) else ""
    report_path = run_dir / "report.md"
    raw_status = str(result.get("status", "passed")).lower()
    status = "PASS" if raw_status in {"pass", "passed", "ok", "success"} else "FAIL"
    print(
        f"{status} {scenario}{elapsed_text} artifacts={run_dir} report={report_path}",
        flush=True,
    )
    if verbosity == "quiet":
        return
    scenario_result = (
        result.get("scenario_result")
        if isinstance(result.get("scenario_result"), Mapping)
        else {}
    )
    lifecycle = result.get("lifecycle") if isinstance(result.get("lifecycle"), Mapping) else {}
    cache = result.get("cache") if isinstance(result.get("cache"), Mapping) else {}
    cases = _case_count(scenario_result)
    case_text = "" if cases is None else f" cases={cases}"
    states: list[str] = []
    if "restored" in scenario_result:
        states.append(f"restored={_bool(scenario_result['restored'])}")
    if "worksite_preserved" in scenario_result:
        states.append(f"worksite={_bool(scenario_result['worksite_preserved'])}")
    states.extend(
        (
            f"lifecycle={_safe_text(lifecycle.get('status', 'unknown'))}",
            f"cache={_safe_text(cache.get('decision', 'fresh'))}",
        )
    )
    print(f"  result:{case_text} {' '.join(states)}", flush=True)
    calls = metrics.get("observed_mcp_tool_calls", 0)
    processes = metrics.get("observed_mcp_process_starts", 0)
    print(f"  execution: mcp_processes={processes} mcp_tool_calls={calls}", flush=True)
    if verbosity == "verbose":
        phases = metrics.get("phases_seconds")
        if isinstance(phases, Mapping):
            rendered = ", ".join(
                f"{_safe_text(name, limit=40)}={float(value):.2f}s"
                for name, value in sorted(phases.items())
                if isinstance(value, (int, float))
            )
            if rendered:
                print(f"  timings: {rendered}", flush=True)
        stats = progress.terminal_stats() if progress is not None else {}
        policies = stats.get("enabled_policies", [])
        print(
            f"  policy: enabled={','.join(map(str, policies)) or 'none'} "
            f"allowlist_tools={int(stats.get('allowlist_tool_count', 0))}",
            flush=True,
        )
        print(
            f"  calls: mutation_attempts={int(stats.get('mutation_attempts', 0))} "
            f"reads={int(stats.get('read_calls', 0))}",
            flush=True,
        )
        print(f"  result_json: {run_dir / 'run-result.json'}", flush=True)


def bounded_terminal_text(text: str, *, verbosity: str) -> str:
    """Bound captured child diagnostics in non-JSON all output."""

    limits = {
        "quiet": (24, 4_096),
        "normal": (100, 16_384),
        "verbose": (400, 65_536),
    }
    max_lines, max_chars = limits[verbosity]
    source = "\n".join(safe_error_text(line) for line in text.rstrip().splitlines())
    lines = source.splitlines()
    truncated = len(lines) > max_lines or len(source) > max_chars
    kept = "\n".join(lines[:max_lines])[:max_chars].rstrip()
    if truncated:
        kept += "\n... output truncated; inspect the child run artifacts for full detail."
    return kept


__all__ = [
    "RunProgressReporter",
    "VERBOSITY_LEVELS",
    "bounded_terminal_text",
    "print_compact_scenario_result",
    "safe_error_text",
]
