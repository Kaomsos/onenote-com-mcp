"""Typed OneNote mutation service with policy, confirmation, and read-back."""

from __future__ import annotations

import base64
from pathlib import Path
import re
import time
from typing import Any

from ..bridge import OneNoteBridge
from ..constants import CREATE_FILE_TYPES, NEW_PAGE_STYLES, SPECIAL_LOCATIONS, XML_SCHEMA_2013
from ..hierarchy import display_name
from ..page import (
    DELETABLE_PAGE_OBJECT_TYPES,
    build_image_page_update_xml,
    build_page_update_xml,
    collect_page_objects,
    proportional_dimensions,
)
from ..policy import MutationPolicy
from .base import BaseService
from .errors import PartialFailure
from .hierarchy import HierarchyService
from .pages import PageService


REPLACE_BODY_OBJECT_TYPES = {"Outline", "Image", "InkDrawing", "FileAttachment", "InsertedFile", "MediaFile"}


class MutationService(BaseService):
    def __init__(self, bridge: OneNoteBridge, hierarchy: HierarchyService, pages: PageService) -> None:
        super().__init__(bridge)
        self.hierarchy = hierarchy
        self.pages = pages

    @staticmethod
    def safe_leaf_name(name: str) -> str:
        cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", name).strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        if not cleaned:
            raise ValueError("Name cannot be empty.")
        return cleaned

    @staticmethod
    def create_resource_type(create_type: str) -> str | None:
        key = create_type.casefold()
        if key == "section":
            return "section"
        if key in {"folder", "section_group"}:
            return "section_group"
        if key == "notebook":
            return "notebook"
        return None

    def confirm_resource(
        self,
        object_id: str,
        resource_type: str,
        *,
        expected_name: str,
        expected_parent_id: str | None,
        expected_modified: str | None = None,
    ) -> dict[str, Any]:
        item = self.hierarchy.resource(object_id, resource_type)
        actual_name = display_name(item)
        if actual_name != expected_name:
            raise ValueError(f"Confirmation mismatch: expected name '{expected_name}', found '{actual_name}'.")
        if item["parent_id"] != expected_parent_id:
            raise ValueError(
                f"Confirmation mismatch: expected parent_id '{expected_parent_id}', found '{item['parent_id']}'."
            )
        if expected_modified is not None and item.get("modified") != expected_modified:
            raise ValueError(
                f"Confirmation mismatch: expected modified '{expected_modified}', found '{item.get('modified')}'."
            )
        return item

    def open_hierarchy(self, path: str, relative_to_identifier: str = "", create_type: str = "none") -> dict[str, Any]:
        normalized_create_type = create_type.strip().casefold() or "none"
        relative_to_id = ""
        expected_path = path.replace("\\", "/").strip("/")
        if relative_to_identifier:
            parent = self.hierarchy.resolve(relative_to_identifier)
            relative_to_id = parent["id"]
            expected_path = self.hierarchy.friendly_child_path(parent["path"], path)
        if normalized_create_type == "none":
            existing = self.hierarchy.find_path(expected_path)
            if existing:
                return {"object_id": existing["id"], "item": existing, "opened_existing": True}
            if not relative_to_identifier:
                try:
                    existing = self.hierarchy.resolve(path)
                    return {"object_id": existing["id"], "item": existing, "opened_existing": True}
                except Exception:
                    pass
        MutationPolicy.current().require_write()
        result = self.call(
            "open_hierarchy",
            path=path,
            relative_to_id=relative_to_id,
            create_file_type=self.enum("create_type", normalized_create_type, CREATE_FILE_TYPES),
        )
        resource_type = self.create_resource_type(normalized_create_type)
        item = (
            self.hierarchy.wait_for_created(expected_path, resource_type, result["object_id"])
            if resource_type
            else None
        )
        data: dict[str, Any] = {
            "object_id": item["id"] if item else result["object_id"],
            "opened_existing": False,
        }
        if item:
            data["item"] = item
        return data

    def create_notebook(self, name_or_path: str, base_folder: str = "") -> dict[str, Any]:
        MutationPolicy.current().require_write()
        raw = Path(name_or_path)
        if raw.is_absolute():
            notebook_path = raw
        else:
            root = (
                Path(base_folder)
                if base_folder
                else Path(self.call("get_special_location", location=SPECIAL_LOCATIONS["default_notebook_folder"])["path"])
            )
            notebook_path = root / self.safe_leaf_name(name_or_path)
        result = self.call(
            "open_hierarchy",
            path=str(notebook_path),
            relative_to_id="",
            create_file_type=CREATE_FILE_TYPES["notebook"],
        )
        notebook = self.hierarchy.wait_for_created(notebook_path.name, "notebook", result["object_id"])
        if notebook is None:
            raise RuntimeError("Notebook creation returned success, but the new notebook could not be verified.")
        return {"path": str(notebook_path), "notebook_id": result["object_id"], "item": notebook}

    def create_section(self, parent_id: str, section_name: str) -> dict[str, Any]:
        MutationPolicy.current().require_write()
        parent = self.hierarchy.resource(parent_id)
        if parent["resource_type"] not in {"notebook", "section_group"}:
            raise ValueError("parent_id must identify a notebook or section_group.")
        filename = self.safe_leaf_name(section_name)
        if not filename.lower().endswith(".one"):
            filename += ".one"
        result = self.call(
            "open_hierarchy",
            path=filename,
            relative_to_id=parent["id"],
            create_file_type=CREATE_FILE_TYPES["section"],
        )
        expected_path = self.hierarchy.friendly_child_path(parent["path"], filename)
        section = self.hierarchy.wait_for_created(expected_path, "section", result["object_id"])
        if section is None:
            raise RuntimeError("Section creation returned success, but the new section could not be verified.")
        return {
            "parent": parent,
            "section": section,
            "section_id": section["id"],
            "name": section_name,
            "path": expected_path,
        }

    def create_section_group(self, parent_id: str, group_name: str) -> dict[str, Any]:
        MutationPolicy.current().require_write()
        parent = self.hierarchy.resource(parent_id)
        if parent["resource_type"] not in {"notebook", "section_group"}:
            raise ValueError("parent_id must identify a notebook or section_group.")
        result = self.call(
            "open_hierarchy",
            path=self.safe_leaf_name(group_name),
            relative_to_id=parent["id"],
            create_file_type=CREATE_FILE_TYPES["section_group"],
        )
        expected_path = self.hierarchy.friendly_child_path(parent["path"], group_name)
        group = self.hierarchy.wait_for_created(expected_path, "section_group", result["object_id"])
        if group is None:
            raise RuntimeError("Section-group creation returned success, but the new group could not be verified.")
        return {
            "parent": parent,
            "section_group": group,
            "section_group_id": group["id"],
            "name": group_name,
            "path": expected_path,
        }

    def create_page(
        self,
        section_id: str,
        title: str,
        content: str = "",
        content_format: str = "plain",
        new_page_style: str = "blank_with_title",
    ) -> dict[str, Any]:
        MutationPolicy.current().require_write()
        section = self.hierarchy.resource(section_id, "section")
        page_id = self.call(
            "create_new_page",
            section_id=section["id"],
            new_page_style=self.enum("new_page_style", new_page_style, NEW_PAGE_STYLES),
        )["page_id"]
        xml = build_page_update_xml(page_id, title=title, content=content, content_format=content_format)
        self.call("update_page_content", xml=xml, schema=XML_SCHEMA_2013, force=False)
        expected_path = self.hierarchy.friendly_child_path(section["path"], title)
        page = self.hierarchy.wait_for_created(expected_path, "page", page_id)
        if page is None:
            raise RuntimeError("Page creation returned success, but the new page could not be verified.")
        return {"page_id": page["id"], "page": page, "section": section, "title": title, "path": expected_path}

    def update_page_title(
        self,
        page_id: str,
        title: str,
        expected_title: str,
        expected_section_id: str,
        expected_modified: str | None = None,
    ) -> dict[str, Any]:
        MutationPolicy.current().require_write()
        self.pages.confirm(
            page_id,
            expected_title=expected_title,
            expected_section_id=expected_section_id,
            expected_modified=expected_modified,
        )
        self.call(
            "update_page_content",
            xml=build_page_update_xml(page_id, title=title),
            schema=XML_SCHEMA_2013,
            force=False,
        )
        item = self.hierarchy.wait_for(page_id, "page", predicate=lambda value: value["title"] == title)
        if item is None:
            raise RuntimeError("Update returned success, but the page title could not be verified.")
        return {"item": item}

    def rename_resource(
        self,
        object_id: str,
        resource_type: str,
        new_name: str,
        expected_name: str,
        expected_parent_id: str,
        expected_modified: str | None,
    ) -> dict[str, Any]:
        MutationPolicy.current().require_write()
        item = self.confirm_resource(
            object_id,
            resource_type,
            expected_name=expected_name,
            expected_parent_id=expected_parent_id,
            expected_modified=expected_modified,
        )
        normalized_name = self.safe_leaf_name(new_name)
        self.call(
            "update_hierarchy",
            xml=self.hierarchy.update_xml(item, name=normalized_name),
            schema=XML_SCHEMA_2013,
        )
        refreshed = self.hierarchy.wait_for(
            object_id,
            resource_type,
            predicate=lambda value: value["name"] == normalized_name and value["parent_id"] == expected_parent_id,
        )
        if refreshed is None:
            raise RuntimeError("Rename returned success, but the new name could not be verified by ID.")
        return {"item": refreshed, "previous_name": expected_name}

    def reorder_page(
        self,
        page_id: str,
        expected_title: str,
        expected_section_id: str,
        after_page_id: str = "",
        page_level: int = 0,
        expected_modified: str | None = None,
    ) -> dict[str, Any]:
        MutationPolicy.current().require_write()
        page = self.pages.confirm(
            page_id,
            expected_title=expected_title,
            expected_section_id=expected_section_id,
            expected_modified=expected_modified,
        )
        section = self.hierarchy.resource(expected_section_id, "section")
        pages = [
            item
            for item in self.hierarchy.resources(include_recycle_bin=False)
            if item["resource_type"] == "page" and item["section_id"] == expected_section_id
        ]
        pages.sort(key=lambda item: item["order"])
        pages = [item for item in pages if item["id"] != page_id]
        if after_page_id:
            if after_page_id == page_id:
                raise ValueError("after_page_id cannot equal page_id.")
            indexes = [index for index, item in enumerate(pages) if item["id"] == after_page_id]
            if not indexes:
                raise ValueError("after_page_id must identify another page in the same section.")
            insertion_index = indexes[0] + 1
        else:
            insertion_index = 0
        target_level = page_level or page["page_level"]
        if target_level < 1:
            raise ValueError("page_level must be zero (preserve) or at least 1.")
        if insertion_index == 0 and target_level != 1:
            raise ValueError("The first page in a section must have page_level=1.")
        if insertion_index > 0 and target_level > pages[insertion_index - 1]["page_level"] + 1:
            raise ValueError("page_level cannot jump by more than one level from the preceding page.")
        pages.insert(insertion_index, {**page, "page_level": target_level})
        self.call("update_hierarchy", xml=self.hierarchy.page_order_xml(section, pages), schema=XML_SCHEMA_2013)
        refreshed_pages = [
            item
            for item in self.hierarchy.resources(include_recycle_bin=False)
            if item["resource_type"] == "page" and item["section_id"] == expected_section_id
        ]
        refreshed_pages.sort(key=lambda item: item["order"])
        refreshed = next((item for item in refreshed_pages if item["id"] == page_id), None)
        if refreshed is None or refreshed["order"] != insertion_index or refreshed["page_level"] != target_level:
            raise RuntimeError("Reorder returned success, but order/page_level read-back verification failed.")
        return {"item": refreshed, "pages": refreshed_pages}

    def move_section(
        self,
        section_id: str,
        destination_parent_id: str,
        expected_name: str,
        expected_parent_id: str,
        expected_modified: str | None = None,
    ) -> dict[str, Any]:
        MutationPolicy.current().require_experimental_move()
        section = self.confirm_resource(
            section_id,
            "section",
            expected_name=expected_name,
            expected_parent_id=expected_parent_id,
            expected_modified=expected_modified,
        )
        destination = self.hierarchy.resource(destination_parent_id)
        if destination["resource_type"] not in {"notebook", "section_group"}:
            raise ValueError("destination_parent_id must identify a notebook or section_group.")
        destination_notebook_id = (
            destination["id"] if destination["resource_type"] == "notebook" else destination["notebook_id"]
        )
        if destination_notebook_id != section["notebook_id"]:
            raise ValueError("move_section only supports destinations in the same notebook.")
        before_pages = [
            item
            for item in self.hierarchy.resources(include_recycle_bin=False)
            if item["resource_type"] == "page" and item["section_id"] == section_id
        ]
        before_pages.sort(key=lambda item: item["order"])
        before_hashes = {item["id"]: self.pages.digest(self.pages.xml(item["id"], "all")) for item in before_pages}
        self.call("update_hierarchy", xml=self.hierarchy.section_move_xml(section, destination), schema=XML_SCHEMA_2013)
        moved = self.hierarchy.wait_for(
            section_id,
            "section",
            predicate=lambda value: value["parent_id"] == destination_parent_id,
        )
        if moved is None:
            raise RuntimeError("Move returned success, but the Section parent could not be verified.")
        after_pages = [
            item
            for item in self.hierarchy.resources(include_recycle_bin=False)
            if item["resource_type"] == "page" and item["section_id"] == section_id
        ]
        after_pages.sort(key=lambda item: item["order"])
        if [item["id"] for item in after_pages] != [item["id"] for item in before_pages]:
            raise RuntimeError("Section moved, but Page identity/order verification failed.")
        after_hashes = {item["id"]: self.pages.digest(self.pages.xml(item["id"], "all")) for item in after_pages}
        if after_hashes != before_hashes:
            raise RuntimeError("Section moved, but Page content verification failed.")
        return {
            "item": moved,
            "verified": {
                "section_id_preserved": True,
                "page_ids_and_order_preserved": True,
                "page_content_preserved": True,
            },
            "warnings": ["Experimental COM behavior: keep this tool disabled until the documented isolated test passes."],
        }

    def append_to_page(
        self,
        page_id: str,
        content: str,
        expected_title: str,
        expected_section_id: str,
        expected_modified: str | None = None,
        content_format: str = "plain",
        x: float | None = None,
        y: float | None = None,
    ) -> dict[str, Any]:
        MutationPolicy.current().require_write()
        before = self.pages.confirm(
            page_id,
            expected_title=expected_title,
            expected_section_id=expected_section_id,
            expected_modified=expected_modified,
        )
        before_hash = self.pages.digest(self.pages.xml(page_id, "all"))
        xml = build_page_update_xml(page_id, content=content, content_format=content_format, x=x, y=y)
        self.call("update_page_content", xml=xml, schema=XML_SCHEMA_2013, force=False)
        if self.pages.digest(self.pages.xml(page_id, "all")) == before_hash:
            raise RuntimeError("Append returned success, but Page content did not change during read-back verification.")
        return {"item": self.hierarchy.wait_for(page_id, "page"), "before_modified": before.get("modified"), "appended": True}

    def add_image_to_page(
        self,
        page_id: str,
        image_path: str,
        expected_title: str,
        expected_section_id: str,
        expected_modified: str | None = None,
        image_format: str = "",
        x: float = 36.0,
        y: float = 120.0,
        width: float | None = None,
        height: float | None = None,
    ) -> dict[str, Any]:
        MutationPolicy.current().require_write()
        self.pages.confirm(
            page_id,
            expected_title=expected_title,
            expected_section_id=expected_section_id,
            expected_modified=expected_modified,
        )
        before_hash = self.pages.digest(self.pages.xml(page_id, "all"))
        path = Path(image_path)
        if not path.is_file():
            raise ValueError(f"Image file not found: {image_path}")
        fmt = image_format or path.suffix.lstrip(".")
        if not fmt:
            raise ValueError("image_format is required when image_path has no extension.")
        resolved_width, resolved_height = proportional_dimensions(path, width, height)
        xml = build_image_page_update_xml(
            page_id,
            image_base64=base64.b64encode(path.read_bytes()).decode("ascii"),
            image_format=fmt,
            x=x,
            y=y,
            width=resolved_width,
            height=resolved_height,
        )
        self.call("update_page_content", xml=xml, schema=XML_SCHEMA_2013, force=False)
        if self.pages.digest(self.pages.xml(page_id, "all")) == before_hash:
            raise RuntimeError("Image update returned success, but Page content did not change during read-back verification.")
        return {
            "item": self.hierarchy.wait_for(page_id, "page"),
            "image_path": str(path),
            "width": resolved_width,
            "height": resolved_height,
        }

    def replace_page_body(
        self,
        page_id: str,
        content: str,
        expected_title: str,
        expected_section_id: str,
        expected_modified: str | None = None,
        title: str | None = None,
        content_format: str = "plain",
    ) -> dict[str, Any]:
        policy = MutationPolicy.current()
        policy.require_write()
        policy.require_delete()
        self.pages.confirm(
            page_id,
            expected_title=expected_title,
            expected_section_id=expected_section_id,
            expected_modified=expected_modified,
        )
        page_xml = self.pages.xml(page_id, "all")
        before_hash = self.pages.digest(page_xml)
        deleted: list[str] = []
        try:
            for obj in collect_page_objects(page_xml):
                if obj.get("type") not in REPLACE_BODY_OBJECT_TYPES:
                    continue
                object_id = obj.get("object_id")
                if not object_id:
                    continue
                self.call("delete_page_content", page_id=page_id, object_id=object_id, force=False)
                deleted.append(object_id)
            self.call(
                "update_page_content",
                xml=build_page_update_xml(page_id, title=title, content=content, content_format=content_format),
                schema=XML_SCHEMA_2013,
                force=False,
            )
            if self.pages.digest(self.pages.xml(page_id, "all")) == before_hash:
                raise RuntimeError("Rebuild returned success, but Page content did not change during read-back verification.")
        except Exception as exc:
            if deleted:
                raise PartialFailure(
                    str(exc),
                    partial=True,
                    completed_steps=[{"operation": "delete_page_content", "object_id": value} for value in deleted],
                ) from exc
            raise
        return {
            "item": self.hierarchy.wait_for(page_id, "page"),
            "deleted_objects": deleted,
            "replaced": True,
            "partial": False,
        }

    def delete_page_content(
        self,
        page_id: str,
        object_id: str,
        expected_title: str,
        expected_section_id: str,
        expected_modified: str | None = None,
    ) -> dict[str, Any]:
        MutationPolicy.current().require_delete()
        self.pages.confirm(
            page_id,
            expected_title=expected_title,
            expected_section_id=expected_section_id,
            expected_modified=expected_modified,
        )
        objects = collect_page_objects(self.pages.xml(page_id, "all"))
        matched = next((obj for obj in objects if obj.get("object_id") == object_id), None)
        if matched and not matched.get("delete_supported"):
            suggested_id = matched.get("delete_object_id")
            if suggested_id:
                raise ValueError(
                    f"Object '{object_id}' is a {matched.get('type')} child and is not directly deletable by OneNote COM. "
                    f"Delete its parent content object '{suggested_id}' instead."
                )
            allowed = ", ".join(sorted(DELETABLE_PAGE_OBJECT_TYPES))
            raise ValueError(
                f"Object '{object_id}' is a {matched.get('type')} child and is not directly deletable by OneNote COM. "
                f"Deletable object types: {allowed}."
            )
        if matched is None or not matched.get("delete_supported"):
            raise ValueError("object_id is not a currently verified deletable page content object.")
        self.call("delete_page_content", page_id=page_id, object_id=object_id, force=False)
        remaining = collect_page_objects(self.pages.xml(page_id, "all"))
        if any(item.get("object_id") == object_id for item in remaining):
            raise RuntimeError("Delete returned success, but the page content object still exists.")
        return {"page_id": page_id, "object_id": object_id, "deleted": True}

    def delete_resource(
        self,
        object_id: str,
        resource_type: str,
        expected_name: str,
        expected_parent_id: str,
        expected_modified: str | None,
        permanently: bool,
    ) -> dict[str, Any]:
        MutationPolicy.current().require_delete(permanently=permanently)
        item = self.confirm_resource(
            object_id,
            resource_type,
            expected_name=expected_name,
            expected_parent_id=expected_parent_id,
            expected_modified=expected_modified,
        )
        self.call("delete_hierarchy", object_id=object_id, permanently=permanently)
        final_state: dict[str, Any] | None = None
        for attempt in range(8):
            try:
                final_state = self.hierarchy.resource(object_id, resource_type)
            except ValueError:
                final_state = None
            if final_state is None or (not permanently and final_state["is_in_recycle_bin"]):
                return {
                    "item": item,
                    "object_id": object_id,
                    "permanently": permanently,
                    "deleted": True,
                    "final_state": final_state,
                }
            if attempt < 7:
                time.sleep(0.5)
        raise RuntimeError("Delete returned success, but the object remained active after read-back verification.")

    def delete_page(
        self,
        page_id: str,
        expected_title: str,
        expected_section_id: str,
        expected_modified: str | None = None,
        permanently: bool = False,
    ) -> dict[str, Any]:
        page = self.pages.confirm(
            page_id,
            expected_title=expected_title,
            expected_section_id=expected_section_id,
            expected_modified=expected_modified,
        )
        return self.delete_resource(
            page_id, "page", page["title"], page["parent_id"], expected_modified, permanently
        )

    def delete_hierarchy(self, object_identifier: str, permanently: bool = False) -> dict[str, Any]:
        policy = MutationPolicy.current()
        policy.require_raw_xml()
        policy.require_delete(permanently=permanently)
        item = self.hierarchy.resolve(object_identifier)
        if item["resource_type"] == "notebook":
            raise ValueError("Notebook deletion is unsupported; close_notebook is not deletion.")
        deleted_ids = []
        for attempt in range(4):
            object_id = item["id"]
            self.call("delete_hierarchy", object_id=object_id, permanently=permanently)
            deleted_ids.append(object_id)
            time.sleep(0.5)
            remaining = self.hierarchy.find_path(item["path"], item["resource_type"])
            if not remaining:
                return {
                    "object_id": object_id,
                    "deleted_ids": deleted_ids,
                    "permanently": permanently,
                    "deleted": True,
                    "verified_gone": True,
                }
            item = remaining
            if attempt == 3:
                raise RuntimeError(f"Delete returned success, but '{item['path']}' still exists with ID {item['id']}.")
        raise RuntimeError("Delete did not complete.")

    def update_page_xml(self, xml: str) -> dict[str, Any]:
        policy = MutationPolicy.current()
        policy.require_raw_xml()
        policy.require_write()
        self.call("update_page_content", xml=xml, schema=XML_SCHEMA_2013, force=False)
        return {"updated": True}

    def update_hierarchy_xml(self, xml: str) -> dict[str, Any]:
        policy = MutationPolicy.current()
        policy.require_raw_xml()
        policy.require_write()
        self.call("update_hierarchy", xml=xml, schema=XML_SCHEMA_2013)
        return {"updated": True}
