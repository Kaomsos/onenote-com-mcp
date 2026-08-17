"""Typed mutation MCP tools.

The functions in this module are intentionally thin: policy checks, bridge calls,
and read-back verification belong to :mod:`local_onenote_mcp.services`.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from .responses import invoke as _invoke


MAX_BATCH_ITEMS = 20
MAX_SORT_CHILDREN = 1_000
ExactId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
SafeName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]


class _BatchItem(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContainerCreateItem(_BatchItem):
    name: SafeName


class PageCreateItem(_BatchItem):
    title: SafeName
    content: Annotated[str, Field(max_length=100_000)] = ""
    content_format: Literal["plain", "html", "markdown"] = "plain"


class _ContainerRenameItem(_BatchItem):
    new_name: SafeName
    expected_name: SafeName
    expected_parent_id: ExactId
    expected_modified: str | None = None


class SectionRenameItem(_ContainerRenameItem):
    section_id: ExactId


class SectionGroupRenameItem(_ContainerRenameItem):
    section_group_id: ExactId


class PageRenameItem(_BatchItem):
    page_id: ExactId
    new_title: SafeName
    expected_title: SafeName
    expected_section_id: ExactId
    expected_modified: str | None = None


class _ContainerReparentItem(_BatchItem):
    expected_name: SafeName
    expected_parent_id: ExactId
    expected_modified: str | None = None


class SectionReparentItem(_ContainerReparentItem):
    section_id: ExactId


class SectionGroupReparentItem(_ContainerReparentItem):
    section_group_id: ExactId


class PageReparentItem(_BatchItem):
    page_id: ExactId
    expected_title: SafeName
    expected_section_id: ExactId
    expected_modified: str | None = None
    page_scope: Literal["page_only", "indentation_subtree"] = "page_only"


class _ContainerDeleteItem(_BatchItem):
    expected_name: SafeName
    expected_parent_id: ExactId
    expected_modified: str | None = None


class SectionDeleteItem(_ContainerDeleteItem):
    section_id: ExactId


class SectionGroupDeleteItem(_ContainerDeleteItem):
    section_group_id: ExactId


class PageDeleteItem(_BatchItem):
    page_id: ExactId
    expected_title: SafeName
    expected_section_id: ExactId
    expected_modified: str | None = None


def _dump(items: list[_BatchItem] | None) -> list[dict[str, Any]] | None:
    return None if items is None else [item.model_dump() for item in items]


ContainerCreateItems = Annotated[list[ContainerCreateItem], Field(min_length=1, max_length=MAX_BATCH_ITEMS)]
PageCreateItems = Annotated[list[PageCreateItem], Field(min_length=1, max_length=MAX_BATCH_ITEMS)]
SectionRenameItems = Annotated[list[SectionRenameItem], Field(min_length=1, max_length=MAX_BATCH_ITEMS)]
SectionGroupRenameItems = Annotated[list[SectionGroupRenameItem], Field(min_length=1, max_length=MAX_BATCH_ITEMS)]
PageRenameItems = Annotated[list[PageRenameItem], Field(min_length=1, max_length=MAX_BATCH_ITEMS)]
SectionReparentItems = Annotated[list[SectionReparentItem], Field(min_length=1, max_length=MAX_BATCH_ITEMS)]
SectionGroupReparentItems = Annotated[list[SectionGroupReparentItem], Field(min_length=1, max_length=MAX_BATCH_ITEMS)]
PageReparentItems = Annotated[list[PageReparentItem], Field(min_length=1, max_length=MAX_BATCH_ITEMS)]
SectionDeleteItems = Annotated[list[SectionDeleteItem], Field(min_length=1, max_length=MAX_BATCH_ITEMS)]
SectionGroupDeleteItems = Annotated[list[SectionGroupDeleteItem], Field(min_length=1, max_length=MAX_BATCH_ITEMS)]
PageDeleteItems = Annotated[list[PageDeleteItem], Field(min_length=1, max_length=MAX_BATCH_ITEMS)]
ExpectedChildIds = Annotated[
    list[ExactId], Field(min_length=1, max_length=MAX_SORT_CHILDREN)
]


def invoke(operation: str, **arguments: Any) -> dict[str, Any]:
    """Dispatch one typed mutation through the Operation Runtime."""

    return _invoke(operation, **arguments)


async def create_notebook(name: str, base_folder: str | None = None) -> dict[str, Any]:
    """With Create, create a Notebook and verify it through the typed hierarchy model."""

    return invoke("create_notebook", name=name, base_folder=base_folder)


async def create_section(
    parent_id: str,
    name: str | None = None,
    items: ContainerCreateItems | None = None,
    expected_parent_name: str | None = None,
    expected_parent_modified: str | None = None,
) -> dict[str, Any]:
    """With Create, create one Section with name, or up to 20 Sections with items below one confirmed exact parent; the two modes are mutually exclusive."""

    return invoke("create_section", parent_id=parent_id, name=name, items=_dump(items), expected_parent_name=expected_parent_name, expected_parent_modified=expected_parent_modified)


async def create_section_group(
    parent_id: str,
    name: str | None = None,
    items: ContainerCreateItems | None = None,
    expected_parent_name: str | None = None,
    expected_parent_modified: str | None = None,
) -> dict[str, Any]:
    """With Create, create one SectionGroup with name, or up to 20 SectionGroups with items below one confirmed exact parent; the two modes are mutually exclusive."""

    return invoke("create_section_group", parent_id=parent_id, name=name, items=_dump(items), expected_parent_name=expected_parent_name, expected_parent_modified=expected_parent_modified)


async def create_page(
    section_id: str,
    title: str | None = None,
    content: str = "",
    content_format: str = "plain",
    items: PageCreateItems | None = None,
    expected_section_name: str | None = None,
    expected_section_modified: str | None = None,
) -> dict[str, Any]:
    """With Create and Writes, create one Page with title/content, or up to 20 Pages with items below one confirmed exact Section; the two modes are mutually exclusive."""

    return invoke(
        "create_page",
        section_id=section_id,
        title=title,
        content=content,
        content_format=content_format,
        items=_dump(items),
        expected_section_name=expected_section_name,
        expected_section_modified=expected_section_modified,
    )


async def rename_page(
    page_id: str | None = None,
    title: str | None = None,
    expected_title: str | None = None,
    expected_section_id: str | None = None,
    expected_modified: str | None = None,
    items: PageRenameItems | None = None,
) -> dict[str, Any]:
    """With Writes, rename one exact confirmed Page, or up to 20 same-Notebook Pages through explicit items; modes are mutually exclusive."""

    return invoke(
        "rename_page",
        page_id=page_id,
        title=title,
        expected_title=expected_title,
        expected_section_id=expected_section_id,
        expected_modified=expected_modified,
        items=_dump(items),
    )


async def rename_section_group(
    section_group_id: str | None = None,
    new_name: str | None = None,
    expected_name: str | None = None,
    expected_parent_id: str | None = None,
    expected_modified: str | None = None,
    items: SectionGroupRenameItems | None = None,
) -> dict[str, Any]:
    """With Writes, rename one exact confirmed SectionGroup, or up to 20 same-Notebook SectionGroups through explicit items; modes are mutually exclusive."""

    return invoke(
        "rename_section_group",
        section_group_id=section_group_id,
        new_name=new_name,
        expected_name=expected_name,
        expected_parent_id=expected_parent_id,
        expected_modified=expected_modified,
        items=_dump(items),
    )


async def rename_section(
    section_id: str | None = None,
    new_name: str | None = None,
    expected_name: str | None = None,
    expected_parent_id: str | None = None,
    expected_modified: str | None = None,
    items: SectionRenameItems | None = None,
) -> dict[str, Any]:
    """With Writes, rename one exact confirmed Section, or up to 20 same-Notebook Sections through explicit items; modes are mutually exclusive."""

    return invoke(
        "rename_section",
        section_id=section_id,
        new_name=new_name,
        expected_name=expected_name,
        expected_parent_id=expected_parent_id,
        expected_modified=expected_modified,
        items=_dump(items),
    )


async def reorder_page(
    page_id: str,
    expected_title: str,
    expected_section_id: str,
    after_page_id: str | None = None,
    page_level: int = 0,
    expected_modified: str | None = None,
) -> dict[str, Any]:
    """With Writes, reorder an exact Page within its Section and verify order and indentation."""

    return invoke(
        "reorder_page",
        page_id=page_id,
        expected_title=expected_title,
        expected_section_id=expected_section_id,
        after_page_id=after_page_id,
        page_level=page_level,
        expected_modified=expected_modified,
    )


async def reorder_section(
    section_id: str,
    expected_name: str,
    expected_parent_id: str,
    after_section_id: str | None = None,
    expected_modified: str | None = None,
) -> dict[str, Any]:
    """With Writes, reorder an exact Section among same-parent Section siblings."""

    return invoke(
        "reorder_section",
        section_id=section_id,
        expected_name=expected_name,
        expected_parent_id=expected_parent_id,
        after_section_id=after_section_id,
        expected_modified=expected_modified,
    )


async def sort_children(
    parent_id: ExactId,
    expected_parent_name: SafeName,
    expected_child_ids: ExpectedChildIds,
    child_type: Literal["section", "page"] | None = None,
    key: Literal["name", "created", "modified"] = "name",
    direction: Literal["ascending", "descending"] = "ascending",
    expected_parent_modified: str | None = None,
) -> dict[str, Any]:
    """With Writes, stably sort only direct children: Notebook/SectionGroup parents imply Section, while Section/Page parents imply Page. child_type is an optional consistency check; recursive sorting is unsupported."""

    return invoke(
        "sort_children",
        child_type=child_type,
        parent_id=parent_id,
        expected_parent_name=expected_parent_name,
        expected_parent_modified=expected_parent_modified,
        expected_child_ids=expected_child_ids,
        key=key,
        direction=direction,
    )


async def reparent_section(
    section_id: str | None = None,
    destination_parent_id: str | None = None,
    expected_name: str | None = None,
    expected_parent_id: str | None = None,
    expected_modified: str | None = None,
    items: SectionReparentItems | None = None,
) -> dict[str, Any]:
    """With Writes and Organize, reparent one exact Section or up to 20 confirmed Sections to one exact same-Notebook parent; a batch returns final observed live hierarchy positions in input order, and modes are mutually exclusive."""

    return invoke(
        "reparent_section",
        section_id=section_id,
        destination_parent_id=destination_parent_id,
        expected_name=expected_name,
        expected_parent_id=expected_parent_id,
        expected_modified=expected_modified,
        items=_dump(items),
    )


async def reparent_page(
    page_id: str | None = None,
    destination_section_id: str | None = None,
    expected_title: str | None = None,
    expected_section_id: str | None = None,
    expected_modified: str | None = None,
    page_scope: Literal["page_only", "indentation_subtree"] = "page_only",
    items: PageReparentItems | None = None,
) -> dict[str, Any]:
    """With Writes and Organize, reparent one exact Page scope or up to 20 non-overlapping Page scopes to one same-Notebook Section; a batch returns final observed live hierarchy positions in input order.

    The selected Page becomes a root Page in the destination Section.  By
    default only that Page moves and excluded descendants remain in the source
    Section, promoted by one level.  Set page_scope="indentation_subtree" to move
    the complete indentation subtree.  The response reports only the destination
    root Page's observed final position; it does not request or guarantee placement.
    """

    return invoke(
        "reparent_page",
        page_id=page_id,
        destination_section_id=destination_section_id,
        expected_title=expected_title,
        expected_section_id=expected_section_id,
        expected_modified=expected_modified,
        page_scope=page_scope,
        items=_dump(items),
    )


async def reparent_section_group(
    section_group_id: str | None = None,
    destination_parent_id: str | None = None,
    expected_name: str | None = None,
    expected_parent_id: str | None = None,
    expected_modified: str | None = None,
    items: SectionGroupReparentItems | None = None,
) -> dict[str, Any]:
    """With Writes and Organize, reparent one exact SectionGroup or up to 20 non-overlapping SectionGroups to one exact same-Notebook parent; a batch returns final observed live hierarchy positions in input order."""

    return invoke(
        "reparent_section_group",
        section_group_id=section_group_id,
        destination_parent_id=destination_parent_id,
        expected_name=expected_name,
        expected_parent_id=expected_parent_id,
        expected_modified=expected_modified,
        items=_dump(items),
    )


async def append_page_content(
    page_id: str,
    content: str,
    expected_title: str,
    expected_section_id: str,
    expected_modified: str | None = None,
    content_format: str = "plain",
    x: float | None = None,
    y: float | None = None,
) -> dict[str, Any]:
    """With Writes, append content to an exact confirmed Page."""

    return invoke(
        "append_page_content",
        page_id=page_id,
        content=content,
        expected_title=expected_title,
        expected_section_id=expected_section_id,
        expected_modified=expected_modified,
        content_format=content_format,
        x=x,
        y=y,
    )


async def add_page_image_from_file(
    page_id: str,
    image_path: str,
    expected_title: str,
    expected_section_id: str,
    expected_modified: str | None = None,
    x: float = 36.0,
    y: float = 120.0,
    width: float | None = None,
    height: float | None = None,
) -> dict[str, Any]:
    """With Writes and Local File IO, add a validated local image to an exact confirmed Page."""

    return invoke(
        "add_page_image_from_file",
        page_id=page_id,
        image_path=image_path,
        expected_title=expected_title,
        expected_section_id=expected_section_id,
        expected_modified=expected_modified,
        x=x,
        y=y,
        width=width,
        height=height,
    )


async def replace_page_body(
    page_id: str,
    content: str,
    expected_title: str,
    expected_section_id: str,
    expected_modified: str | None = None,
    content_format: str = "plain",
) -> dict[str, Any]:
    """With Writes and Deletes, replace supported body objects on an exact Page and report partial failures."""

    return invoke(
        "replace_page_body",
        page_id=page_id,
        content=content,
        expected_title=expected_title,
        expected_section_id=expected_section_id,
        expected_modified=expected_modified,
        content_format=content_format,
    )


async def delete_page_content_object(
    page_id: str,
    page_content_object_id: str,
    expected_title: str,
    expected_section_id: str,
    expected_modified: str | None = None,
) -> dict[str, Any]:
    """With Deletes, remove one exact verified PageContentObject; never delete the Page."""

    return invoke(
        "delete_page_content_object",
        page_id=page_id,
        page_content_object_id=page_content_object_id,
        expected_title=expected_title,
        expected_section_id=expected_section_id,
        expected_modified=expected_modified,
    )


async def delete_section_group(
    section_group_id: str | None = None,
    expected_name: str | None = None,
    expected_parent_id: str | None = None,
    expected_modified: str | None = None,
    items: SectionGroupDeleteItems | None = None,
) -> dict[str, Any]:
    """With Deletes, non-permanently delete one exact SectionGroup or up to 20 non-overlapping same-Notebook SectionGroups; modes are mutually exclusive."""

    return invoke(
        "delete_section_group",
        section_group_id=section_group_id,
        expected_name=expected_name,
        expected_parent_id=expected_parent_id,
        expected_modified=expected_modified,
        items=_dump(items),
    )


async def delete_section(
    section_id: str | None = None,
    expected_name: str | None = None,
    expected_parent_id: str | None = None,
    expected_modified: str | None = None,
    items: SectionDeleteItems | None = None,
) -> dict[str, Any]:
    """With Deletes, non-permanently delete one exact Section or up to 20 same-Notebook Sections; modes are mutually exclusive."""

    return invoke(
        "delete_section",
        section_id=section_id,
        expected_name=expected_name,
        expected_parent_id=expected_parent_id,
        expected_modified=expected_modified,
        items=_dump(items),
    )


async def delete_page(
    page_id: str | None = None,
    expected_title: str | None = None,
    expected_section_id: str | None = None,
    expected_modified: str | None = None,
    items: PageDeleteItems | None = None,
) -> dict[str, Any]:
    """With Deletes, non-permanently delete one exact Page or up to 20 non-overlapping same-Notebook Pages; modes are mutually exclusive."""

    return invoke(
        "delete_page",
        page_id=page_id,
        expected_title=expected_title,
        expected_section_id=expected_section_id,
        expected_modified=expected_modified,
        items=_dump(items),
    )


TOOLS = [
    create_notebook,
    create_section_group,
    create_section,
    create_page,
    rename_page,
    rename_section_group,
    rename_section,
    reorder_page,
    reorder_section,
    sort_children,
    reparent_page,
    reparent_section,
    reparent_section_group,
    append_page_content,
    add_page_image_from_file,
    replace_page_body,
    delete_page_content_object,
    delete_page,
    delete_section,
    delete_section_group,
]
