"""Scenario-scoped fixture creation using an already-started MCP client."""

from __future__ import annotations

import argparse
from typing import Any
import uuid

from ...mcp_stdio_client import MCPStdioClient
from ...runtime import InvariantFailure, RuntimeOptions
from ...test_utils import (
    capture_snapshot,
    display_name,
    manifest_path,
    stable_item,
    write_json,
)
from .fixture_builders import (
    enforce_page_position,
    ensure_copy_list_tag_fixture,
    ensure_copy_rich_fixture,
    ensure_group,
    ensure_page,
    ensure_reparent_page_rich_fixture,
    ensure_section,
    new_manifest,
)
from .specs import ScenarioSpec


PAGE_COPY_SCENARIOS = {
    "copy-page",
    "copy-section",
    "copy-section-group",
    "copy-notebook",
    "move-page",
}

REORDER_PAGE_DESCRIPTION_TITLE = "00-Reorder-Description"
REORDER_PAGE_DESCRIPTION = """Reorder Page 人工验收说明

目标分区：01-Reorder-Page-Section

操作前（顺序 01,02,03）：
01-Parent：page_level=1
  02-Child：page_level=2，缩进在 01-Parent 下
03-Sibling：page_level=1

正向 Reorder：
把 03-Sibling 移到 01-Parent 后，并设为 page_level=2。

预期操作后（顺序 01,03,02）：
01-Parent：page_level=1
  03-Sibling：page_level=2，缩进在 01-Parent 下
  02-Child：page_level=2，仍缩进在 01-Parent 下

默认恢复后（顺序 01,02,03）：
01-Parent：page_level=1
  02-Child：page_level=2，缩进在 01-Parent 下
03-Sibling：page_level=1
"""

REORDER_SECTION_DESCRIPTION_TITLE = "00-Reorder-Section-Description"
REORDER_SECTION_DESCRIPTION = """Reorder Section 人工验收说明

本场景同时覆盖 Section 的两种合法父级。

场景一：父级为 Notebook
操作前：00-Description, 01-Root-Section-A, 02-Root-Section-B, 03-Root-Section-C
操作后：00-Description, 01-Root-Section-A, 03-Root-Section-C, 02-Root-Section-B
恢复后：00-Description, 01-Root-Section-A, 02-Root-Section-B, 03-Root-Section-C

场景二：父级为 01-Section-Parent（SectionGroup）
操作前：01-Group-Section-A, 02-Group-Section-B, 03-Group-Section-C
操作后：01-Group-Section-A, 03-Group-Section-C, 02-Group-Section-B
恢复后：01-Group-Section-A, 02-Group-Section-B, 03-Group-Section-C

两种情况都只改变同父级 Section 顺序，不改变 parent_id、Section ID 或 Page 后代。
"""

REORDER_SECTION_GROUP_DESCRIPTION_TITLE = "00-Reorder-SectionGroup-Description"
REORDER_SECTION_GROUP_DESCRIPTION = """Reorder SectionGroup 人工验收说明

本场景同时覆盖 SectionGroup 的两种合法父级。

场景一：父级为 Notebook
操作前：00-Group-Parent, 01-Root-Group-A, 02-Root-Group-B, 03-Root-Group-C
操作后：00-Group-Parent, 01-Root-Group-A, 03-Root-Group-C, 02-Root-Group-B
恢复后：00-Group-Parent, 01-Root-Group-A, 02-Root-Group-B, 03-Root-Group-C

场景二：父级为 00-Group-Parent（SectionGroup）
操作前：01-Nested-Group-A, 02-Nested-Group-B, 03-Nested-Group-C
操作后：01-Nested-Group-A, 03-Nested-Group-C, 02-Nested-Group-B
恢复后：01-Nested-Group-A, 02-Nested-Group-B, 03-Nested-Group-C

两种情况都只改变同父级 SectionGroup 顺序，不改变 parent_id、Group ID 或 Section/Page 后代。
"""

REPARENT_SECTION_DESCRIPTION_TITLE = "00-Reparent-Section-Description"
REPARENT_SECTION_DESCRIPTION = """Reparent Section 人工验收说明

本场景在同一个 disposable Notebook 中覆盖三种合法父级变化，并保持 Section ID 与 Page 后代。

场景一：Notebook 父级 → SectionGroup 父级
操作前：01-Notebook-To-Group-Section 位于 Notebook 根级
操作后：01-Notebook-To-Group-Section 位于 01-Destination-Group
默认恢复后：01-Notebook-To-Group-Section 回到 Notebook 根级

场景二：SectionGroup 父级 → Notebook 父级
操作前：02-Group-To-Notebook-Section 位于 02-Source-Group
操作后：02-Group-To-Notebook-Section 位于 Notebook 根级
默认恢复后：02-Group-To-Notebook-Section 回到 02-Source-Group

场景三：SectionGroup 父级 → SectionGroup 父级
操作前：03-Group-To-Group-Section 位于 03-Source-Group
操作后：03-Group-To-Group-Section 位于 03-Destination-Group
默认恢复后：03-Group-To-Group-Section 回到 03-Source-Group

三个目标 Section 各自包含同编号 Page。Reparent 前后必须保持 Section ID、Page ID、Page 顺序、缩进关系和正文不变。
"""

REPARENT_PAGE_DESCRIPTION_TITLE = "00-Reparent-Page-Description"
REPARENT_PAGE_DESCRIPTION = """Reparent Page 人工验收说明

本场景验证同一 Notebook 内使用原生 UpdateHierarchy 更换 Page 所属 Section。
OneNote 可以为 Reparent 后的 Page 及其内容对象重新分配 ID；场景会记录旧 ID → 新 ID，
但不会调用 Copy、DeleteHierarchy 或把旧 Page 显式移入回收站。

操作前：01-Source-Section/01-Reparent-Page
操作后：02-Destination-Section/01-Reparent-Page；02-Destination-Anchor 保持不变
默认恢复后：01-Source-Section/01-Reparent-Page

目标 Page 包含 Rich Text、Table、List、Tag 和 Image。验收允许目标 Page/内容对象 ID
一对一重映射，但要求标题、page_level、富内容语义摘要、可见文本、List/Tag 语义、
Image 二进制和内容对象类型不变；所有无关对象的 ID、关系和内容必须保持不变。
"""

REPARENT_SECTION_GROUP_DESCRIPTION_TITLE = "00-Reparent-SectionGroup-Description"
REPARENT_SECTION_GROUP_DESCRIPTION = """Reparent SectionGroup 人工验收说明

本场景探索同一个 disposable Notebook 内三种保持 Group ID 的父级变化。

场景一：Notebook 父级 → SectionGroup 父级
操作前：01-Notebook-To-Group-Target 位于 Notebook 根级
操作后：01-Notebook-To-Group-Target 位于 01-Destination-Parent
默认恢复后：01-Notebook-To-Group-Target 回到 Notebook 根级

场景二：SectionGroup 父级 → Notebook 父级
操作前：02-Group-To-Notebook-Target 位于 02-Source-Parent
操作后：02-Group-To-Notebook-Target 位于 Notebook 根级
默认恢复后：02-Group-To-Notebook-Target 回到 02-Source-Parent

场景三：SectionGroup 父级 → SectionGroup 父级
操作前：03-Group-To-Group-Target 位于 03-Source-Parent
操作后：03-Group-To-Group-Target 位于 03-Destination-Parent
默认恢复后：03-Group-To-Group-Target 回到 03-Source-Parent

三个目标 Group 各自包含同编号 Section 和 Page。前后必须保持全树 ID、后代关系、Page 内容 hash 和内容对象 ID 不变。
"""


