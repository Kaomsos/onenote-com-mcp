"""Process-level server settings derived from the environment."""

from __future__ import annotations

import os


MCP_NAME = "local-onenote"
DEFAULT_TIMEOUT = int(os.environ.get("LOCAL_ONENOTE_MCP_TIMEOUT", "90"))
MAX_TEXT_CHARS = int(os.environ.get("LOCAL_ONENOTE_MCP_MAX_TEXT_CHARS", "60000"))

ADAPTER_PERSISTENT_POWERSHELL = "persistent_powershell"
ADAPTER_ONE_SHOT_POWERSHELL = "one_shot_powershell"
KNOWN_BRIDGE_ADAPTERS = frozenset(
    {ADAPTER_PERSISTENT_POWERSHELL, ADAPTER_ONE_SHOT_POWERSHELL}
)
DEFAULT_BRIDGE_ADAPTER = ADAPTER_PERSISTENT_POWERSHELL


def parse_bridge_adapter_name(raw: str | None = None) -> str:
    """Return a known adapter name or fail closed.

    This function only parses a name. It does not construct a COM client.
    """

    if raw is None:
        raw = os.environ.get("LOCAL_ONENOTE_BRIDGE_ADAPTER", DEFAULT_BRIDGE_ADAPTER)
    value = str(raw).strip()
    if value not in KNOWN_BRIDGE_ADAPTERS:
        raise ValueError(
            "LOCAL_ONENOTE_BRIDGE_ADAPTER must be one of "
            f"{sorted(KNOWN_BRIDGE_ADAPTERS)}; got {value!r}."
        )
    return value
