"""Notebook Copy scenario."""

from __future__ import annotations

from .common.copy_runtime import execute_copy
from .copy_scenario_base import CopyScenario
from .common.registry import SCENARIO_REGISTRY


@SCENARIO_REGISTRY.register
class CopyNotebookScenario(CopyScenario):
    name = "copy-notebook"
    help_text = (
        "GATED: create, copy and close the Notebook while preserving its folder, "
        "report, then close or keep."
    )

    execute_copy = staticmethod(execute_copy)


__all__ = ["CopyNotebookScenario"]
