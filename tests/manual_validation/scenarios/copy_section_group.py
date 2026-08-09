"""Section Group Copy scenario."""

from __future__ import annotations

from .common.copy_runtime import execute_copy
from .copy_scenario_base import CopyScenario
from .common.registry import SCENARIO_REGISTRY


@SCENARIO_REGISTRY.register
class CopySectionGroupScenario(CopyScenario):
    name = "copy-section-group"
    help_text = (
        "GATED: create and copy the prepared Section Group; clean up by default or "
        "preserve the verified worksite for inspection."
    )

    execute_copy = staticmethod(execute_copy)


__all__ = ["CopySectionGroupScenario"]
