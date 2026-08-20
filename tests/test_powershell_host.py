"""Contract tests for shared PowerShell fragments and encoding."""

from __future__ import annotations

from local_onenote_mcp.com_client import MAX_COMMAND_LINE_CHARS
from local_onenote_mcp.powershell_host import (
    MAX_DECODED_FRAME_BYTES,
    MAX_ENCODED_FRAME_BYTES,
    POWERSHELL_FAKE_CLIENT_BOOTSTRAP,
    POWERSHELL_FAKE_PERSISTENT_HOST_SCRIPT,
    POWERSHELL_ONE_SHOT_SCRIPT,
    POWERSHELL_OPERATION_NAMES,
    POWERSHELL_OPERATION_SWITCH,
    POWERSHELL_PERSISTENT_HOST_SCRIPT,
    encode_powershell_command,
)
from local_onenote_mcp.services.backend_operation_classification import BRIDGE_OPERATIONS


def test_switch_covers_bridge_operations() -> None:
    assert POWERSHELL_OPERATION_NAMES == BRIDGE_OPERATIONS
    for name in sorted(BRIDGE_OPERATIONS):
        assert f'"{name}"' in POWERSHELL_OPERATION_SWITCH


def test_one_shot_and_persistent_embed_the_same_switch() -> None:
    assert POWERSHELL_OPERATION_SWITCH in POWERSHELL_ONE_SHOT_SCRIPT
    assert POWERSHELL_OPERATION_SWITCH in POWERSHELL_PERSISTENT_HOST_SCRIPT
    assert POWERSHELL_OPERATION_SWITCH not in POWERSHELL_FAKE_PERSISTENT_HOST_SCRIPT
    assert "New-Object -ComObject OneNote.Application" in POWERSHELL_ONE_SHOT_SCRIPT
    assert "New-Object -ComObject OneNote.Application" in POWERSHELL_PERSISTENT_HOST_SCRIPT
    assert "New-Object -ComObject OneNote.Application" not in POWERSHELL_FAKE_PERSISTENT_HOST_SCRIPT


def test_production_host_has_no_test_switch() -> None:
    assert POWERSHELL_FAKE_CLIENT_BOOTSTRAP not in POWERSHELL_PERSISTENT_HOST_SCRIPT
    assert "Invoke-FakeBridgeOperation" not in POWERSHELL_PERSISTENT_HOST_SCRIPT
    assert "force_hresult" not in POWERSHELL_PERSISTENT_HOST_SCRIPT
    assert "force_oversize" not in POWERSHELL_PERSISTENT_HOST_SCRIPT


def test_scripts_do_not_interpolate_user_env_into_source() -> None:
    for script in (POWERSHELL_ONE_SHOT_SCRIPT, POWERSHELL_PERSISTENT_HOST_SCRIPT):
        assert "Invoke-Expression" not in script
        assert "$args" not in script


def test_encoded_command_is_utf16le_base64_and_fits_budget() -> None:
    script = "Write-Output '测'"
    encoded = encode_powershell_command(script)
    decoded = __import__("base64").b64decode(encoded)
    assert decoded.decode("utf-16le") == script
    assert decoded.decode("utf-8", errors="replace") != script
    command = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-Sta",
        "-EncodedCommand",
        encode_powershell_command(POWERSHELL_PERSISTENT_HOST_SCRIPT),
    ]
    assert sum(len(part) + 1 for part in command) < MAX_COMMAND_LINE_CHARS
    assert "[Console]::In.ReadLine" not in POWERSHELL_PERSISTENT_HOST_SCRIPT
    assert "ReadLine()" not in POWERSHELL_PERSISTENT_HOST_SCRIPT
    assert str(MAX_DECODED_FRAME_BYTES) in POWERSHELL_PERSISTENT_HOST_SCRIPT
    assert str(MAX_ENCODED_FRAME_BYTES) in POWERSHELL_PERSISTENT_HOST_SCRIPT
    for name in sorted(POWERSHELL_OPERATION_NAMES):
        assert name in POWERSHELL_PERSISTENT_HOST_SCRIPT
    assert "$script:Allowed='" in POWERSHELL_PERSISTENT_HOST_SCRIPT
    assert "Get-RequiredJsonInt" in POWERSHELL_PERSISTENT_HOST_SCRIPT
    assert "Get-RequiredJsonObject" in POWERSHELL_PERSISTENT_HOST_SCRIPT


def test_open_hierarchy_batch_stays_in_one_session() -> None:
    branch = POWERSHELL_OPERATION_SWITCH.split('"open_hierarchy_batch" {', 1)[1].split(
        '"update_hierarchy" {', 1
    )[0]
    assert "foreach ($entry in @($p.requests))" in branch
    assert "$openedByKey[$key] = $objectId" in branch
    assert branch.count("$onenote.GetHierarchy(") == 1
    assert "New-Object -ComObject OneNote.Application" not in branch
