"""OneNote export, navigation, synchronization, and app operation service."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..bridge import OneNoteBridge
from ..constants import (
    FILING_LOCATION_TYPES,
    FILING_LOCATIONS,
    PUBLISH_FORMATS,
    SPECIAL_LOCATIONS,
)
from ..policy import MutationPolicy
from .base import BaseService
from .hierarchy import HierarchyService
from .mutations import MutationService
from .mutation_control import mutation_attempt_policy


class OperationsService(BaseService):
    def __init__(
        self,
        bridge: OneNoteBridge,
        hierarchy: HierarchyService,
        mutations: MutationService,
    ) -> None:
        super().__init__(bridge)
        self.hierarchy = hierarchy
        self.mutations = mutations

    def special_locations(self) -> dict[str, Any]:
        locations = {
            name: self.call("get_special_location", location=value)["path"]
            for name, value in SPECIAL_LOCATIONS.items()
        }
        return {"locations": locations}

    def hyperlink(self, object_id: str, page_content_object_id: str = "", web: bool = False) -> dict[str, Any]:
        item = self.hierarchy.resource(object_id)
        operation = "get_web_hyperlink" if web else "get_hyperlink"
        result = self.call(operation, object_id=object_id, page_content_object_id=page_content_object_id)
        return {"item": item, "hyperlink": result["hyperlink"]}

    def parent(self, object_id: str) -> dict[str, Any]:
        item = self.hierarchy.resource(object_id)
        parent_id = self.call("get_hierarchy_parent", object_id=object_id)["parent_id"]
        parent = self.hierarchy.resource(parent_id) if parent_id else None
        return {"item": item, "parent": parent, "parent_id": parent_id}

    def publish(self, object_id: str, target_path: str, format: str = "pdf", overwrite: bool = False) -> dict[str, Any]:
        output = Path(target_path).expanduser()
        if not output.is_absolute():
            output = Path.cwd() / output
        output = output.resolve(strict=False)
        if output.exists() and not overwrite:
            raise ValueError(f"Target already exists: {target_path}")
        output.parent.mkdir(parents=True, exist_ok=True)
        item = self.hierarchy.resource(object_id)
        if item["resource_type"] not in {"notebook", "section", "page"}:
            raise ValueError("publish_object supports notebook, section, or page IDs.")
        result = self.call(
            "publish",
            object_id=object_id,
            target_path=str(output),
            format=self.enum("format", format, PUBLISH_FORMATS),
        )
        return {"item": item, "path": result["path"], "format": format.casefold()}

    def navigate(self, object_id: str, page_content_object_id: str = "", new_window: bool = False) -> dict[str, Any]:
        item = self.hierarchy.resource(object_id)
        self.call(
            "navigate_to",
            object_id=object_id,
            page_content_object_id=page_content_object_id,
            new_window=new_window,
        )
        return {"item": item, "navigated": True}

    def navigate_url(self, url: str, new_window: bool = False) -> dict[str, Any]:
        self.call("navigate_to_url", url=url, new_window=new_window)
        return {"navigated": True}

    def sync_notebook(self, notebook_id: str) -> dict[str, Any]:
        item = self.hierarchy.resource(notebook_id, "notebook")
        self.call("sync_hierarchy", object_id=notebook_id)
        return {
            "item": item,
            "sync_requested": True,
            "accepted": True,
            "converged": False,
            "convergence": {
                "converged": False,
                "reason": "OneNote COM exposes request acceptance but no supported completion proof.",
            },
        }

    def close_notebook(
        self,
        notebook_id: str,
        expected_name: str,
        expected_modified: str | None = None,
    ) -> dict[str, Any]:
        MutationPolicy.current().require_write()
        item = self.mutations.confirm_resource(
            notebook_id,
            "notebook",
            expected_name=expected_name,
            expected_parent_id=None,
            expected_modified=expected_modified,
        )
        def observe():
            try:
                return self.hierarchy.resource(notebook_id, "notebook")
            except ValueError:
                return None

        postcondition = lambda value: value is None or value.get("is_open") is False
        reconciliation = self.mutations.mutation_attempts.execute(
            mutation_attempt_policy("close_notebook"),
            execute=lambda: self.call(
                "close_notebook", notebook_id=notebook_id, force=False
            ),
            observe=observe,
            is_pre_state=lambda value: value is not None and value.get("is_open") is not False,
            is_post_state=postcondition,
        )
        self.mutations._raise_failed_controlled_outcome(reconciliation)
        stable = self.mutations._converge(
            operation="close_notebook",
            observe=observe,
            accept=postcondition,
            project_identity=self.hierarchy._resource_identity,
            failure_message="Close was accepted, but the Notebook open state did not converge.",
        )
        return {
            "item": item,
            "closed": True,
            "final_state": stable.value,
            "convergence": stable.summary(),
            "reconciliation": reconciliation.summary(),
        }

    def merge_sections(self, source_section_id: str, destination_section_id: str) -> dict[str, Any]:
        policy = MutationPolicy.current()
        policy.require_raw_xml()
        policy.require_write()
        source = self.hierarchy.resource(source_section_id, "section")
        destination = self.hierarchy.resource(destination_section_id, "section")
        if source["id"] == destination["id"]:
            raise ValueError("source_section_id and destination_section_id must be different exact IDs.")
        self.call(
            "merge_sections",
            source_section_id=source["id"],
            destination_section_id=destination["id"],
        )
        return {
            "source_section_id": source["id"],
            "destination_section_id": destination["id"],
            "merged": True,
        }

    def set_filing_location(
        self,
        filing_location: str,
        filing_location_type: str,
        section_or_page_id: str,
    ) -> dict[str, Any]:
        MutationPolicy.current().require_write()
        item = self.hierarchy.resource(section_or_page_id)
        if item["resource_type"] not in {"section", "page"}:
            raise ValueError("section_or_page_id must identify an exact Section or Page ID.")
        self.call(
            "set_filing_location",
            filing_location=self.enum("filing_location", filing_location, FILING_LOCATIONS),
            filing_location_type=self.enum("filing_location_type", filing_location_type, FILING_LOCATION_TYPES),
            section_or_page_id=item["id"],
        )
        return {"object_id": item["id"], "updated": True}
