"""Process-level server settings derived from the environment."""

from __future__ import annotations

import os


MCP_NAME = "local-onenote"
DEFAULT_TIMEOUT = int(os.environ.get("LOCAL_ONENOTE_MCP_TIMEOUT", "90"))
MAX_TEXT_CHARS = int(os.environ.get("LOCAL_ONENOTE_MCP_MAX_TEXT_CHARS", "60000"))