async def _rich_page(
    client: MCPStdioClient,
    section: dict[str, Any],
    options: RuntimeOptions,
    token: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    page = await ensure_page(client, section["id"], "Rich-Page", f"Copy token: {token}")
    return await ensure_copy_rich_fixture(client, page, options.run_dir)


async def _layered_copy_pages(
    client: MCPStdioClient,
    section: dict[str, Any],
    options: RuntimeOptions,
    token: str,
    *,
    parent_title: str = "Rich-Page",
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Create the shared strict parent plus semantic List/Tag child fixture."""

    parent = await ensure_page(
        client,
        section["id"],
        parent_title,
        f"Copy token: {token}",
    )
    parent, copy_fixture = await ensure_copy_rich_fixture(
        client,
        parent,
        options.run_dir,
    )
    semantic_page = await ensure_page(
        client,
        section["id"],
        "List-Tag-Page",
        f"Semantic copy token: {token}",
    )
    parent = await enforce_page_position(client, section["id"], parent["id"], "", 1)
    semantic_page = await enforce_page_position(
        client,
        section["id"],
        semantic_page["id"],
        parent["id"],
        2,
    )
    semantic_page, semantic_fixture = await ensure_copy_list_tag_fixture(
        client,
        semantic_page,
    )
    copy_fixture["automated_content"] = [
        "rich_text",
        "table",
        "image",
        "list",
        "tag",
    ]
    copy_fixture["semantic_page"] = semantic_fixture
    return parent, semantic_page, copy_fixture


def _validate_fixture_snapshot(
    scenario: str,
    snapshot: dict[str, Any],
    structure: dict[str, dict[str, Any]],
    copy_fixture: dict[str, Any] | None,
) -> list[str]:
    """Prove the selected profile's identity, topology, and content invariants."""

    by_id = {
        str(item["id"]): item
        for item in snapshot.get("items", [])
        if isinstance(item, dict) and item.get("id")
    }
    resolved: dict[str, dict[str, Any]] = {}
    for key, declared in structure.items():
        item = by_id.get(str(declared.get("id", "")))
        if item is None or item.get("is_in_recycle_bin") is True:
            raise InvariantFailure(f"Fixture structure.{key} is missing from the active snapshot.")
        resolved[key] = item
    checks = ["all declared manifest keys resolve to active fresh IDs"]

    def require(condition: bool, message: str, check: str) -> None:
        if not condition:
            raise InvariantFailure(message)
        checks.append(check)

    if scenario in {"create", "reorder-page"}:
        parent = resolved["parent_page"]
        child = resolved["child_page"]
        sibling = resolved["sibling_page"]
        section_key = "content_section" if scenario == "create" else "reorder_section"
        section = resolved[section_key]
        require(
            parent.get("section_id") == section["id"]
            and child.get("section_id") == section["id"]
            and sibling.get("section_id") == section["id"],
            "Fixture Page tree is not contained by the declared source Section.",
            "Parent/Child/Sibling share the declared source Section",
        )
        require(
            int(parent.get("page_level", 0)) == 1
            and int(child.get("page_level", 0)) == 2
            and int(sibling.get("page_level", 0)) == 1
            and child.get("parent_page_id") == parent["id"]
            and sibling.get("parent_page_id") in {None, ""},
            "Fixture Parent/Child/Sibling Page topology is invalid.",
            "Page levels and derived parent relationships match the profile",
        )
        if scenario == "reorder-page":
            description_section = resolved["description_section"]
            description_page = resolved["description_page"]
            require(
                description_page.get("section_id") == description_section["id"],
                "Reorder Page Description Page escaped its Description Section.",
                "Description Page belongs to the Description Section",
            )
            expected_titles = {
                "description_page": "00-Reorder-Description",
                "parent_page": "01-Parent",
                "child_page": "02-Child",
                "sibling_page": "03-Sibling",
            }
            require(
                all(display_name(resolved[key]) == title for key, title in expected_titles.items()),
                "Reorder Page fixture Page titles do not have the required stable numbering.",
                "all scenario Pages use stable 00/01/02/03 title prefixes",
            )
    if scenario == "create":
        require(
            resolved["content_section"].get("parent_id") == resolved["group_a"]["id"],
            "Create fixture Content-Section is outside Group-A.",
            "Content-Section is a child of Group-A",
        )
        require(
            resolved["disposable_group"].get("parent_id")
            == resolved["delete_sandbox"]["id"]
            and resolved["disposable_section"].get("parent_id")
            == resolved["delete_sandbox"]["id"]
            and resolved["disposable_page"].get("section_id")
            == resolved["disposable_section"]["id"],
            "Create fixture disposable targets escaped Delete-Sandbox.",
            "disposable targets are descendants of Delete-Sandbox",
        )
    elif scenario == "rename":
        require(
            len(resolved) == 1,
            "Rename fixture must contain exactly one selected target.",
            "exactly one CLI-selected rename target key was created",
        )
        selected = next(iter(resolved.values()))
        require(
            display_name(selected) != "",
            "Rename fixture selected target has no stable display name.",
            "selected rename target has a stable display name",
        )
    elif scenario == "reorder-section":
        description_section = resolved["description_section"]
        description_page = resolved["description_page"]
        root_parent_id = resolved["root_section_a"].get("parent_id")
        root_parent = by_id.get(str(root_parent_id))
        parent_group = resolved["section_parent_group"]
        require(
            description_page.get("section_id") == description_section["id"]
            and description_section.get("parent_id") == root_parent_id,
            "Reorder Section Description escaped the fixture Notebook.",
            "Description Page and Section belong to the fixture Notebook",
        )
        require(
            root_parent is not None
            and root_parent.get("resource_type") == "notebook"
            and parent_group.get("resource_type") == "section_group"
            and parent_group.get("parent_id") == root_parent_id,
            "Section reorder fixture does not cover Notebook and SectionGroup parents.",
            "Section fixture covers both legal parent types: Notebook and SectionGroup",
        )
        expected_names = {
            "description_section": "00-Description",
            "description_page": REORDER_SECTION_DESCRIPTION_TITLE,
            "section_parent_group": "01-Section-Parent",
        }
        for prefix, label in (("root", "Root"), ("group", "Group")):
            for index, letter in enumerate("abc", start=1):
                upper = letter.upper()
                expected_names[f"{prefix}_section_{letter}"] = (
                    f"{index:02d}-{label}-Section-{upper}"
                )
                expected_names[f"{prefix}_page_{letter}"] = (
                    f"{index:02d}-{label}-Page-{upper}"
                )
        require(
            all(
                display_name(resolved[key]) == expected
                for key, expected in expected_names.items()
            ),
            "Section reorder fixture Sections/Pages do not have stable numbering.",
            "both Section sequences and their Pages use stable 01/02/03 numbering",
        )
        for prefix, parent_id in (
            ("root", root_parent_id),
            ("group", parent_group["id"]),
        ):
            section_keys = [f"{prefix}_section_{letter}" for letter in "abc"]
            page_keys = [f"{prefix}_page_{letter}" for letter in "abc"]
            sections = [resolved[key] for key in section_keys]
            require(
                all(section.get("parent_id") == parent_id for section in sections),
                "Section reorder fixture has a Section outside its declared parent.",
                f"{prefix} A/B/C Sections share one legal parent",
            )
            require(
                all(resolved[page_key].get("section_id") == section["id"] for page_key, section in zip(page_keys, sections)),
                "Section reorder fixture Page escaped its declared Section.",
                f"{prefix} A/B/C Sections each contain their declared Page",
            )
        direct_root = [
            item["id"] for item in snapshot["items"]
            if item.get("resource_type") == "section"
            and item.get("parent_id") == resolved["root_section_a"]["parent_id"]
        ]
        direct_group = [
            item["id"] for item in snapshot["items"]
            if item.get("resource_type") == "section"
            and item.get("parent_id") == resolved["section_parent_group"]["id"]
        ]
        expected_root = [description_section["id"]] + [
            resolved[f"root_section_{letter}"]["id"] for letter in "abc"
        ]
        expected_group = [resolved[f"group_section_{letter}"]["id"] for letter in "abc"]
        require(
            [object_id for object_id in direct_root if object_id in set(expected_root)] == expected_root
            and [object_id for object_id in direct_group if object_id in set(expected_group)] == expected_group,
            "Section reorder fixture is not in exact A/B/C order.",
            "both Section sibling sequences are exactly A/B/C",
        )
    elif scenario == "reorder-section-group":
        description_section = resolved["description_section"]
        description_page = resolved["description_page"]
        root_groups = [resolved[f"root_group_{letter}"] for letter in "abc"]
        nested_groups = [resolved[f"nested_group_{letter}"] for letter in "abc"]
        parent_group = resolved["section_group_parent"]
        notebook_id = root_groups[0]["parent_id"]
        notebook = by_id.get(str(notebook_id))
        require(
            notebook is not None
            and notebook.get("resource_type") == "notebook"
            and all(group.get("parent_id") == notebook_id for group in root_groups)
            and parent_group.get("parent_id") == notebook_id
            and parent_group.get("resource_type") == "section_group"
            and all(
                group.get("parent_id") == parent_group["id"]
                for group in nested_groups
            ),
            "SectionGroup reorder fixture does not cover Notebook and SectionGroup parents.",
            "SectionGroup fixture covers both legal parent types: Notebook and SectionGroup",
        )
        require(
            description_section.get("parent_id") == notebook_id
            and description_page.get("section_id") == description_section["id"],
            "Reorder SectionGroup Description escaped the fixture Notebook.",
            "Description Page and Section belong to the fixture Notebook",
        )
        expected_names = {
            "description_section": "00-Description",
            "description_page": REORDER_SECTION_GROUP_DESCRIPTION_TITLE,
            "section_group_parent": "00-Group-Parent",
        }
        for prefix, label in (("root", "Root"), ("nested", "Nested")):
            for index, letter in enumerate("abc", start=1):
                upper = letter.upper()
                expected_names[f"{prefix}_group_{letter}"] = (
                    f"{index:02d}-{label}-Group-{upper}"
                )
                expected_names[f"{prefix}_section_{letter}"] = (
                    f"{index:02d}-{label}-Section-{upper}"
                )
                expected_names[f"{prefix}_page_{letter}"] = (
                    f"{index:02d}-{label}-Page-{upper}"
                )
        require(
            all(
                display_name(resolved[key]) == expected
                for key, expected in expected_names.items()
            ),
            "SectionGroup reorder fixture Groups/Sections/Pages do not have stable numbering.",
            "both SectionGroup sequences and descendants use stable 01/02/03 numbering",
        )
        direct_root_groups = [
            item["id"] for item in snapshot["items"]
            if item.get("resource_type") == "section_group" and item.get("parent_id") == notebook_id
        ]
        direct_nested_groups = [
            item["id"] for item in snapshot["items"]
            if item.get("resource_type") == "section_group"
            and item.get("parent_id") == parent_group["id"]
        ]
        expected_root = [parent_group["id"]] + [group["id"] for group in root_groups]
        expected_nested = [group["id"] for group in nested_groups]
        require(
            [object_id for object_id in direct_root_groups if object_id in set(expected_root)]
            == expected_root
            and direct_nested_groups == expected_nested,
            "SectionGroup reorder fixture is not in exact A/B/C order for both parents.",
            "both SectionGroup sibling sequences are exactly A/B/C",
        )
        for prefix, groups in (("root", root_groups), ("nested", nested_groups)):
            for letter, group in zip("abc", groups):
                section = resolved[f"{prefix}_section_{letter}"]
                page = resolved[f"{prefix}_page_{letter}"]
                require(
                    section.get("parent_id") == group["id"]
                    and page.get("section_id") == section["id"],
                    "SectionGroup reorder fixture descendant escaped its declared Group.",
                    f"{prefix} Group {letter.upper()} contains its declared Section/Page descendants",
                )
    elif scenario == "reparent-section":
        description_section = resolved["description_section"]
        description_page = resolved["description_page"]
        notebook_id = str(description_section.get("parent_id", ""))
        notebook_to_group = resolved["notebook_to_group_section"]
        notebook_to_group_destination = resolved["notebook_to_group_destination"]
        group_to_notebook_source = resolved["group_to_notebook_source"]
        group_to_notebook = resolved["group_to_notebook_section"]
        group_to_group_source = resolved["group_to_group_source"]
        group_to_group_destination = resolved["group_to_group_destination"]
        group_to_group = resolved["group_to_group_section"]
        require(
            description_section.get("parent_id") == notebook_id
            and description_page.get("section_id") == description_section["id"],
            "Reparent Section Description escaped the fixture Notebook.",
            "Description Page and Section belong to the fixture Notebook",
        )
        require(
            notebook_to_group.get("parent_id") == notebook_id
            and notebook_to_group_destination.get("parent_id") == notebook_id,
            "Notebook-to-SectionGroup fixture relationship is invalid.",
            "case 1 source is Notebook-root and destination is a root SectionGroup",
        )
        require(
            group_to_notebook_source.get("parent_id") == notebook_id
            and group_to_notebook.get("parent_id") == group_to_notebook_source["id"],
            "SectionGroup-to-Notebook fixture relationship is invalid.",
            "case 2 source is under its root SectionGroup and destination is Notebook",
        )
        require(
            group_to_group_source.get("parent_id") == notebook_id
            and group_to_group_destination.get("parent_id") == notebook_id
            and group_to_group_source["id"] != group_to_group_destination["id"]
            and group_to_group.get("parent_id") == group_to_group_source["id"],
            "SectionGroup-to-SectionGroup fixture relationship is invalid.",
            "case 3 source and destination are distinct root SectionGroups",
        )
        expected_names = {
            "description_section": "00-Description",
            "description_page": REPARENT_SECTION_DESCRIPTION_TITLE,
            "notebook_to_group_destination": "01-Destination-Group",
            "notebook_to_group_section": "01-Notebook-To-Group-Section",
            "notebook_to_group_page": "01-Notebook-To-Group-Page",
            "group_to_notebook_source": "02-Source-Group",
            "group_to_notebook_section": "02-Group-To-Notebook-Section",
            "group_to_notebook_page": "02-Group-To-Notebook-Page",
            "group_to_group_source": "03-Source-Group",
            "group_to_group_destination": "03-Destination-Group",
            "group_to_group_section": "03-Group-To-Group-Section",
            "group_to_group_page": "03-Group-To-Group-Page",
        }
        require(
            all(
                display_name(resolved[key]) == expected
                for key, expected in expected_names.items()
            ),
            "Reparent fixture Groups/Sections/Pages do not have stable numbering.",
            "all three reparent cases use stable 00/01/02/03 numbering",
        )
        for section_key, page_key in (
            ("notebook_to_group_section", "notebook_to_group_page"),
            ("group_to_notebook_section", "group_to_notebook_page"),
            ("group_to_group_section", "group_to_group_page"),
        ):
            require(
                resolved[page_key].get("section_id") == resolved[section_key]["id"],
                "Reparent fixture Page escaped its declared Section.",
                f"{section_key} contains its numbered Page",
            )
        require(
            len(
                {
                    notebook_to_group["id"],
                    group_to_notebook["id"],
                    group_to_group["id"],
                }
            )
            == 3,
            "Reparent fixture must use three distinct target Sections.",
            "all three reparent cases use distinct target Section IDs",
        )
    elif scenario == "reparent-page":
        description_section = resolved["description_section"]
        description_page = resolved["description_page"]
        source = resolved["source_section"]
        destination = resolved["destination_section"]
        target = resolved["reparent_page"]
        anchor = resolved["destination_anchor_page"]
        require(
            source.get("parent_id") == destination.get("parent_id")
            and source.get("parent_id")
            and source["id"] != destination["id"],
            "Page reparent fixture Sections are not distinct children of one Notebook.",
            "source and destination Sections are distinct children of one Notebook",
        )
        require(
            description_section.get("parent_id") == source.get("parent_id")
            and description_page.get("section_id") == description_section["id"],
            "Reparent Page Description escaped the fixture Notebook.",
            "Description Page and Section belong to the fixture Notebook",
        )
        require(
            target.get("section_id") == source["id"]
            and target.get("parent_id") == source["id"]
            and int(target.get("page_level", 0)) == 1
            and target.get("parent_page_id") in {None, ""},
            "Page reparent target is not a root Page in the source Section.",
            "target Page is a root Page in the source Section",
        )
        require(
            anchor.get("section_id") == destination["id"]
            and anchor["id"] != target["id"],
            "Page reparent destination anchor is invalid.",
            "destination anchor remains outside the reparented target",
        )
        expected_names = {
            "description_section": "00-Description",
            "description_page": REPARENT_PAGE_DESCRIPTION_TITLE,
            "source_section": "01-Source-Section",
            "destination_section": "02-Destination-Section",
            "reparent_page": "01-Reparent-Page",
            "destination_anchor_page": "02-Destination-Anchor",
        }
        require(
            all(
                display_name(resolved[key]) == expected
                for key, expected in expected_names.items()
            ),
            "Page reparent fixture does not have stable numbering.",
            "Description, Sections, target Page, and anchor use stable numbering",
        )
        require(
            isinstance(copy_fixture, dict)
            and copy_fixture.get("page_id") == target["id"],
            "Page reparent rich-content evidence is not bound to the target Page.",
            "target Page owns the declared rich-content fixture",
        )
    elif scenario == "reparent-section-group":
        description_section = resolved["description_section"]
        description_page = resolved["description_page"]
        notebook_id = str(description_section.get("parent_id", ""))
        notebook_to_group_destination = resolved["notebook_to_group_destination"]
        notebook_to_group_target = resolved["notebook_to_group_target"]
        group_to_notebook_source = resolved["group_to_notebook_source"]
        group_to_notebook_target = resolved["group_to_notebook_target"]
        group_to_group_source = resolved["group_to_group_source"]
        group_to_group_destination = resolved["group_to_group_destination"]
        group_to_group_target = resolved["group_to_group_target"]
        require(
            description_page.get("section_id") == description_section["id"]
            and notebook_id,
            "Reparent SectionGroup Description escaped the fixture Notebook.",
            "Description Page and Section belong to the fixture Notebook",
        )
        require(
            notebook_to_group_target.get("parent_id") == notebook_id
            and notebook_to_group_destination.get("parent_id") == notebook_id,
            "Notebook-to-SectionGroup reparent fixture relationship is invalid.",
            "case 1 target is Notebook-root and destination is a root SectionGroup",
        )
        require(
            group_to_notebook_source.get("parent_id") == notebook_id
            and group_to_notebook_target.get("parent_id")
            == group_to_notebook_source["id"],
            "SectionGroup-to-Notebook reparent fixture relationship is invalid.",
            "case 2 target is under a root SectionGroup and destination is Notebook",
        )
        require(
            group_to_group_source.get("parent_id") == notebook_id
            and group_to_group_destination.get("parent_id") == notebook_id
            and group_to_group_source["id"] != group_to_group_destination["id"]
            and group_to_group_target.get("parent_id") == group_to_group_source["id"],
            "SectionGroup-to-SectionGroup reparent fixture relationship is invalid.",
            "case 3 source and destination are distinct root SectionGroups",
        )
        expected_names = {
            "description_section": "00-Description",
            "description_page": REPARENT_SECTION_GROUP_DESCRIPTION_TITLE,
            "notebook_to_group_destination": "01-Destination-Parent",
            "notebook_to_group_target": "01-Notebook-To-Group-Target",
            "notebook_to_group_section": "01-Descendant-Section",
            "notebook_to_group_page": "01-Descendant-Page",
            "group_to_notebook_source": "02-Source-Parent",
            "group_to_notebook_target": "02-Group-To-Notebook-Target",
            "group_to_notebook_section": "02-Descendant-Section",
            "group_to_notebook_page": "02-Descendant-Page",
            "group_to_group_source": "03-Source-Parent",
            "group_to_group_destination": "03-Destination-Parent",
            "group_to_group_target": "03-Group-To-Group-Target",
            "group_to_group_section": "03-Descendant-Section",
            "group_to_group_page": "03-Descendant-Page",
        }
        require(
            all(
                display_name(resolved[key]) == expected
                for key, expected in expected_names.items()
            ),
            "SectionGroup reparent fixture does not have stable numbering.",
            "all three reparent cases and descendants use stable numbering",
        )
        for prefix in (
            "notebook_to_group",
            "group_to_notebook",
            "group_to_group",
        ):
            target = resolved[f"{prefix}_target"]
            section = resolved[f"{prefix}_section"]
            page = resolved[f"{prefix}_page"]
            require(
                section.get("parent_id") == target["id"]
                and page.get("section_id") == section["id"],
                "SectionGroup reparent descendants escaped a target Group.",
                f"{prefix} target contains its numbered Section and Page descendants",
            )
        require(
            len(
                {
                    notebook_to_group_target["id"],
                    group_to_notebook_target["id"],
                    group_to_group_target["id"],
                }
            )
            == 3,
            "SectionGroup reparent fixture must use three distinct targets.",
            "all three reparent cases use distinct target Group IDs",
        )
    elif scenario == "delete":
        require(
            resolved["disposable_group"].get("parent_id")
            == resolved["delete_sandbox"]["id"],
            "Delete target is not a direct descendant of Delete-Sandbox.",
            "disposable_group is manifest-allowlisted under Delete-Sandbox",
        )
    elif scenario == "copy-page":
        require(
            resolved["parent_page"].get("section_id") == resolved["source_section"]["id"]
            and resolved["source_section"]["id"] != resolved["disposable_section"]["id"],
            "Copy Page fixture source and destination are not isolated Sections.",
            "Page Copy source and destination are isolated Sections",
        )
    elif scenario == "copy-section":
        require(
            resolved["source_section"].get("parent_id") == resolved["group_a"]["id"]
            and resolved["group_a"]["id"] != resolved["group_b"]["id"],
            "Copy Section fixture source and destination groups are invalid.",
            "source Section and destination Group are distinct",
        )
    elif scenario == "copy-section-group":
        require(
            resolved["source_section"].get("parent_id") == resolved["group_a"]["id"],
            "Copy SectionGroup fixture source Section escaped its source Group.",
            "rich source Section is contained by the source Group",
        )
    elif scenario == "copy-notebook":
        require(
            resolved["parent_page"].get("section_id") == resolved["source_section"]["id"],
            "Copy Notebook fixture rich Page escaped its source Section.",
            "rich source Page is contained by the source Notebook Section",
        )
    elif scenario == "move-page":
        require(
            resolved["disposable_page"].get("section_id")
            != resolved["destination_section"]["id"],
            "Move source Page already belongs to the destination Section.",
            "Move source Page and destination Section are distinct",
        )

    if scenario in PAGE_COPY_SCENARIOS:
        parent_key = "disposable_page" if scenario == "move-page" else "parent_page"
        parent = resolved[parent_key]
        semantic_page = resolved["semantic_page"]
        require(
            parent.get("section_id") == semantic_page.get("section_id")
            and int(parent.get("page_level", 0)) == 1
            and int(semantic_page.get("page_level", 0)) == 2
            and semantic_page.get("parent_page_id") == parent["id"],
            "Layered Copy fixture Page topology is invalid.",
            "strict parent and semantic child form an isolated two-page subtree",
        )

    if copy_fixture is not None:
        automated = {str(value).casefold() for value in copy_fixture.get("automated_content", [])}
        require(
            {"rich_text", "table", "image"}.issubset(automated),
            "Rich Copy fixture is missing a required automated content capability.",
            "rich text, table, and image capabilities were created and observed",
        )
        semantic = copy_fixture.get("semantic_page")
        if scenario in PAGE_COPY_SCENARIOS:
            require(
                isinstance(semantic, dict)
                and {"List", "Tag"}.issubset(semantic.get("observed_capabilities", []))
                and semantic.get("observed_counts", {}).get("List") == 3
                and semantic.get("observed_counts", {}).get("Tag") == 3,
                "Semantic Copy fixture is missing the three generated List/Tag items.",
                "semantic child contains three generated mixed List/Tag items",
            )
        elif scenario == "reparent-page":
            list_tag = copy_fixture.get("list_tag")
            require(
                isinstance(list_tag, dict)
                and list_tag.get("page_id") == resolved["reparent_page"]["id"]
                and {"List", "Tag"}.issubset(list_tag.get("observed_capabilities", []))
                and list_tag.get("observed_counts", {}).get("List") == 3
                and list_tag.get("observed_counts", {}).get("Tag") == 3,
                "Reparent Page fixture is missing its generated List/Tag content.",
                "target Page contains three mixed List/Tag items alongside rich content",
            )
    return checks


async def prepare_scenario_fixture(
    args: argparse.Namespace,
    options: RuntimeOptions,
    client: MCPStdioClient,
    notebook: dict[str, Any],
    notebook_path: str,
    spec: ScenarioSpec,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create only the selected scenario's declared fixture and persist evidence."""

    structure: dict[str, dict[str, Any]] = {}
    copy_fixture: dict[str, Any] | None = None
    token = str(uuid.uuid4())
    notebook_id = str(notebook["id"])

    if args.scenario == "create":
        group_a = await ensure_group(client, notebook_id, "Group-A")
        group_b = await ensure_group(client, notebook_id, "Group-B")
        delete_sandbox = await ensure_group(client, notebook_id, "Delete-Sandbox")
        content_section = await ensure_section(client, group_a["id"], "Content-Section")
        disposable_group = await ensure_group(client, delete_sandbox["id"], "Disposable-Group")
        disposable_section = await ensure_section(
            client, delete_sandbox["id"], "Disposable-Section"
        )
        parent = await ensure_page(
            client, content_section["id"], "Parent", f"Parent smoke token: {token}"
        )
        child = await ensure_page(
            client, content_section["id"], "Child", f"Child smoke token: {token}"
        )
        sibling = await ensure_page(
            client, content_section["id"], "Sibling", f"Sibling smoke token: {token}"
        )
        disposable_page = await ensure_page(
            client,
            disposable_section["id"],
            "Disposable-Page",
            f"Disposable smoke token: {token}",
        )
        parent = await enforce_page_position(client, content_section["id"], parent["id"], "", 1)
        child = await enforce_page_position(
            client, content_section["id"], child["id"], parent["id"], 2
        )
        sibling = await enforce_page_position(
            client, content_section["id"], sibling["id"], child["id"], 1
        )
        parent, copy_fixture = await ensure_copy_rich_fixture(
            client, parent, options.run_dir
        )
        structure.update(
            group_a=group_a,
            group_b=group_b,
            delete_sandbox=delete_sandbox,
            content_section=content_section,
            parent_page=parent,
            child_page=child,
            sibling_page=sibling,
            disposable_group=disposable_group,
            disposable_section=disposable_section,
            disposable_page=disposable_page,
        )
    elif args.scenario == "rename":
        target_key = args.target
        if target_key == "content_section":
            group = await ensure_group(client, notebook_id, "Rename-Group")
            structure[target_key] = await ensure_section(client, group["id"], "Content-Section")
        else:
            name = "Group-A" if target_key == "group_a" else "Group-B"
            structure[target_key] = await ensure_group(client, notebook_id, name)
    elif args.scenario == "reorder-page":
        description_section = await ensure_section(client, notebook_id, "Description")
        description_page = await ensure_page(
            client,
            description_section["id"],
            REORDER_PAGE_DESCRIPTION_TITLE,
            f"{REORDER_PAGE_DESCRIPTION}\nFixture token: {token}",
        )
        description_text = str(
            (await client.call_tool("get_page_text", {"page_id": description_page["id"]}))["text"]
        )
        required_description_markers = (
            "操作前（顺序 01,02,03）",
            "预期操作后（顺序 01,03,02）",
            "默认恢复后（顺序 01,02,03）",
        )
        if not all(marker in description_text for marker in required_description_markers):
            raise InvariantFailure("Reorder Page Description is missing a before/after/restore marker.")
        section = await ensure_section(client, notebook_id, "01-Reorder-Page-Section")
        parent = await ensure_page(client, section["id"], "01-Parent", f"01 Parent token: {token}")
        child = await ensure_page(client, section["id"], "02-Child", f"02 Child token: {token}")
        sibling = await ensure_page(client, section["id"], "03-Sibling", f"03 Sibling token: {token}")
        parent = await enforce_page_position(client, section["id"], parent["id"], "", 1)
        child = await enforce_page_position(client, section["id"], child["id"], parent["id"], 2)
        sibling = await enforce_page_position(client, section["id"], sibling["id"], child["id"], 1)
        structure.update(
            description_section=description_section,
            description_page=description_page,
            reorder_section=section,
            parent_page=parent,
            child_page=child,
            sibling_page=sibling,
        )
    elif args.scenario == "reorder-section":
        description_section = await ensure_section(client, notebook_id, "00-Description")
        description_page = await ensure_page(
            client,
            description_section["id"],
            REORDER_SECTION_DESCRIPTION_TITLE,
            f"{REORDER_SECTION_DESCRIPTION}\nFixture token: {token}",
        )
        description_text = str(
            (
                await client.call_tool(
                    "get_page_text", {"page_id": description_page["id"]}
                )
            )["text"]
        )
        required_description_markers = (
            "场景一：父级为 Notebook",
            "场景二：父级为 01-Section-Parent（SectionGroup）",
            "操作后：00-Description, 01-Root-Section-A, 03-Root-Section-C, 02-Root-Section-B",
            "操作后：01-Group-Section-A, 03-Group-Section-C, 02-Group-Section-B",
        )
        if not all(marker in description_text for marker in required_description_markers):
            raise InvariantFailure(
                "Reorder Section Description is missing a parent or order marker."
            )
        parent_group = await ensure_group(client, notebook_id, "01-Section-Parent")
        root_sections = {}
        group_sections = {}
        for index, letter in enumerate("ABC", start=1):
            root_section = await ensure_section(
                client, notebook_id, f"{index:02d}-Root-Section-{letter}"
            )
            group_section = await ensure_section(
                client, parent_group["id"], f"{index:02d}-Group-Section-{letter}"
            )
            root_page = await ensure_page(
                client,
                root_section["id"],
                f"{index:02d}-Root-Page-{letter}",
                f"Root Section token: {token}-{letter}",
            )
            group_page = await ensure_page(
                client,
                group_section["id"],
                f"{index:02d}-Group-Page-{letter}",
                f"Group Section token: {token}-{letter}",
            )
            key = letter.casefold()
            root_sections[key] = (root_section, root_page)
            group_sections[key] = (group_section, group_page)
        structure.update(
            description_section=description_section,
            description_page=description_page,
            section_parent_group=parent_group,
        )
        for key, (section, page) in root_sections.items():
            structure[f"root_section_{key}"] = section
            structure[f"root_page_{key}"] = page
        for key, (section, page) in group_sections.items():
            structure[f"group_section_{key}"] = section
            structure[f"group_page_{key}"] = page
    elif args.scenario == "reorder-section-group":
        description_section = await ensure_section(client, notebook_id, "00-Description")
        description_page = await ensure_page(
            client,
            description_section["id"],
            REORDER_SECTION_GROUP_DESCRIPTION_TITLE,
            f"{REORDER_SECTION_GROUP_DESCRIPTION}\nFixture token: {token}",
        )
        description_text = str(
            (
                await client.call_tool(
                    "get_page_text", {"page_id": description_page["id"]}
                )
            )["text"]
        )
        required_description_markers = (
            "场景一：父级为 Notebook",
            "场景二：父级为 00-Group-Parent（SectionGroup）",
            "操作后：00-Group-Parent, 01-Root-Group-A, 03-Root-Group-C, 02-Root-Group-B",
            "操作后：01-Nested-Group-A, 03-Nested-Group-C, 02-Nested-Group-B",
        )
        if not all(marker in description_text for marker in required_description_markers):
            raise InvariantFailure(
                "Reorder SectionGroup Description is missing a parent or order marker."
            )
        structure.update(
            description_section=description_section,
            description_page=description_page,
        )
        parent_group = await ensure_group(client, notebook_id, "00-Group-Parent")
        structure["section_group_parent"] = parent_group
        for index, letter in enumerate("ABC", start=1):
            root_group = await ensure_group(
                client, notebook_id, f"{index:02d}-Root-Group-{letter}"
            )
            root_section = await ensure_section(
                client, root_group["id"], f"{index:02d}-Root-Section-{letter}"
            )
            root_page = await ensure_page(
                client,
                root_section["id"],
                f"{index:02d}-Root-Page-{letter}",
                f"Root SectionGroup token: {token}-{letter}",
            )
            nested_group = await ensure_group(
                client,
                parent_group["id"],
                f"{index:02d}-Nested-Group-{letter}",
            )
            nested_section = await ensure_section(
                client,
                nested_group["id"],
                f"{index:02d}-Nested-Section-{letter}",
            )
            nested_page = await ensure_page(
                client,
                nested_section["id"],
                f"{index:02d}-Nested-Page-{letter}",
                f"Nested SectionGroup token: {token}-{letter}",
            )
            key = letter.casefold()
            structure[f"root_group_{key}"] = root_group
            structure[f"root_section_{key}"] = root_section
            structure[f"root_page_{key}"] = root_page
            structure[f"nested_group_{key}"] = nested_group
            structure[f"nested_section_{key}"] = nested_section
            structure[f"nested_page_{key}"] = nested_page
    elif args.scenario == "reparent-section":
        description_section = await ensure_section(client, notebook_id, "00-Description")
        description_page = await ensure_page(
            client,
            description_section["id"],
            REPARENT_SECTION_DESCRIPTION_TITLE,
            f"{REPARENT_SECTION_DESCRIPTION}\nFixture token: {token}",
        )
        description_text = str(
            (
                await client.call_tool(
                    "get_page_text", {"page_id": description_page["id"]}
                )
            )["text"]
        )
        required_description_markers = (
            "场景一：Notebook 父级 → SectionGroup 父级",
            "场景二：SectionGroup 父级 → Notebook 父级",
            "场景三：SectionGroup 父级 → SectionGroup 父级",
            "三个目标 Section 各自包含同编号 Page",
        )
        if not all(marker in description_text for marker in required_description_markers):
            raise InvariantFailure(
                "Reparent Section Description is missing a parent transition marker."
            )

        notebook_to_group_destination = await ensure_group(
            client, notebook_id, "01-Destination-Group"
        )
        notebook_to_group_section = await ensure_section(
            client, notebook_id, "01-Notebook-To-Group-Section"
        )
        notebook_to_group_page = await ensure_page(
            client,
            notebook_to_group_section["id"],
            "01-Notebook-To-Group-Page",
            f"Reparent case 1 token: {token}",
        )

        group_to_notebook_source = await ensure_group(
            client, notebook_id, "02-Source-Group"
        )
        group_to_notebook_section = await ensure_section(
            client,
            group_to_notebook_source["id"],
            "02-Group-To-Notebook-Section",
        )
        group_to_notebook_page = await ensure_page(
            client,
            group_to_notebook_section["id"],
            "02-Group-To-Notebook-Page",
            f"Reparent case 2 token: {token}",
        )

        group_to_group_source = await ensure_group(
            client, notebook_id, "03-Source-Group"
        )
        group_to_group_destination = await ensure_group(
            client, notebook_id, "03-Destination-Group"
        )
        group_to_group_section = await ensure_section(
            client,
            group_to_group_source["id"],
            "03-Group-To-Group-Section",
        )
        group_to_group_page = await ensure_page(
            client,
            group_to_group_section["id"],
            "03-Group-To-Group-Page",
            f"Reparent case 3 token: {token}",
        )
        structure.update(
            description_section=description_section,
            description_page=description_page,
            notebook_to_group_destination=notebook_to_group_destination,
            notebook_to_group_section=notebook_to_group_section,
            notebook_to_group_page=notebook_to_group_page,
            group_to_notebook_source=group_to_notebook_source,
            group_to_notebook_section=group_to_notebook_section,
            group_to_notebook_page=group_to_notebook_page,
            group_to_group_source=group_to_group_source,
            group_to_group_destination=group_to_group_destination,
            group_to_group_section=group_to_group_section,
            group_to_group_page=group_to_group_page,
        )
    elif args.scenario == "reparent-page":
        description_section = await ensure_section(client, notebook_id, "00-Description")
        description_page = await ensure_page(
            client,
            description_section["id"],
            REPARENT_PAGE_DESCRIPTION_TITLE,
            f"{REPARENT_PAGE_DESCRIPTION}\nFixture token: {token}",
        )
        description_text = str(
            (
                await client.call_tool(
                    "get_page_text", {"page_id": description_page["id"]}
                )
            )["text"]
        )
        required_description_markers = (
            "操作前：01-Source-Section/01-Reparent-Page",
            "操作后：02-Destination-Section/01-Reparent-Page",
            "默认恢复后：01-Source-Section/01-Reparent-Page",
            "Rich Text、Table、List、Tag 和 Image",
            "旧 ID → 新 ID",
        )
        if not all(marker in description_text for marker in required_description_markers):
            raise InvariantFailure("Reparent Page Description is missing a state marker.")
        source = await ensure_section(client, notebook_id, "01-Source-Section")
        destination = await ensure_section(client, notebook_id, "02-Destination-Section")
        target = await ensure_page(
            client,
            source["id"],
            "01-Reparent-Page",
            f"Page reparent token: {token}",
        )
        target, copy_fixture = await ensure_reparent_page_rich_fixture(
            client,
            target,
            options.run_dir,
        )
        target, list_tag_fixture = await ensure_copy_list_tag_fixture(client, target)
        copy_fixture["automated_content"] = [
            "rich_text",
            "table",
            "image",
            "list",
            "tag",
        ]
        copy_fixture["list_tag"] = list_tag_fixture
        anchor = await ensure_page(
            client,
            destination["id"],
            "02-Destination-Anchor",
            f"Destination anchor token: {token}",
        )
        structure.update(
            description_section=description_section,
            description_page=description_page,
            source_section=source,
            destination_section=destination,
            reparent_page=target,
            destination_anchor_page=anchor,
        )
    elif args.scenario == "reparent-section-group":
        description_section = await ensure_section(client, notebook_id, "00-Description")
        description_page = await ensure_page(
            client,
            description_section["id"],
            REPARENT_SECTION_GROUP_DESCRIPTION_TITLE,
            f"{REPARENT_SECTION_GROUP_DESCRIPTION}\nFixture token: {token}",
        )
        description_text = str(
            (
                await client.call_tool(
                    "get_page_text", {"page_id": description_page["id"]}
                )
            )["text"]
        )
        required_description_markers = (
            "场景一：Notebook 父级 → SectionGroup 父级",
            "场景二：SectionGroup 父级 → Notebook 父级",
            "场景三：SectionGroup 父级 → SectionGroup 父级",
            "三个目标 Group 各自包含同编号 Section 和 Page",
        )
        if not all(marker in description_text for marker in required_description_markers):
            raise InvariantFailure(
                "Reparent SectionGroup Description is missing a transition marker."
            )

        notebook_to_group_destination = await ensure_group(
            client, notebook_id, "01-Destination-Parent"
        )
        notebook_to_group_target = await ensure_group(
            client, notebook_id, "01-Notebook-To-Group-Target"
        )
        notebook_to_group_section = await ensure_section(
            client, notebook_to_group_target["id"], "01-Descendant-Section"
        )
        notebook_to_group_page = await ensure_page(
            client,
            notebook_to_group_section["id"],
            "01-Descendant-Page",
            f"SectionGroup reparent case 1 token: {token}",
        )

        group_to_notebook_source = await ensure_group(
            client, notebook_id, "02-Source-Parent"
        )
        group_to_notebook_target = await ensure_group(
            client, group_to_notebook_source["id"], "02-Group-To-Notebook-Target"
        )
        group_to_notebook_section = await ensure_section(
            client, group_to_notebook_target["id"], "02-Descendant-Section"
        )
        group_to_notebook_page = await ensure_page(
            client,
            group_to_notebook_section["id"],
            "02-Descendant-Page",
            f"SectionGroup reparent case 2 token: {token}",
        )

        group_to_group_source = await ensure_group(
            client, notebook_id, "03-Source-Parent"
        )
        group_to_group_destination = await ensure_group(
            client, notebook_id, "03-Destination-Parent"
        )
        group_to_group_target = await ensure_group(
            client, group_to_group_source["id"], "03-Group-To-Group-Target"
        )
        group_to_group_section = await ensure_section(
            client, group_to_group_target["id"], "03-Descendant-Section"
        )
        group_to_group_page = await ensure_page(
            client,
            group_to_group_section["id"],
            "03-Descendant-Page",
            f"SectionGroup reparent case 3 token: {token}",
        )
        structure.update(
            description_section=description_section,
            description_page=description_page,
            notebook_to_group_destination=notebook_to_group_destination,
            notebook_to_group_target=notebook_to_group_target,
            notebook_to_group_section=notebook_to_group_section,
            notebook_to_group_page=notebook_to_group_page,
            group_to_notebook_source=group_to_notebook_source,
            group_to_notebook_target=group_to_notebook_target,
            group_to_notebook_section=group_to_notebook_section,
            group_to_notebook_page=group_to_notebook_page,
            group_to_group_source=group_to_group_source,
            group_to_group_destination=group_to_group_destination,
            group_to_group_target=group_to_group_target,
            group_to_group_section=group_to_group_section,
            group_to_group_page=group_to_group_page,
        )
    elif args.scenario == "delete":
        sandbox = await ensure_group(client, notebook_id, "Delete-Sandbox")
        disposable = await ensure_group(client, sandbox["id"], "Disposable-Group")
        structure.update(delete_sandbox=sandbox, disposable_group=disposable)
    elif args.scenario == "copy-page":
        source_section = await ensure_section(client, notebook_id, "Source")
        destination = await ensure_section(client, notebook_id, "Destination")
        page, semantic_page, copy_fixture = await _layered_copy_pages(
            client, source_section, options, token
        )
        structure.update(
            source_section=source_section,
            parent_page=page,
            semantic_page=semantic_page,
            disposable_section=destination,
        )
    elif args.scenario == "copy-section":
        source_group = await ensure_group(client, notebook_id, "Source-Group")
        destination = await ensure_group(client, notebook_id, "Group-B")
        source_section = await ensure_section(client, source_group["id"], "Source-Section")
        page, semantic_page, copy_fixture = await _layered_copy_pages(
            client, source_section, options, token
        )
        structure.update(
            group_a=source_group,
            group_b=destination,
            source_section=source_section,
            parent_page=page,
            semantic_page=semantic_page,
        )
    elif args.scenario == "copy-section-group":
        source_group = await ensure_group(client, notebook_id, "Group-A")
        source_section = await ensure_section(client, source_group["id"], "Source-Section")
        page, semantic_page, copy_fixture = await _layered_copy_pages(
            client, source_section, options, token
        )
        structure.update(
            group_a=source_group,
            source_section=source_section,
            parent_page=page,
            semantic_page=semantic_page,
        )
    elif args.scenario == "copy-notebook":
        source_section = await ensure_section(client, notebook_id, "Source-Section")
        page, semantic_page, copy_fixture = await _layered_copy_pages(
            client, source_section, options, token
        )
        structure.update(
            source_section=source_section,
            parent_page=page,
            semantic_page=semantic_page,
        )
    elif args.scenario == "move-page":
        source_section = await ensure_section(client, notebook_id, "Source")
        destination = await ensure_section(client, notebook_id, "Destination")
        page, semantic_page, copy_fixture = await _layered_copy_pages(
            client,
            source_section,
            options,
            token,
            parent_title="Disposable-Page",
        )
        structure.update(
            disposable_page=page,
            semantic_page=semantic_page,
            destination_section=destination,
        )
    else:
        raise ValueError(f"Unsupported fixture scenario: {args.scenario}")

    snapshot = await capture_snapshot(client, notebook_id)
    manifest = new_manifest(
        options.run_dir,
        notebook,
        structure,
        notebook_path=notebook_path,
    )
    manifest["scenario_policies"] = {args.scenario: spec.policy.as_dict()}
    manifest["scenario_spec"] = spec.as_dict()
    manifest["scenario_spec"]["fixture_profile"]["actual_manifest_keys"] = sorted(structure)
    manifest["mcp_process_contract"] = {
        "maximum_starts": 1,
        "fixture_and_scenario_share_process": True,
    }
    if copy_fixture is not None:
        if args.scenario == "reparent-page":
            manifest["reparent_page_fixture"] = copy_fixture
        else:
            manifest["copy_fixture"] = copy_fixture
    manifest["fixture_validation"] = {"status": "pending"}
    write_json(manifest_path(options.run_dir), manifest)
    write_json(options.run_dir / "prepared.json", snapshot)
    write_json(options.run_dir / "page-hashes.json", snapshot.get("page_hashes", {}))
    fixture_result = {
        "scenario": args.scenario,
        "notebook": stable_item(notebook),
        "structure_ids": {key: value["id"] for key, value in structure.items()},
        "fixture_profile": manifest["scenario_spec"]["fixture_profile"],
        "validation": {"passed": False, "checks": []},
    }
    write_json(options.run_dir / "fixture-result.json", fixture_result)

    declared = set(spec.fixture.manifest_keys)
    try:
        if args.scenario == "rename" and set(structure) != {args.target}:
            raise InvariantFailure(
                "Rename fixture must create exactly the one CLI-selected manifest key."
            )
        if args.scenario != "rename" and not declared.issubset(structure):
            missing = sorted(declared - set(structure))
            raise InvariantFailure(f"Fixture did not create declared manifest keys: {missing}")
        validation_checks = _validate_fixture_snapshot(
            args.scenario,
            snapshot,
            structure,
            copy_fixture,
        )
        if args.scenario == "reorder-page":
            validation_checks.append(
                "Description Page states 01,02,03 before; 01,03,02 after; 01,02,03 restored"
            )
        elif args.scenario == "reorder-section":
            validation_checks.append(
                "Description Page states 01,02,03 before; 01,03,02 after; "
                "01,02,03 restored for Notebook and SectionGroup parents"
            )
        elif args.scenario == "reorder-section-group":
            validation_checks.append(
                "Description Page states 01,02,03 before; 01,03,02 after; "
                "01,02,03 restored for Notebook and SectionGroup parents"
            )
        elif args.scenario == "reparent-section":
            validation_checks.append(
                "Description Page states before/after/restore for Notebook-to-SectionGroup, "
                "SectionGroup-to-Notebook, and SectionGroup-to-SectionGroup reparents"
            )
        elif args.scenario == "reparent-page":
            validation_checks.append(
                "Description Page states ID-remapping and rich-content acceptance for the "
                "numbered Page reparent"
            )
        elif args.scenario == "reparent-section-group":
            validation_checks.append(
                "Description Page states before/after/restore for Notebook-to-SectionGroup, "
                "SectionGroup-to-Notebook, and SectionGroup-to-SectionGroup typed reparents"
            )
    except InvariantFailure as exc:
        manifest["fixture_validation"] = {
            "status": "failed",
            "error": str(exc),
        }
        fixture_result["validation"] = {
            "passed": False,
            "checks": [],
            "error": str(exc),
        }
        write_json(manifest_path(options.run_dir), manifest)
        write_json(options.run_dir / "fixture-result.json", fixture_result)
        raise

    manifest["fixture_validation"] = {
        "status": "passed",
        "checks": validation_checks,
    }
    fixture_result["validation"] = {"passed": True, "checks": validation_checks}
    write_json(manifest_path(options.run_dir), manifest)
    write_json(options.run_dir / "fixture-result.json", fixture_result)
    return manifest, fixture_result


__all__ = [
    "REORDER_PAGE_DESCRIPTION",
    "REORDER_PAGE_DESCRIPTION_TITLE",
    "REORDER_SECTION_DESCRIPTION",
    "REORDER_SECTION_DESCRIPTION_TITLE",
    "REORDER_SECTION_GROUP_DESCRIPTION",
    "REORDER_SECTION_GROUP_DESCRIPTION_TITLE",
    "REPARENT_SECTION_DESCRIPTION",
    "REPARENT_SECTION_DESCRIPTION_TITLE",
    "REPARENT_PAGE_DESCRIPTION",
    "REPARENT_PAGE_DESCRIPTION_TITLE",
    "REPARENT_SECTION_GROUP_DESCRIPTION",
    "REPARENT_SECTION_GROUP_DESCRIPTION_TITLE",
    "prepare_scenario_fixture",
]
