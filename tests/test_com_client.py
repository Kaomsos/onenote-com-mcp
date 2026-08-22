"""Delivery-state, framing, and lifecycle contracts for COM clients."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from local_onenote_mcp.bridge import OneNoteBridge
from local_onenote_mcp.com_client import (
    ADAPTER_ONE_SHOT_POWERSHELL,
    ADAPTER_PERSISTENT_POWERSHELL,
    DELIVERY_NOT_SUBMITTED,
    DELIVERY_POSSIBLY_DISPATCHED,
    DELIVERY_RESPONDED,
    MAX_DECODED_FRAME_BYTES,
    MAX_ENCODED_FRAME_BYTES,
    NOT_ATTEMPTED_DISPATCH_LOCK_TIMEOUT,
    NOT_ATTEMPTED_HOST_TRANSITION,
    REFRESH_HOST_DISCARD_UNCONFIRMED,
    REFRESH_HOST_DISCARDED,
    REFRESH_NOT_ATTEMPTED,
    REFRESH_NOT_NEEDED,
    REFRESH_REFRESHED,
    REFRESH_REJECTED_CLOSED,
    ComClientError,
    ComRefreshResult,
    OneShotPowerShellClient,
    PersistentPowerShellClient,
    create_com_client,
    decode_protocol_frame,
    encode_protocol_frame,
    validate_response_payload,
    validate_success_epoch,
)
from local_onenote_mcp.powershell_host import POWERSHELL_PERSISTENT_HOST_SCRIPT
from local_onenote_mcp.onenote_errors import (
    OneNoteBridgeError,
    OneNoteNotYetSynchronizedError,
    OneNoteObjectUnavailableError,
    OneNoteOperationTimeoutError,
    bridge_error,
    idempotent_retry_allowed,
)
from local_onenote_mcp.services.mutation_control import (
    MutationAttemptExecutor,
    MutationAttemptPolicy,
    MutationIdentityPolicy,
    MutationReplayPolicy,
    ReconciliationState,
)
from local_onenote_mcp.settings import parse_bridge_adapter_name


FAKE_HOST = Path(__file__).resolve().parent / "support" / "fake_persistent_host.py"


def _host_command(*extra: str) -> list[str]:
    return [sys.executable, "-u", str(FAKE_HOST), *extra]


def _client(**kwargs) -> PersistentPowerShellClient:
    kwargs.setdefault("host_command", _host_command())
    kwargs.setdefault("close_wait_seconds", 1.0)
    return PersistentPowerShellClient(**kwargs)


def test_parse_adapter_name_defaults_and_rejects_unknown(monkeypatch) -> None:
    monkeypatch.delenv("LOCAL_ONENOTE_BRIDGE_ADAPTER", raising=False)
    assert parse_bridge_adapter_name() == ADAPTER_PERSISTENT_POWERSHELL
    monkeypatch.setenv("LOCAL_ONENOTE_BRIDGE_ADAPTER", ADAPTER_ONE_SHOT_POWERSHELL)
    assert parse_bridge_adapter_name() == ADAPTER_ONE_SHOT_POWERSHELL
    with pytest.raises(ValueError):
        parse_bridge_adapter_name("pywin32")


def test_create_client_does_not_start_host() -> None:
    client = create_com_client(ADAPTER_PERSISTENT_POWERSHELL, host_command=_host_command())
    assert client.state == "NEW"
    assert client.generation is None


def test_custom_frame_limits_assemble_matching_host_script() -> None:
    default = PersistentPowerShellClient()
    assert default._host_script is POWERSHELL_PERSISTENT_HOST_SCRIPT
    assert str(MAX_DECODED_FRAME_BYTES) in default._host_script
    assert str(MAX_ENCODED_FRAME_BYTES) in default._host_script

    custom = PersistentPowerShellClient(
        max_decoded_frame_bytes=4096,
        max_encoded_frame_bytes=8192,
    )
    assert custom._host_script is not POWERSHELL_PERSISTENT_HOST_SCRIPT
    assert "$script:MaxDecodedFrameBytes=4096" in custom._host_script
    assert "$script:MaxEncodedFrameBytes=8192" in custom._host_script
    assert str(MAX_DECODED_FRAME_BYTES) not in custom._host_script

    injected = PersistentPowerShellClient(
        host_script="injected-host-script",
        max_decoded_frame_bytes=64,
        max_encoded_frame_bytes=128,
    )
    assert injected._host_script == "injected-host-script"


def test_persistent_ready_and_serial_generation_sequence(tmp_path) -> None:
    audit = tmp_path / "audit.jsonl"
    client = _client()
    bridge = OneNoteBridge(timeout_seconds=5, audit_path=audit, client=client)
    first = bridge.call("get_hierarchy", start_id="", scope=2, schema=2)
    second = bridge.call("create_new_page", section_id="s", new_page_style=0)
    assert first == {"xml": "<one:Notebooks/>", "com_epoch": 1}
    assert second == {"page_id": "page-1", "com_epoch": 1}
    assert client.generation == 1
    rows = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
    assert [row["delivery_state"] for row in rows] == [DELIVERY_RESPONDED, DELIVERY_RESPONDED]
    assert [row["adapter"] for row in rows] == [ADAPTER_PERSISTENT_POWERSHELL] * 2
    assert all(row["client_generation"] == 1 for row in rows)
    bridge.close()
    assert client.state == "CLOSED"
    with pytest.raises(OneNoteBridgeError) as raised:
        bridge.call("get_hierarchy", start_id="", scope=2, schema=2)
    assert raised.value.delivery_state == DELIVERY_NOT_SUBMITTED
    assert raised.value.reconciliation == "not_applied"
    assert client.state == "CLOSED"


def test_close_idle_bridge_does_not_start_host() -> None:
    client = _client()
    OneNoteBridge(client=client).close()
    assert client.state == "CLOSED"
    assert client.generation is None


def test_noise_after_ready_is_protocol_violation() -> None:
    client = _client(host_command=_host_command("--mode", "noise"))
    with pytest.raises(ComClientError) as raised:
        client.execute("get_hierarchy", {}, timeout_seconds=2)
    assert raised.value.delivery_state == DELIVERY_POSSIBLY_DISPATCHED
    client.close()


def test_in_flight_timeout_does_not_replay_same_request() -> None:
    client = _client(host_command=_host_command("--mode", "hang"))
    executed = {"count": 0}

    def once():
        executed["count"] += 1
        return client.execute("update_page_content", {"xml": "x"}, timeout_seconds=0.2)

    with pytest.raises(ComClientError) as raised:
        once()
    assert raised.value.delivery_state == DELIVERY_POSSIBLY_DISPATCHED
    assert raised.value.timed_out is True
    assert raised.value.generation == 1
    assert client.state == "NEW"
    assert executed["count"] == 1
    client.close()


def test_same_client_rebuilds_generation_after_poison_reap(tmp_path) -> None:
    once = tmp_path / "hang-once"
    client = _client(
        host_command=_host_command("--mode", "hang-once", "--once-file", str(once))
    )
    with pytest.raises(ComClientError) as raised:
        client.execute("update_page_content", {"xml": "x"}, timeout_seconds=0.2)
    assert raised.value.delivery_state == DELIVERY_POSSIBLY_DISPATCHED
    assert raised.value.generation == 1
    assert client.state == "NEW"
    assert client._reader_io is None
    result = client.execute("get_hierarchy", {}, timeout_seconds=2)
    assert result["ok"] is True
    assert client.generation == 2
    client.close()
    assert client.state == "CLOSED"
    assert client._reader_io is None


def test_host_crash_is_possibly_dispatched() -> None:
    client = _client(host_command=_host_command("--mode", "crash"))
    with pytest.raises(ComClientError) as raised:
        client.execute("get_hierarchy", {}, timeout_seconds=2)
    assert raised.value.delivery_state == DELIVERY_POSSIBLY_DISPATCHED
    client.close()


def test_start_failure_is_not_submitted() -> None:
    client = _client(host_command=_host_command("--mode", "fatal"))
    with pytest.raises(ComClientError) as raised:
        client.execute("get_hierarchy", {}, timeout_seconds=2)
    assert raised.value.delivery_state == DELIVERY_NOT_SUBMITTED
    client.close()


def test_ok_false_keeps_generation() -> None:
    client = _client(host_command=_host_command("--mode", "ok-false"))
    first = client.execute("get_hierarchy", {}, timeout_seconds=2)
    second = client.execute("get_hierarchy", {}, timeout_seconds=2)
    assert first["ok"] is False
    assert second["ok"] is False
    assert client.generation == 1
    client.close()


def test_close_in_flight_is_possibly_dispatched() -> None:
    client = _client(host_command=_host_command("--mode", "hang"))
    errors: list[BaseException] = []

    def worker():
        try:
            client.execute("update_page_content", {"xml": "x"}, timeout_seconds=5)
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    deadline = time.time() + 2
    while client.state != "READY" and time.time() < deadline:
        time.sleep(0.02)
    time.sleep(0.05)
    client.close()
    thread.join(2)
    assert errors and isinstance(errors[0], ComClientError)
    assert errors[0].delivery_state == DELIVERY_POSSIBLY_DISPATCHED
    assert client.state == "CLOSED"


def test_request_oversize_is_not_submitted() -> None:
    client = _client(max_decoded_frame_bytes=64, max_encoded_frame_bytes=128)
    with pytest.raises(ComClientError) as raised:
        client.execute("update_page_content", {"xml": "x" * 200}, timeout_seconds=2)
    assert raised.value.delivery_state == DELIVERY_NOT_SUBMITTED
    client.close()


def test_response_oversize_is_possibly_dispatched() -> None:
    client = _client(
        host_command=_host_command("--mode", "oversized"),
        max_decoded_frame_bytes=200,
        max_encoded_frame_bytes=300,
    )
    with pytest.raises(ComClientError) as raised:
        client.execute("get_hierarchy", {}, timeout_seconds=2)
    assert raised.value.delivery_state == DELIVERY_POSSIBLY_DISPATCHED
    client.close()


def test_truncated_frame_is_possibly_dispatched() -> None:
    client = _client(
        host_command=_host_command("--mode", "no-newline"),
        max_decoded_frame_bytes=1024,
        max_encoded_frame_bytes=200,
    )
    with pytest.raises(ComClientError) as raised:
        client.execute("get_hierarchy", {}, timeout_seconds=2)
    assert raised.value.delivery_state == DELIVERY_POSSIBLY_DISPATCHED
    client.close()


def test_non_ascii_round_trip() -> None:
    client = _client(host_command=_host_command("--mode", "non-ascii"))
    result = client.execute(
        "get_hierarchy",
        {"start_id": "测", "scope": 2, "schema": 2},
        timeout_seconds=2,
    )
    assert result["data"]["xml"] == "<one:Notebooks>测</one:Notebooks>"
    client.close()


def test_mismatch_sequence_poisons() -> None:
    client = _client(host_command=_host_command("--mode", "mismatch"))
    with pytest.raises(ComClientError) as raised:
        client.execute("get_hierarchy", {}, timeout_seconds=2)
    assert raised.value.delivery_state == DELIVERY_POSSIBLY_DISPATCHED
    client.close()


def test_one_shot_three_states(monkeypatch, tmp_path) -> None:
    client = OneShotPowerShellClient()

    def fail_write(_payload):
        raise OSError("disk full")

    monkeypatch.setattr(OneShotPowerShellClient, "_write_temp_json", staticmethod(fail_write))
    with pytest.raises(ComClientError) as raised:
        client.execute("get_hierarchy", {}, timeout_seconds=1)
    assert raised.value.delivery_state == DELIVERY_NOT_SUBMITTED

    request = tmp_path / "req.json"
    response = tmp_path / "resp.json"

    def write(payload):
        request.write_text(json.dumps(payload), encoding="utf-8")
        return request

    monkeypatch.setattr(OneShotPowerShellClient, "_write_temp_json", staticmethod(write))
    monkeypatch.setattr(
        OneShotPowerShellClient, "_reserve_temp_path", staticmethod(lambda: response)
    )

    def missing(*_args, **_kwargs):
        raise FileNotFoundError("powershell.exe")

    monkeypatch.setattr("local_onenote_mcp.com_client.subprocess.run", missing)
    with pytest.raises(ComClientError) as raised:
        client.execute("get_hierarchy", {}, timeout_seconds=1)
    assert raised.value.delivery_state == DELIVERY_NOT_SUBMITTED

    def ok(*_args, **_kwargs):
        response.write_text(json.dumps({"ok": False, "error": {"hresult": 1}}), encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("local_onenote_mcp.com_client.subprocess.run", ok)
    result = client.execute("get_hierarchy", {}, timeout_seconds=1)
    assert result["ok"] is False

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="powershell.exe", timeout=1)

    monkeypatch.setattr("local_onenote_mcp.com_client.subprocess.run", timeout)
    with pytest.raises(ComClientError) as raised:
        client.execute("get_hierarchy", {}, timeout_seconds=1)
    assert raised.value.delivery_state == DELIVERY_POSSIBLY_DISPATCHED
    assert raised.value.timed_out is True

    def no_response(*_args, **_kwargs):
        return SimpleNamespace(returncode=7, stdout="", stderr="fail")

    monkeypatch.setattr("local_onenote_mcp.com_client.subprocess.run", no_response)
    with pytest.raises(ComClientError) as raised:
        client.execute("get_hierarchy", {}, timeout_seconds=1)
    assert raised.value.delivery_state == DELIVERY_POSSIBLY_DISPATCHED


def test_possibly_dispatched_timeout_is_not_replayed() -> None:
    error = bridge_error(
        "timed out",
        operation="update_page_content",
        timed_out=True,
        delivery_state=DELIVERY_POSSIBLY_DISPATCHED,
    )
    assert isinstance(error, OneNoteOperationTimeoutError)
    assert idempotent_retry_allowed(error) is False
    assert "delivery_state" not in error.public_details()
    assert error.reconciliation == "indeterminate"
    calls = []

    policy = MutationAttemptPolicy(
        policy_id="test_no_replay_dispatch",
        replay_policy=MutationReplayPolicy.EXACT_PRESTATE_TYPED_TRANSIENT,
        identity_policy=MutationIdentityPolicy.PRESERVED,
        observer_description="test",
        partial_boundary_description="test",
    )
    outcome = MutationAttemptExecutor().execute(
        policy,
        execute=lambda: calls.append("e") or (_ for _ in ()).throw(error),
        observe=lambda: "before",
        is_pre_state=lambda value: value == "before",
        is_post_state=lambda value: value == "after",
    )
    assert calls == ["e"]
    assert outcome.state is ReconciliationState.NOT_APPLIED


def test_responded_timeout_and_sync_errors_remain_replayable() -> None:
    timeout = bridge_error(
        "timed out",
        operation="update_page_content",
        timed_out=True,
        delivery_state=DELIVERY_RESPONDED,
    )
    sync = OneNoteNotYetSynchronizedError("safe", operation="get_page_content")
    unavailable = OneNoteObjectUnavailableError("gone", operation="get_page_content")
    assert idempotent_retry_allowed(timeout) is True
    assert idempotent_retry_allowed(sync) is True
    assert idempotent_retry_allowed(unavailable) is False


def test_persistent_init_failure_does_not_fall_back(tmp_path) -> None:
    audit = tmp_path / "audit.jsonl"
    client = _client(host_command=_host_command("--mode", "fatal"))
    bridge = OneNoteBridge(timeout_seconds=2, audit_path=audit, client=client)
    with pytest.raises(OneNoteBridgeError) as raised:
        bridge.call("get_hierarchy", start_id="", scope=2, schema=2)
    assert raised.value.delivery_state == DELIVERY_NOT_SUBMITTED
    assert bridge.adapter_id == ADAPTER_PERSISTENT_POWERSHELL
    record = json.loads(audit.read_text(encoding="utf-8"))
    assert record["adapter"] == ADAPTER_PERSISTENT_POWERSHELL
    assert record["delivery_state"] == DELIVERY_NOT_SUBMITTED
    bridge.close()


def test_close_releases_stdout_and_reader_io() -> None:
    client = _client()
    client.execute("get_hierarchy", {}, timeout_seconds=2)
    process = client._process
    assert process is not None
    stdout = process.stdout
    assert stdout is not None
    client.close()
    assert client._reader_io is None
    assert client._process is None
    assert stdout.closed


def test_ready_generation_mismatch_is_not_submitted() -> None:
    client = _client(host_command=_host_command("--mode", "ready-bad-generation"))
    with pytest.raises(ComClientError) as raised:
        client.execute("get_hierarchy", {}, timeout_seconds=2)
    assert raised.value.delivery_state == DELIVERY_NOT_SUBMITTED
    client.close()


def test_ready_missing_field_is_not_submitted() -> None:
    client = _client(host_command=_host_command("--mode", "ready-missing-field"))
    with pytest.raises(ComClientError) as raised:
        client.execute("get_hierarchy", {}, timeout_seconds=2)
    assert raised.value.delivery_state == DELIVERY_NOT_SUBMITTED
    client.close()


def test_ready_wrong_type_is_not_submitted() -> None:
    client = _client(host_command=_host_command("--mode", "ready-string-version"))
    with pytest.raises(ComClientError) as raised:
        client.execute("get_hierarchy", {}, timeout_seconds=2)
    assert raised.value.delivery_state == DELIVERY_NOT_SUBMITTED
    client.close()


def test_response_missing_data_is_possibly_dispatched() -> None:
    client = _client(host_command=_host_command("--mode", "response-missing-data"))
    with pytest.raises(ComClientError) as raised:
        client.execute("get_hierarchy", {}, timeout_seconds=2)
    assert raised.value.delivery_state == DELIVERY_POSSIBLY_DISPATCHED
    client.close()


def test_response_string_ok_is_not_coerced() -> None:
    client = _client(host_command=_host_command("--mode", "response-ok-string"))
    with pytest.raises(ComClientError) as raised:
        client.execute("get_hierarchy", {}, timeout_seconds=2)
    assert raised.value.delivery_state == DELIVERY_POSSIBLY_DISPATCHED
    client.close()


def test_response_string_generation_is_protocol_violation() -> None:
    client = _client(host_command=_host_command("--mode", "response-generation-string"))
    with pytest.raises(ComClientError) as raised:
        client.execute("get_hierarchy", {}, timeout_seconds=2)
    assert raised.value.delivery_state == DELIVERY_POSSIBLY_DISPATCHED
    client.close()


def test_response_schema_rejects_loose_types() -> None:
    complete = {
        "protocol_version": 1,
        "generation": 1,
        "sequence": 1,
        "kind": "response",
        "ok": True,
        "data": {"xml": "<one:Notebooks/>"},
        "error": None,
    }
    assert validate_response_payload(complete, generation=1, sequence=1)["ok"] is True
    with pytest.raises(ValueError):
        validate_response_payload({**complete, "ok": "false"}, generation=1, sequence=1)
    with pytest.raises(ValueError):
        validate_response_payload({**complete, "generation": True}, generation=1, sequence=1)
    with pytest.raises(ValueError):
        validate_response_payload({**complete, "generation": "1"}, generation=1, sequence=1)
    missing = dict(complete)
    del missing["data"]
    with pytest.raises(ValueError):
        validate_response_payload(missing, generation=1, sequence=1)
    failed = {
        **complete,
        "ok": False,
        "data": None,
        "error": {
            "message": "x",
            "hresult": 1,
            "wrapper_hresult": 1,
            "exception_depth": 0,
            "leaf_exception_type": "System.Exception",
            "category": "OperationStopped",
        },
    }
    assert validate_response_payload(failed, generation=1, sequence=1)["ok"] is False
    incomplete_error = {**failed, "error": {"message": "x"}}
    with pytest.raises(ValueError):
        validate_response_payload(incomplete_error, generation=1, sequence=1)
    frame = encode_protocol_frame(complete, max_decoded=4096, max_encoded=4096)
    decoded = decode_protocol_frame(frame[:-1], max_decoded=4096, max_encoded=4096)
    assert decoded["kind"] == "response"
    with pytest.raises(ValueError):
        decode_protocol_frame(
            encode_protocol_frame(
                {**complete, "protocol_version": "1"},
                max_decoded=4096,
                max_encoded=4096,
            )[:-1],
            max_decoded=4096,
            max_encoded=4096,
        )


def test_frame_utf8_json_is_not_utf16() -> None:
    frame = encode_protocol_frame(
        {
            "protocol_version": 1,
            "generation": 1,
            "sequence": 1,
            "kind": "request",
            "operation": "get_hierarchy",
            "params": {"start_id": "测"},
        },
        max_decoded=4096,
        max_encoded=4096,
    )
    assert frame.startswith(b"ONB1 ")
    assert b"\x00" not in frame


def test_validate_success_epoch_requires_exact_next_int() -> None:
    assert validate_success_epoch({"com_epoch": 2}, expected=2) == 2
    with pytest.raises(ValueError):
        validate_success_epoch(None, expected=2)
    with pytest.raises(ValueError):
        validate_success_epoch({"com_epoch": "2"}, expected=2)
    with pytest.raises(ValueError):
        validate_success_epoch({"com_epoch": True}, expected=2)
    with pytest.raises(ValueError):
        validate_success_epoch({"com_epoch": 3}, expected=2)
    with pytest.raises(ValueError):
        validate_success_epoch({}, expected=2)


def test_refresh_com_on_new_client_is_not_needed() -> None:
    client = _client()
    result = client.refresh_com(timeout_seconds=1)
    assert result.outcome == REFRESH_NOT_NEEDED
    assert result.content_free_projection() == {"outcome": REFRESH_NOT_NEEDED}
    assert client.state == "NEW"
    assert client.generation is None
    client.close()


def test_one_shot_refresh_com_is_not_needed() -> None:
    result = OneShotPowerShellClient().refresh_com(timeout_seconds=1)
    assert result.outcome == REFRESH_NOT_NEEDED


def test_refresh_com_success_increments_epoch_on_same_generation() -> None:
    client = _client()
    first = client.execute("get_hierarchy", {}, timeout_seconds=2)
    assert first["ok"] is True
    assert client.generation == 1
    assert client.com_epoch == 1
    result = client.refresh_com(timeout_seconds=2)
    assert result.outcome == REFRESH_REFRESHED
    assert result.generation == 1
    assert result.com_epoch == 2
    assert client.state == "READY"
    assert client.com_epoch == 2
    second = client.execute("get_hierarchy", {}, timeout_seconds=2)
    assert second["ok"] is True
    assert second["data"]["com_epoch"] == 2
    assert client.generation == 1
    client.close()


@pytest.mark.parametrize(
    "mode",
    ("refresh-activation-failure", "refresh-probe-failure"),
)
def test_refresh_ok_false_discards_host(mode: str) -> None:
    client = _client(host_command=_host_command("--mode", mode))
    client.execute("get_hierarchy", {}, timeout_seconds=2)
    result = client.refresh_com(timeout_seconds=2)
    assert result.outcome == REFRESH_HOST_DISCARDED
    assert result.discarded_generation == 1
    assert result.content_free_projection() == {
        "outcome": REFRESH_HOST_DISCARDED,
        "discarded_generation": 1,
    }
    assert client.state == "NEW"
    rebuilt = client.execute("get_hierarchy", {}, timeout_seconds=2)
    assert rebuilt["ok"] is True
    assert client.generation == 2
    assert client.com_epoch == 1
    client.close()


@pytest.mark.parametrize(
    "mode",
    ("refresh-malformed-epoch", "refresh-missing-epoch", "refresh-wrong-epoch"),
)
def test_refresh_malformed_epoch_discards_host(mode: str) -> None:
    client = _client(host_command=_host_command("--mode", mode))
    client.execute("get_hierarchy", {}, timeout_seconds=2)
    result = client.refresh_com(timeout_seconds=2)
    assert result.outcome == REFRESH_HOST_DISCARDED
    assert result.discarded_generation == 1
    assert client.state == "NEW"
    client.close()


def test_refresh_timeout_after_submit_discards_host() -> None:
    client = _client(host_command=_host_command("--mode", "refresh-hang"))
    client.execute("get_hierarchy", {}, timeout_seconds=2)
    result = client.refresh_com(timeout_seconds=0.2)
    assert result.outcome == REFRESH_HOST_DISCARDED
    assert result.discarded_generation == 1
    assert client.state == "NEW"
    client.close()


def test_refresh_unconfirmed_reap_stays_broken(monkeypatch) -> None:
    client = _client(host_command=_host_command("--mode", "refresh-hang"))
    client.execute("get_hierarchy", {}, timeout_seconds=2)
    monkeypatch.setattr(client, "_reap", lambda *, kill: False)
    result = client.refresh_com(timeout_seconds=0.2)
    assert result.outcome == REFRESH_HOST_DISCARD_UNCONFIRMED
    assert result.content_free_projection() == {"outcome": REFRESH_HOST_DISCARD_UNCONFIRMED}
    assert client.state == "BROKEN"
    assert client._process is not None
    with pytest.raises(ComClientError) as raised:
        client.execute("get_hierarchy", {}, timeout_seconds=1)
    assert raised.value.delivery_state == DELIVERY_NOT_SUBMITTED
    assert client.state == "BROKEN"
    monkeypatch.undo()
    client.close()
    assert client.state == "CLOSED"


def test_refresh_close_before_publish_is_rejected_closed() -> None:
    client = _client()
    client.execute("get_hierarchy", {}, timeout_seconds=2)
    client._admission_hook = client.close
    result = client.refresh_com(timeout_seconds=2)
    assert result.outcome == REFRESH_REJECTED_CLOSED
    assert client.state == "CLOSED"


def test_refresh_host_loss_before_publish_is_not_attempted() -> None:
    client = _client()
    client.execute("get_hierarchy", {}, timeout_seconds=2)

    def kill_idle_host() -> None:
        process = client._process
        assert process is not None
        process.kill()
        deadline = time.time() + 2
        while client.state == "READY" and time.time() < deadline:
            time.sleep(0.02)

    client._admission_hook = kill_idle_host
    result = client.refresh_com(timeout_seconds=2)
    assert result.outcome == REFRESH_NOT_ATTEMPTED
    assert result.reason == NOT_ATTEMPTED_HOST_TRANSITION
    assert client.state in {"NEW", "BROKEN"}
    client.close()


def test_refresh_dispatch_lock_timeout_does_not_poison() -> None:
    client = _client(host_command=_host_command("--mode", "hang"))
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            client.execute("get_hierarchy", {}, timeout_seconds=2)
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    deadline = time.time() + 2
    while client.state != "READY" and time.time() < deadline:
        time.sleep(0.02)
    result = client.refresh_com(timeout_seconds=0.2)
    assert result.outcome == REFRESH_NOT_ATTEMPTED
    assert result.reason == NOT_ATTEMPTED_DISPATCH_LOCK_TIMEOUT
    assert client.state == "READY"
    assert client.com_epoch == 1
    client.close()
    thread.join(2)
    assert errors


def test_refresh_closed_client_is_rejected() -> None:
    client = _client()
    client.close()
    result = client.refresh_com(timeout_seconds=1)
    assert result.outcome == REFRESH_REJECTED_CLOSED


def test_refresh_then_close_after_success_keeps_refreshed() -> None:
    client = _client()
    client.execute("get_hierarchy", {}, timeout_seconds=2)
    committed = threading.Event()

    def close_after_commit() -> None:
        committed.wait(timeout=2)
        client.close()

    original_finalize = client._finalize_refresh

    def finalize(pending, expected_epoch):
        result = original_finalize(pending, expected_epoch)
        committed.set()
        return result

    client._finalize_refresh = finalize  # type: ignore[method-assign]
    thread = threading.Thread(target=close_after_commit)
    thread.start()
    result = client.refresh_com(timeout_seconds=2)
    thread.join(2)
    assert result.outcome == REFRESH_REFRESHED
    assert result.com_epoch == 2
    assert client.state == "CLOSED"


def test_bridge_refresh_audit_is_content_free(tmp_path) -> None:
    audit = tmp_path / "audit.jsonl"
    client = _client()
    bridge = OneNoteBridge(timeout_seconds=5, audit_path=audit, client=client)
    bridge.call("get_hierarchy", start_id="secret-id", scope=2, schema=2)
    result = bridge.refresh_com_client()
    assert result.outcome == REFRESH_REFRESHED
    rows = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
    refresh_row = rows[-1]
    assert refresh_row["operation"] == "refresh_com"
    assert refresh_row["refresh_outcome"] == REFRESH_REFRESHED
    assert "ok" not in refresh_row
    assert refresh_row["generation"] == 1
    assert refresh_row["com_epoch"] == 2
    rendered = json.dumps(refresh_row)
    assert "secret-id" not in rendered
    assert "<one:Notebooks" not in rendered
    bridge.close()


def test_launch_handler_attaches_refresh_projection_after_ready(monkeypatch) -> None:
    from local_onenote_mcp.operation_catalog import _launch_onenote_gui

    launched = {"count": 0}
    refreshed = {"count": 0}

    def fake_launch():
        launched["count"] += 1
        return {
            "status": "already_running",
            "launch_attempted": False,
            "launch_attempts": 0,
            "ready": True,
        }

    class _Bridge:
        def refresh_com_client(self):
            refreshed["count"] += 1
            return ComRefreshResult(outcome=REFRESH_NOT_NEEDED)

    monkeypatch.setattr(
        "local_onenote_mcp.operation_catalog.launch_desktop_gui",
        fake_launch,
    )
    result = _launch_onenote_gui(
        SimpleNamespace(hierarchy=SimpleNamespace(bridge=_Bridge()))
    )
    assert launched["count"] == 1
    assert refreshed["count"] == 1
    assert result["status"] == "already_running"
    assert result["com_client_refresh"] == {"outcome": REFRESH_NOT_NEEDED}


def test_launch_handler_does_not_refresh_when_launch_fails(monkeypatch) -> None:
    from local_onenote_mcp.onenote_errors import OneNoteDesktopLaunchError
    from local_onenote_mcp.operation_catalog import _launch_onenote_gui

    refreshed = {"count": 0}

    def fake_launch():
        raise OneNoteDesktopLaunchError(
            "launch failed",
            operation="launch_onenote_gui",
        )

    class _Bridge:
        def refresh_com_client(self):
            refreshed["count"] += 1
            return ComRefreshResult(outcome=REFRESH_NOT_NEEDED)

    monkeypatch.setattr(
        "local_onenote_mcp.operation_catalog.launch_desktop_gui",
        fake_launch,
    )
    with pytest.raises(OneNoteDesktopLaunchError):
        _launch_onenote_gui(
            SimpleNamespace(hierarchy=SimpleNamespace(bridge=_Bridge()))
        )
    assert refreshed["count"] == 0


def test_reap_from_reader_thread_is_not_confirmed() -> None:
    client = PersistentPowerShellClient(host_command=_host_command())
    client._reader = threading.current_thread()
    assert client._reap(kill=False) is False
    client.close()


def test_protocol_failure_joins_reader_before_new() -> None:
    client = _client(host_command=_host_command("--mode", "noise"))
    captured: dict[str, object] = {}
    original = client._poison

    def poison(*, kill: bool) -> bool:
        captured["reader"] = client._reader
        confirmed = original(kill=kill)
        reader = captured["reader"]
        captured["confirmed"] = confirmed
        captured["reader_alive"] = reader.is_alive() if reader is not None else False
        return confirmed

    client._poison = poison  # type: ignore[method-assign]
    with pytest.raises(ComClientError) as raised:
        client.execute("get_hierarchy", {}, timeout_seconds=2)
    assert raised.value.delivery_state == DELIVERY_POSSIBLY_DISPATCHED
    assert captured["confirmed"] is True
    assert captured["reader_alive"] is False
    assert client.state == "NEW"
    assert client._reader is None
    client.close()


def test_refresh_failure_and_close_have_single_cleanup_owner() -> None:
    client = _client(host_command=_host_command("--mode", "refresh-hang"))
    client.execute("get_hierarchy", {}, timeout_seconds=2)
    overlap = {"count": 0, "max": 0}
    gate = threading.Event()
    resume = threading.Event()
    lock = threading.Lock()

    def hook() -> None:
        with lock:
            overlap["count"] += 1
            overlap["max"] = max(overlap["max"], overlap["count"])
        gate.set()
        resume.wait(timeout=2)
        with lock:
            overlap["count"] -= 1

    client._cleanup_hook = hook
    results: list[object] = []

    def do_refresh() -> None:
        results.append(client.refresh_com(timeout_seconds=0.3))

    refresh_thread = threading.Thread(target=do_refresh)
    refresh_thread.start()
    assert gate.wait(timeout=2)
    close_thread = threading.Thread(target=client.close)
    close_thread.start()
    time.sleep(0.05)
    resume.set()
    refresh_thread.join(2)
    close_thread.join(2)
    assert overlap["max"] == 1
    assert results
    assert getattr(results[0], "outcome", None) in {
        REFRESH_HOST_DISCARDED,
        REFRESH_HOST_DISCARD_UNCONFIRMED,
    }
    assert client.state in {"NEW", "CLOSED", "CLOSING"}
    if client.state != "CLOSED":
        client.close()
    assert client.state == "CLOSED"


def test_refresh_broken_commit_wins_linearization_against_later_close() -> None:
    client = _client(host_command=_host_command("--mode", "refresh-activation-failure"))
    client.execute("get_hierarchy", {}, timeout_seconds=2)
    submitted = threading.Event()
    resume = threading.Event()
    observed: dict[str, object] = {}

    def hook() -> None:
        observed["state"] = client.state
        observed["owner"] = client._cleanup_owner
        submitted.set()
        resume.wait(timeout=2)

    client._broken_submitted_hook = hook
    results: list[object] = []

    def do_refresh() -> None:
        results.append(client.refresh_com(timeout_seconds=2))

    refresh_thread = threading.Thread(target=do_refresh)
    refresh_thread.start()
    assert submitted.wait(timeout=2)
    assert observed["state"] == "BROKEN"
    assert observed["owner"] == "refresh"

    close_thread = threading.Thread(target=client.close)
    close_thread.start()
    time.sleep(0.05)
    assert client.state == "BROKEN"
    assert client._cleanup_owner == "refresh"
    resume.set()
    refresh_thread.join(2)
    close_thread.join(2)

    assert results
    result = results[0]
    assert getattr(result, "outcome", None) == REFRESH_HOST_DISCARDED
    assert getattr(result, "discarded_generation", None) == 1
    assert client.state == "CLOSED"


def test_refresh_close_after_publish_before_response_is_rejected_closed() -> None:
    client = _client(host_command=_host_command("--mode", "refresh-hang"))
    client.execute("get_hierarchy", {}, timeout_seconds=2)
    results: list[object] = []

    def do_refresh() -> None:
        results.append(client.refresh_com(timeout_seconds=2))

    thread = threading.Thread(target=do_refresh)
    thread.start()
    deadline = time.time() + 2
    while time.time() < deadline and client._pending is None:
        time.sleep(0.01)
    assert client._pending is not None
    client.close()
    thread.join(2)
    assert results
    assert getattr(results[0], "outcome", None) == REFRESH_REJECTED_CLOSED
    assert client.state == "CLOSED"


@pytest.mark.parametrize(
    "mode",
    ("refresh-activation-failure", "refresh-malformed-epoch"),
)
def test_refresh_close_before_failure_commit_is_rejected_closed(mode: str) -> None:
    client = _client(host_command=_host_command("--mode", mode))
    client.execute("get_hierarchy", {}, timeout_seconds=2)
    client._commit_refresh_hook = client.close
    result = client.refresh_com(timeout_seconds=2)
    assert result.outcome == REFRESH_REJECTED_CLOSED
    assert client.state == "CLOSED"


def test_unconfirmed_close_keeps_retry_path(monkeypatch) -> None:
    client = _client()
    client.execute("get_hierarchy", {}, timeout_seconds=2)
    monkeypatch.setattr(client, "_reap", lambda *, kill: False)
    client.close()
    assert client.state == "CLOSING"
    assert not client._closed.is_set()
    assert client._process is not None
    monkeypatch.undo()
    client.close()
    assert client.state == "CLOSED"
    assert client._closed.is_set()
    assert client._process is None


def test_refresh_then_business_request_uses_new_epoch() -> None:
    client = _client()
    first = client.execute("get_hierarchy", {}, timeout_seconds=2)
    assert first["data"]["com_epoch"] == 1
    result = client.refresh_com(timeout_seconds=2)
    assert result.outcome == REFRESH_REFRESHED
    assert result.com_epoch == 2
    second = client.execute("get_hierarchy", {}, timeout_seconds=2)
    assert second["data"]["com_epoch"] == 2
    assert client.generation == 1
    client.close()
