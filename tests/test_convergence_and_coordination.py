from __future__ import annotations

import asyncio
import threading
import time

import pytest

from local_onenote_mcp.onenote_errors import (
    OneNoteBridgeError,
    OneNoteCoordinationTimeoutError,
    OneNoteFileUnavailableError,
    OneNoteModalUIBlockedError,
    OneNoteNotYetSynchronizedError,
    OneNoteObjectUnavailableError,
    OneNoteOperationTimeoutError,
    bridge_error,
    idempotent_retry_allowed,
    transient_read_error,
)
from local_onenote_mcp.services.convergence import UNSET, ConvergenceConfig, converge
from local_onenote_mcp.services.coordination import ReadWriteCoordinator
from local_onenote_mcp.services.reconciliation import (
    ReconciliationState,
    reconcile_mutation,
)
from local_onenote_mcp.tools.responses import caught


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_convergence_omitted_initial_value_is_not_a_sample():
    values = iter([None, None])
    clock = FakeClock()
    result = converge(
        lambda: next(values),
        lambda value: value is None,
        lambda value: value,
        config=ConvergenceConfig(
            deadline_seconds=10,
            interval_seconds=1,
            required_stable_observations=2,
            max_observations=4,
        ),
        clock=clock,
        sleeper=clock.sleep,
    )
    assert result.converged is True
    assert result.attempts == 2
    assert result.stable_observations == 2


def test_convergence_none_initial_value_counts_as_first_sample():
    values = iter([None])
    clock = FakeClock()
    result = converge(
        lambda: next(values),
        lambda value: value is None,
        lambda value: value,
        config=ConvergenceConfig(
            deadline_seconds=10,
            interval_seconds=1,
            required_stable_observations=2,
            max_observations=4,
        ),
        clock=clock,
        sleeper=clock.sleep,
        initial_value=None,
    )
    assert result.converged is True
    assert result.attempts == 2
    assert result.stable_observations == 2
    assert result.value is None


def test_convergence_none_initial_value_reversion_requires_two_new_stable_reads():
    values = iter(["visible", None, None])
    clock = FakeClock()
    result = converge(
        lambda: next(values),
        lambda value: value is None,
        lambda value: value,
        config=ConvergenceConfig(
            deadline_seconds=10,
            interval_seconds=1,
            required_stable_observations=2,
            max_observations=5,
        ),
        clock=clock,
        sleeper=clock.sleep,
        initial_value=None,
    )
    assert result.converged is True
    assert result.attempts == 4
    assert result.observation_history[0]["stable"] == 1
    assert result.observation_history[1]["stable"] == 0
    assert result.observation_history[3]["stable"] == 2
    assert UNSET is not None


def test_convergence_requires_two_matching_postconditions_after_a_reversion():
    values = iter(["old", "new", "old", "new", "new"])
    clock = FakeClock()

    result = converge(
        lambda: next(values),
        lambda value: value == "new",
        lambda value: value,
        config=ConvergenceConfig(
            deadline_seconds=10,
            interval_seconds=1,
            required_stable_observations=2,
            max_observations=5,
        ),
        clock=clock,
        sleeper=clock.sleep,
    )

    assert result.converged is True
    assert result.attempts == 5
    assert result.stable_observations == 2
    assert result.observation_history[1]["stable"] == 1
    assert result.observation_history[2]["stable"] == 0


def test_convergence_records_only_typed_transient_category():
    values = iter(
        [
            OneNoteNotYetSynchronizedError("secret payload", operation="get_hierarchy"),
            "new",
            "new",
        ]
    )
    clock = FakeClock()

    def observe():
        value = next(values)
        if isinstance(value, Exception):
            raise value
        return value

    result = converge(
        observe,
        lambda value: value == "new",
        lambda value: value,
        config=ConvergenceConfig(deadline_seconds=3, interval_seconds=1, max_observations=3),
        clock=clock,
        sleeper=clock.sleep,
        transient=transient_read_error,
    )

    assert result.converged is True
    assert result.transient_errors == ("onenote_not_yet_synchronized",)
    assert "secret payload" not in repr(result.observation_history)


