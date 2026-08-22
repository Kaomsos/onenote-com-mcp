"""Windows-only PowerShell host integration without a real OneNote COM client."""

from __future__ import annotations

import shutil
import subprocess
import sys

import pytest

from local_onenote_mcp.com_client import (
    DELIVERY_POSSIBLY_DISPATCHED,
    REFRESH_REFRESHED,
    ComClientError,
    PersistentPowerShellClient,
    decode_protocol_frame,
    encode_protocol_frame,
)
from local_onenote_mcp.powershell_host import (
    POWERSHELL_FAKE_PERSISTENT_HOST_SCRIPT,
    assemble_persistent_host_script,
    encode_powershell_command,
)


pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell host only"),
    pytest.mark.skipif(
        shutil.which("powershell.exe") is None,
        reason="powershell.exe is required",
    ),
]


def test_encoded_sta_host_ready_framing_and_shutdown() -> None:
    client = PersistentPowerShellClient(
        host_script=POWERSHELL_FAKE_PERSISTENT_HOST_SCRIPT,
        close_wait_seconds=3,
    )
    first = client.execute(
        "get_hierarchy",
        {"start_id": "测", "scope": 2, "schema": 2},
        timeout_seconds=15,
    )
    second = client.execute(
        "create_new_page",
        {"section_id": "s", "new_page_style": 0},
        timeout_seconds=15,
    )
    assert first["ok"] is True
    assert "测" in str(first["data"]["xml"])
    assert second == {
        "ok": True,
        "data": {"page_id": "page-1", "com_epoch": 1},
        "error": None,
    }
    assert client.generation == 1
    client.close()
    assert client.state == "CLOSED"
    with pytest.raises(ComClientError) as raised:
        client.execute("get_hierarchy", {}, timeout_seconds=1)
    assert raised.value.delivery_state == "not_submitted"


def test_encoded_sta_host_com_error_response_keeps_host() -> None:
    client = PersistentPowerShellClient(
        host_script=POWERSHELL_FAKE_PERSISTENT_HOST_SCRIPT,
        close_wait_seconds=3,
    )
    failed = client.execute(
        "get_hierarchy",
        {"start_id": "", "scope": 2, "schema": 2, "force_hresult": -2147213299},
        timeout_seconds=15,
    )
    ok = client.execute(
        "get_hierarchy",
        {"start_id": "", "scope": 2, "schema": 2},
        timeout_seconds=15,
    )
    assert failed["ok"] is False
    assert ok["ok"] is True
    assert client.generation == 1
    client.close()


def test_encoded_sta_host_refresh_com_increments_epoch() -> None:
    client = PersistentPowerShellClient(
        host_script=POWERSHELL_FAKE_PERSISTENT_HOST_SCRIPT,
        close_wait_seconds=3,
    )
    first = client.execute(
        "get_hierarchy",
        {"start_id": "", "scope": 2, "schema": 2},
        timeout_seconds=15,
    )
    result = client.refresh_com(timeout_seconds=15)
    second = client.execute(
        "get_hierarchy",
        {"start_id": "", "scope": 2, "schema": 2},
        timeout_seconds=15,
    )
    assert first["ok"] is True
    assert first["data"]["com_epoch"] == 1
    assert result.outcome == REFRESH_REFRESHED
    assert result.generation == 1
    assert result.com_epoch == 2
    assert second["ok"] is True
    assert second["data"]["com_epoch"] == 2
    assert client.generation == 1
    client.close()


def _spawn_bounded_fake_host(*, max_decoded: int, max_encoded: int) -> subprocess.Popen[bytes]:
    script = assemble_persistent_host_script(
        fake_client=True,
        max_decoded_frame_bytes=max_decoded,
        max_encoded_frame_bytes=max_encoded,
    )
    return subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Sta",
            "-EncodedCommand",
            encode_powershell_command(script),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )


def _read_host_line(process: subprocess.Popen[bytes], *, max_encoded: int) -> bytes:
    assert process.stdout is not None
    line = process.stdout.readline()
    assert line
    raw = line.rstrip(b"\r\n")
    return raw


def test_encoded_sta_host_rejects_unknown_operation_before_dispatch() -> None:
    client = PersistentPowerShellClient(
        host_script=POWERSHELL_FAKE_PERSISTENT_HOST_SCRIPT,
        close_wait_seconds=3,
    )
    with pytest.raises(ComClientError) as raised:
        client.execute("not_a_bridge_operation", {}, timeout_seconds=15)
    assert raised.value.delivery_state == DELIVERY_POSSIBLY_DISPATCHED
    client.close()


def test_encoded_sta_host_rejects_generation_mismatch() -> None:
    process = _spawn_bounded_fake_host(max_decoded=4096, max_encoded=8192)
    try:
        ready = decode_protocol_frame(
            _read_host_line(process, max_encoded=8192),
            max_decoded=4096,
            max_encoded=8192,
        )
        assert ready["kind"] == "ready"
        assert process.stdin is not None
        process.stdin.write(
            encode_protocol_frame(
                {
                    "protocol_version": 1,
                    "generation": 1,
                    "sequence": 1,
                    "kind": "request",
                    "operation": "get_hierarchy",
                    "params": {"start_id": "", "scope": 2, "schema": 2},
                },
                max_decoded=4096,
                max_encoded=8192,
            )
        )
        process.stdin.flush()
        first = decode_protocol_frame(
            _read_host_line(process, max_encoded=8192),
            max_decoded=4096,
            max_encoded=8192,
        )
        assert first["kind"] == "response"
        process.stdin.write(
            encode_protocol_frame(
                {
                    "protocol_version": 1,
                    "generation": 2,
                    "sequence": 2,
                    "kind": "request",
                    "operation": "get_hierarchy",
                    "params": {"start_id": "", "scope": 2, "schema": 2},
                },
                max_decoded=4096,
                max_encoded=8192,
            )
        )
        process.stdin.flush()
        leftover = process.stdout.readline() if process.stdout is not None else b""
        process.stdin.close()
        process.wait(timeout=10)
        assert leftover == b""
        assert process.returncode != 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)


def test_encoded_sta_host_rejects_oversize_request() -> None:
    client = PersistentPowerShellClient(
        host_script=assemble_persistent_host_script(
            fake_client=True,
            max_decoded_frame_bytes=256,
            max_encoded_frame_bytes=512,
        ),
        max_decoded_frame_bytes=10_000,
        max_encoded_frame_bytes=20_000,
        close_wait_seconds=3,
    )
    with pytest.raises(ComClientError) as raised:
        client.execute("update_page_content", {"xml": "x" * 400}, timeout_seconds=15)
    assert raised.value.delivery_state == DELIVERY_POSSIBLY_DISPATCHED
    client.close()


def test_encoded_sta_host_rejects_oversize_response() -> None:
    client = PersistentPowerShellClient(
        host_script=assemble_persistent_host_script(
            fake_client=True,
            max_decoded_frame_bytes=200,
            max_encoded_frame_bytes=400,
        ),
        max_decoded_frame_bytes=10_000,
        max_encoded_frame_bytes=20_000,
        close_wait_seconds=3,
    )
    with pytest.raises(ComClientError) as raised:
        client.execute(
            "get_hierarchy",
            {"start_id": "", "scope": 2, "schema": 2, "force_oversize": 2000},
            timeout_seconds=15,
        )
    assert raised.value.delivery_state == DELIVERY_POSSIBLY_DISPATCHED
    client.close()
