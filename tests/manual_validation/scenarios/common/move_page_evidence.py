"""Content-free evidence helpers shared by interactive Page Move scenarios."""

from __future__ import annotations

from typing import Any, Mapping


def partial_move_details(envelope: Mapping[str, Any] | None) -> dict[str, Any]:
    payload = dict(envelope or {})
    error = payload.get("error")
    if isinstance(error, Mapping):
        details = error.get("details")
        if isinstance(details, Mapping):
            return {"code": error.get("code"), **dict(details)}
    return payload


def move_target_id(result: Mapping[str, Any], source_id: str) -> str:
    destination = result.get("destination") or result.get("item") or {}
    if isinstance(destination, Mapping) and destination.get("id"):
        return str(destination["id"])
    report = result.get("copy_report")
    id_map = report.get("id_map") if isinstance(report, Mapping) else None
    if isinstance(id_map, Mapping) and id_map.get(source_id):
        return str(id_map[source_id])
    created = result.get("created_ids")
    if isinstance(created, list) and len(created) == 1 and created[0]:
        return str(created[0])
    return ""


def lossless_move_diagnostic(
    result: Mapping[str, Any],
    *,
    source_id: str,
    target_id: str,
    source_active: bool,
    target_active_in_destination: bool,
    follow_up_todo: str | None = "039_interactive_real_page_move_lossless_validation.md",
) -> dict[str, Any]:
    report = result.get("copy_report")
    report = report if isinstance(report, Mapping) else {}
    page_results: list[dict[str, Any]] = []
    for value in report.get("page_results", ()):
        if not isinstance(value, Mapping):
            continue
        equivalence = value.get("equivalence")
        equivalence = equivalence if isinstance(equivalence, Mapping) else {}
        semantic = equivalence.get("semantic_content_comparison")
        semantic_stages = value.get("semantic_content_stages")
        page_results.append(
            {
                "source_page_id": value.get("source_page_id"),
                "target_page_id": value.get("target_page_id"),
                "lossless": value.get("lossless"),
                "verification_tier": equivalence.get("verification_tier"),
                "acceptance_checks": list(equivalence.get("acceptance_checks", ())),
                "checks": dict(equivalence.get("checks", {})),
                "equivalent": equivalence.get("equivalent"),
                "semantic_projection": (
                    {
                        "source_complete": semantic.get("source_complete"),
                        "target_complete": semantic.get("target_complete"),
                        "checks": dict(semantic.get("checks", {})),
                        "passed": semantic.get("passed"),
                    }
                    if isinstance(semantic, Mapping)
                    else None
                ),
                "semantic_content_stages": (
                    dict(semantic_stages)
                    if isinstance(semantic_stages, Mapping)
                    else None
                ),
                "normalizations": dict(value.get("normalizations", {})),
            }
        )
    issues = [dict(value) for value in report.get("issues", ()) if isinstance(value, Mapping)]
    return {
        "schema_version": 1,
        "operation": "move_page",
        "outcome": result.get("outcome"),
        "failed_step": result.get("failed_step"),
        "source_page_id": source_id,
        "target_page_id": target_id or None,
        "source_active_after_failure": source_active,
        "target_active_in_destination": target_active_in_destination,
        "source_deleted": result.get("source_deleted"),
        "verified": report.get("verified"),
        "lossless": report.get("lossless"),
        "copy_contract_satisfied": report.get("copy_contract_satisfied"),
        "lossless_candidate": (
            report.get("planning", {}).get("lossless_candidate")
            if isinstance(report.get("planning"), Mapping)
            else None
        ),
        "content_capabilities": (
            list(report.get("planning", {}).get("content_capabilities", ()))
            if isinstance(report.get("planning"), Mapping)
            else []
        ),
        "issues": issues,
        "skipped_content": [
            dict(value)
            for value in report.get("skipped_content", ())
            if isinstance(value, Mapping)
        ],
        "page_results": page_results,
        "semantic_content_stages_available": any(
            value.get("semantic_content_stages") is not None for value in page_results
        ),
        "follow_up_todo": follow_up_todo,
        "content_exposed": False,
    }


__all__ = [
    "lossless_move_diagnostic",
    "move_target_id",
    "partial_move_details",
]