def test_convergence_does_not_swallow_unclassified_backend_errors_by_default():
    error = OneNoteBridgeError("secret payload", operation="get_hierarchy")

    with pytest.raises(OneNoteBridgeError) as raised:
        converge(lambda: (_ for _ in ()).throw(error), lambda _value: True, lambda value: value)

    assert raised.value is error


@pytest.mark.parametrize(
    ("hresult", "expected_type", "code", "retryability"),
    [
        (0x8004201D, OneNoteNotYetSynchronizedError, "onenote_not_yet_synchronized", "read_after_delay"),
        (0x80042023, OneNoteOperationTimeoutError, "onenote_operation_timeout", "reconcile_before_retry"),
        (0x80042005, OneNoteObjectUnavailableError, "onenote_object_unavailable", "read_after_delay"),
        (0x80042006, OneNoteFileUnavailableError, "onenote_file_unavailable", "read_after_delay"),
    ],
)
def test_documented_hresult_families_have_stable_typed_errors(
    hresult, expected_type, code, retryability
):
    error = bridge_error("backend payload", operation="get_hierarchy", hresult=hresult)

    assert isinstance(error, expected_type)
    assert error.code == code
    assert error.retryability == retryability
    assert error.hresult == f"0x{hresult:08X}"
    assert "backend payload" not in str(error)


def test_object_unavailable_is_read_transient_but_not_mutation_replay_evidence():
    error = bridge_error("backend payload", operation="update_page_content", hresult=0x80042014)

    assert transient_read_error(error) is True
    assert idempotent_retry_allowed(error) is False


def test_modal_hresult_is_typed_and_not_marked_for_automatic_replay():
    error = bridge_error(
        "backend message",
        operation="update_page_content",
        hresult=-2147213264,
        category="OperationStopped",
    )

    assert isinstance(error, OneNoteModalUIBlockedError)
    response = caught(error)
    assert response["error"]["code"] == "onenote_modal_ui_blocked"
    details = response["error"]["details"]
    assert details["hresult"] == "0x80042030"
    assert details["retryability"] == "after_user_action"
    assert details["reconciliation"] == "indeterminate"


def test_reconciliation_retries_only_once_after_exact_pre_state():
    state = {"value": "before"}
    calls = []

    def execute():
        calls.append("execute")
        if len(calls) == 1:
            raise OneNoteBridgeError("transient", operation="update_page_content")
        state["value"] = "after"
        return "ok"

    result = reconcile_mutation(
        execute=execute,
        observe=lambda: state["value"],
        is_pre_state=lambda value: value == "before",
        is_post_state=lambda value: value == "after",
        retry_if_unchanged=True,
        retry_allowed=lambda _exc: True,
    )

    assert result.state is ReconciliationState.APPLIED
    assert result.attempts == 2
    assert calls == ["execute", "execute"]


def test_reconciliation_accepts_com_error_when_postcondition_is_live():
    state = {"value": "before"}

    def execute():
        state["value"] = "after"
        raise OneNoteBridgeError("late COM error", operation="update_page_content")

    result = reconcile_mutation(
        execute=execute,
        observe=lambda: state["value"],
        is_pre_state=lambda value: value == "before",
        is_post_state=lambda value: value == "after",
    )

    assert result.state is ReconciliationState.APPLIED
    assert result.attempts == 1
    assert result.error is not None


def test_modal_error_is_not_replayed_even_when_exact_pre_state_is_observed():
    calls = []
    modal = bridge_error(
        "secret raw payload",
        operation="update_page_content",
        hresult=-2147213264,
    )

    result = reconcile_mutation(
        execute=lambda: calls.append("execute") or (_ for _ in ()).throw(modal),
        observe=lambda: "before",
        is_pre_state=lambda value: value == "before",
        is_post_state=lambda value: value == "after",
        retry_if_unchanged=True,
        retry_allowed=idempotent_retry_allowed,
    )

    assert result.state is ReconciliationState.NOT_APPLIED
    assert calls == ["execute"]
    assert "secret" not in str(modal)


