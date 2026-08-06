"""Section Copy scenario."""

from __future__ import annotations

from .common.copy_runtime import execute_copy
from .copy_scenario_base import CopyScenario
from .common.registry import SCENARIO_REGISTRY


@SCENARIO_REGISTRY.register
class CopySectionScenario(CopyScenario):
    name = "copy-section"
    help_text = (
        "GATED: create, copy the prepared Section and clean up the target, "
        "report, then close or keep."
    )

    execute_copy = staticmethod(execute_copy)


__all__ = ["CopySectionScenario"]
