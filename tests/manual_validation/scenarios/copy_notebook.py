"""Notebook Copy scenario."""

from __future__ import annotations

from .common.copy_runtime import execute_copy
from .copy_scenario_base import CopyScenario
from .common.registry import SCENARIO_REGISTRY


@SCENARIO_REGISTRY.register
class CopyNotebookScenario(CopyScenario):
    name = "copy-notebook"
    help_text = (
        "GATED: create and copy the Notebook; close the copy by default or preserve "
        "both open Notebooks as a verified worksite for inspection."
    )

    execute_copy = staticmethod(execute_copy)


__all__ = ["CopyNotebookScenario"]