def test_successful_execute_defers_transient_read_error_to_convergence():
    result = reconcile_mutation(
        execute=lambda: "accepted",
        observe=lambda: (_ for _ in ()).throw(
            OneNoteBridgeError("read failed", operation="get_page_content")
        ),
        is_pre_state=lambda _value: False,
        is_post_state=lambda _value: False,
    )

    assert result.state is ReconciliationState.INDETERMINATE
    assert result.execution_succeeded is True
    assert result.execution_result == "accepted"


def test_coordinator_serializes_mutations_and_blocks_reads_from_middle_window():
    coordinator = ReadWriteCoordinator(default_timeout_seconds=1)
    first_entered = threading.Event()
    release_first = threading.Event()
    events: list[str] = []

    def first_mutation():
        with coordinator.mutation():
            events.append("first-start")
            first_entered.set()
            release_first.wait(1)
            events.append("first-end")

    def second_mutation():
        first_entered.wait(1)
        with coordinator.mutation():
            events.append("second")

    def read():
        first_entered.wait(1)
        with coordinator.read():
            events.append("read")

    threads = [
        threading.Thread(target=first_mutation),
        threading.Thread(target=second_mutation),
        threading.Thread(target=read),
    ]
    for thread in threads:
        thread.start()
    assert first_entered.wait(1)
    time.sleep(0.02)
    assert events == ["first-start"]
    release_first.set()
    for thread in threads:
        thread.join(1)

    assert events[0:2] == ["first-start", "first-end"]
    assert events.index("second") < events.index("read")


def test_coordinator_allows_shared_reads_and_releases_after_exception_and_timeout():
    coordinator = ReadWriteCoordinator(default_timeout_seconds=0.05)
    first_read = threading.Event()
    second_read = threading.Event()
    release = threading.Event()

    def reader(entered: threading.Event):
        with coordinator.read():
            entered.set()
            release.wait(1)

    threads = [
        threading.Thread(target=reader, args=(first_read,)),
        threading.Thread(target=reader, args=(second_read,)),
    ]
    for thread in threads:
        thread.start()
    assert first_read.wait(1) and second_read.wait(1)
    with pytest.raises(OneNoteCoordinationTimeoutError):
        with coordinator.mutation(timeout_seconds=0.01):
            pass
    release.set()
    for thread in threads:
        thread.join(1)

    with pytest.raises(RuntimeError):
        with coordinator.mutation():
            raise RuntimeError("boom")
    with coordinator.mutation():
        pass

    with pytest.raises(asyncio.CancelledError):
        with coordinator.mutation():
            raise asyncio.CancelledError()
    with coordinator.read():
        pass


def test_coordinator_invalidates_before_mutation_and_advances_generation():
    events = []
    coordinator = ReadWriteCoordinator(
        mutation_invalidator=lambda generation: events.append(("invalidate", generation))
    )

    with coordinator.mutation():
        events.append(("com", coordinator.generation))

    assert events == [("invalidate", 1), ("com", 1)]


def test_writer_waits_for_old_reader_fill_then_invalidates_that_generation():
    cache: dict[str, tuple[int, str]] = {}
    events: list[str] = []
    reader_entered = threading.Event()
    allow_reader_fill = threading.Event()
    coordinator = ReadWriteCoordinator(
        mutation_invalidator=lambda _generation: (cache.clear(), events.append("invalidate"))
    )

    def old_reader():
        with coordinator.read():
            generation = coordinator.generation
            reader_entered.set()
            allow_reader_fill.wait(1)
            cache["hierarchy"] = (generation, "old")
            events.append("old-fill")

    def writer():
        reader_entered.wait(1)
        with coordinator.mutation():
            events.append("mutation")

    reader_thread = threading.Thread(target=old_reader)
    writer_thread = threading.Thread(target=writer)
    reader_thread.start()
    writer_thread.start()
    assert reader_entered.wait(1)
    time.sleep(0.02)
    assert events == []
    allow_reader_fill.set()
    reader_thread.join(1)
    writer_thread.join(1)

    assert events == ["old-fill", "invalidate", "mutation"]
    assert cache == {}
    assert coordinator.generation == 1
