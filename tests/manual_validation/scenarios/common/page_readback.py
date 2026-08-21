"""Shared content-free assertions for Page path and default-title readback."""

from __future__ import annotations

from typing import Any, Mapping

from ...runtime import InvariantFailure


SPECIAL_PAGE_TITLE = "01-Readback / Page\\:  %~界"
SPECIAL_PAGE_TITLE_CASEFOLD = "01-readback / page\\:  %~界"
THREE_COLUMN_TABLE_HTML = (
    "<p><strong>Automatic Page readback fixture</strong> "
    "<em>rich text</em> "
    '<span style="color:#2F5597">with deterministic formatting</span>.</p>'
    "<table><tr><th>First</th><th>Second</th><th>Third</th></tr>"
    "<tr><td>Alpha</td><td>Beta</td><td>Gamma</td></tr></table>"
)
PURE_RICH_TEXT_HTML = (
    "<p><strong>Automatic pure RichText Move fixture</strong> "
    "<em>without Table or binary content</em>.</p>"
)


def assert_default_page_title_readback(
    report: Mapping[str, Any],
    *,
    source_page_id: str,
) -> dict[str, Any]:
    """Require the semantic Copy chain to preserve an omitted Page title exactly."""

    matches = [
        value
        for value in report.get("page_results", ())
        if isinstance(value, Mapping)
        and str(value.get("source_page_id", "")) == source_page_id
    ]
    if len(matches) != 1:
        raise InvariantFailure(
            "Default-title Page readback did not expose one exact source Page result."
        )
    stages = matches[0].get("title_readback_stages")
    if not isinstance(stages, Mapping):
        raise InvariantFailure(
            "Default-title Page readback omitted title source/target stages."
        )
    source_to_transformed = stages.get("source_to_transformed")
    transformed_to_target = stages.get("transformed_to_target")
    if not isinstance(source_to_transformed, Mapping) or not isinstance(
        transformed_to_target, Mapping
    ):
        raise InvariantFailure(
            "Default-title Page readback omitted a title comparison stage."
        )
    source_checks = source_to_transformed.get("checks")
    target_checks = transformed_to_target.get("checks")
    passed = (
        report.get("verified") is True
        and report.get("lossless") is True
        and report.get("copy_contract_satisfied") is True
        and stages.get("title_override_requested") is False
        and source_to_transformed.get("passed") is True
        and transformed_to_target.get("passed") is True
        and isinstance(source_checks, Mapping)
        and source_checks.get("title") is True
        and isinstance(target_checks, Mapping)
        and target_checks.get("title") is True
    )
    if not passed:
        raise InvariantFailure(
            "Default-title Page readback did not preserve the title through "
            "source-to-transformed-to-target verification."
        )
    return {
        "source_page_id": source_page_id,
        "title_parameter": "omitted",
        "title_override_requested": False,
        "source_to_transformed_title": True,
        "transformed_to_target_title": True,
        "copy_contract_satisfied": True,
        "content_exposed": False,
    }


def assert_semantic_content_page_readback(
    report: Mapping[str, Any],
    *,
    source_page_ids: list[str],
) -> dict[str, Any]:
    """Require one successful semantic_content_v1 result per exact source Page."""

    page_results = [
        value
        for value in report.get("page_results", ())
        if isinstance(value, Mapping)
    ]
    by_source: dict[str, Mapping[str, Any]] = {}
    for value in page_results:
        source_id = str(value.get("source_page_id", ""))
        if not source_id or source_id in by_source:
            raise InvariantFailure(
                "Semantic Page readback contains a missing or duplicate source Page ID."
            )
        by_source[source_id] = value
    if set(by_source) != set(source_page_ids) or len(source_page_ids) != len(
        set(source_page_ids)
    ):
        raise InvariantFailure(
            "Semantic Page readback scope differs from the exact selected Page IDs."
        )
    width_comparisons: list[dict[str, Any]] = []
    for source_id in source_page_ids:
        value = by_source[source_id]
        equivalence = value.get("equivalence")
        semantic = (
            equivalence.get("semantic_content_comparison")
            if isinstance(equivalence, Mapping)
            else None
        )
        if not (
            value.get("lossless") is True
            and isinstance(equivalence, Mapping)
            and equivalence.get("verification_tier") == "semantic_content_v1"
            and equivalence.get("equivalent") is True
            and not equivalence.get("failed_content_object_types")
            and isinstance(semantic, Mapping)
            and semantic.get("source_complete") is True
            and semantic.get("target_complete") is True
            and semantic.get("passed") is True
        ):
            raise InvariantFailure(
                "Selected Page did not pass the complete semantic_content_v1 readback tier."
            )
        for comparison in semantic.get("table_column_width_comparisons", ()):
            if not isinstance(comparison, Mapping) or not (
                comparison.get("content_object_type") == "Table"
                and comparison.get("component_type") == "Column"
                and comparison.get("field") == "width"
                and comparison.get("comparison") == "relative_tolerance"
                and comparison.get("allowed_relative_delta") == 0.05
                and comparison.get("passed") is True
                and comparison.get("content_exposed") is False
            ):
                raise InvariantFailure(
                    "Semantic Page readback contains invalid Table width tolerance evidence."
                )
            width_comparisons.append(
                {
                    "source_page_id": source_id,
                    "table_ordinal": comparison.get("table_ordinal"),
                    "column_ordinal": comparison.get("column_ordinal"),
                    "allowed_relative_delta": comparison.get(
                        "allowed_relative_delta"
                    ),
                    "observed_relative_delta": comparison.get(
                        "observed_relative_delta"
                    ),
                    "passed": True,
                    "content_exposed": False,
                }
            )
    return {
        "source_page_ids": list(source_page_ids),
        "verification_tier": "semantic_content_v1",
        "all_equivalent": True,
        "semantic_projection_complete": True,
        "failed_content_object_types": [],
        "table_column_width_comparisons": width_comparisons,
        "content_exposed": False,
    }


__all__ = [
    "SPECIAL_PAGE_TITLE",
    "SPECIAL_PAGE_TITLE_CASEFOLD",
    "THREE_COLUMN_TABLE_HTML",
    "PURE_RICH_TEXT_HTML",
    "assert_default_page_title_readback",
    "assert_semantic_content_page_readback",
]
