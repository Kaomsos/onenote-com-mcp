from __future__ import annotations

import json
from pathlib import Path
import tomllib

import pytest


ROOT = Path(__file__).resolve().parents[1]
SERVER_NAME = "local-onenote"
EXPECTED_COMMAND = "uv"
EXPECTED_ARGS = ["run", "--locked", "local-onenote-mcp"]
EXPECTED_ENV = {
    "LOCAL_ONENOTE_MCP_TIMEOUT": "90",
    "LOCAL_ONENOTE_MCP_MAX_TEXT_CHARS": "60000",
    "LOCAL_ONENOTE_ENABLE_WRITES": "false",
    "LOCAL_ONENOTE_ENABLE_DELETES": "false",
    "LOCAL_ONENOTE_ENABLE_ORGANIZE": "false",
    "LOCAL_ONENOTE_ENABLE_COPY": "false",
    "LOCAL_ONENOTE_ENABLE_LOCAL_FILE_IO": "false",
    "LOCAL_ONENOTE_ENABLE_UI_CONTROL": "false",
    "LOCAL_ONENOTE_ENABLE_NOTEBOOK_LIFECYCLE": "false",
}


@pytest.mark.parametrize("relative_path", [".mcp.json", ".cursor/mcp.json"])
def test_project_json_mcp_configs_are_consistent_and_fail_closed(
    relative_path: str,
) -> None:
    config = json.loads((ROOT / relative_path).read_text(encoding="utf-8"))
    server = config["mcpServers"][SERVER_NAME]

    assert server == {
        "command": EXPECTED_COMMAND,
        "args": EXPECTED_ARGS,
        "env": EXPECTED_ENV,
    }


@pytest.mark.parametrize(
    ("relative_path", "expected_enabled", "expected_env"),
    [
        (".codex/config.toml", False, EXPECTED_ENV),
        (
            ".grok/config.toml",
            True,
            {
                **EXPECTED_ENV,
                "LOCAL_ONENOTE_ENABLE_WRITES": "true",
                "LOCAL_ONENOTE_ENABLE_UI_CONTROL": "true",
            },
        ),
    ],
)
def test_project_toml_mcp_configs_have_reviewed_static_authorization(
    relative_path: str, expected_enabled: bool, expected_env: dict[str, str]
) -> None:
    with (ROOT / relative_path).open("rb") as config_file:
        config = tomllib.load(config_file)
    server = config["mcp_servers"][SERVER_NAME]

    assert server["enabled"] is expected_enabled
    assert server["command"] == EXPECTED_COMMAND
    assert server["args"] == EXPECTED_ARGS
    assert server["env"] == expected_env
    assert server["startup_timeout_sec"] == 120
    assert server["tool_timeout_sec"] == 120
