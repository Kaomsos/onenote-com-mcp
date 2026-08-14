"""Typed hierarchy read/query MCP tools."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from .context import get_services
from .responses import invoke


class RootQueryScope(BaseModel):
    """Query below every currently open Notebook at the live OneNote root."""

    model_config = ConfigDict(extra="forbid")
    mode: Annotated[
        Literal["root"],
        Field(description="Use the live OneNote root across all currently open Notebooks."),
    ]


class StartNodeQueryScope(BaseModel):
    """Query below one exact, validated hierarchy container ID."""

    model_config = ConfigDict(extra="forbid")
    mode: Annotated[
        Literal["start_node"],
        Field(description="Use exactly one validated native hierarchy start node."),
    ]
    start_node_id: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1),
        Field(description="Exact OneNote COM ID of the single native query start node."),
    ]


QueryScope = Annotated[
    RootQueryScope | StartNodeQueryScope,
    Field(
        discriminator="mode",
        description="Required native scope: all open Notebooks or one exact allowed container ID.",
    ),
]

NameEquals = Annotated[
    str,
    Field(description="Case-insensitive exact metadata name match; empty disables this filter."),
]
NameContains = Annotated[
    str,
    Field(description="Case-insensitive metadata name substring; empty disables this filter."),
]
ModifiedAfter = Annotated[
    str,
    StringConstraints(
        pattern=r"^$|^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})$"
    ),
    Field(description="Strict modified-time lower bound as RFC 3339 with an explicit offset or Z."),
]
ModifiedBefore = Annotated[
    str,
    StringConstraints(
        pattern=r"^$|^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})$"
    ),
    Field(description="Strict modified-time upper bound as RFC 3339 with an explicit offset or Z."),
]
OptionalExactId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, pattern=r"^$|.*\S.*"),
]
QueryOffset = Annotated[
    int,
    Field(ge=0, description="Zero-based offset applied after all metadata filtering."),
]
QueryPageSize = Annotated[
    int,
    Field(
        ge=1,
        le=200,
        description=(
            "Maximum matches returned after offset; this does not reduce GetHierarchy retrieval "
            "or metadata scanning."
        ),
    ),
]


async def list_hierarchy(
    start_identifier: str = "",
    scope: str = "pages",
    include_xml: bool = False,
    include_recycle_bin: bool = False,
) -> dict[str, Any]:
    """List live typed OneNote hierarchy objects."""

    return invoke(
        lambda: get_services().hierarchy.list_hierarchy(
            start_identifier, scope, include_xml, include_recycle_bin
        )
    )


async def list_notebooks(include_recycle_bin: bool = False) -> dict[str, Any]:
    """List live notebooks."""

    return invoke(lambda: get_services().hierarchy.list_notebooks(include_recycle_bin))


async def get_notebook(notebook_id: str) -> dict[str, Any]:
    """Get stable metadata for one Notebook by ID."""

    return invoke(lambda: {"item": get_services().hierarchy.resource(notebook_id, "notebook")})


async def list_section_groups(
    parent_id: str = "",
    recursive: bool = True,
    include_recycle_bin: bool = False,
) -> dict[str, Any]:
    """List SectionGroups, optionally below a typed parent ID."""

    return invoke(
        lambda: get_services().hierarchy.list_section_groups(parent_id, recursive, include_recycle_bin)
    )


async def get_section_group(section_group_id: str) -> dict[str, Any]:
    """Get stable metadata for one SectionGroup by ID."""

    return invoke(lambda: {"item": get_services().hierarchy.resource(section_group_id, "section_group")})


async def list_sections(
    parent_id: str = "",
    recursive: bool = True,
    include_recycle_bin: bool = False,
) -> dict[str, Any]:
    """List Sections, optionally below a typed parent ID."""

    return invoke(lambda: get_services().hierarchy.list_sections(parent_id, recursive, include_recycle_bin))


async def get_section(section_id: str) -> dict[str, Any]:
    """Get stable metadata for one Section by ID."""

    return invoke(lambda: {"item": get_services().hierarchy.resource(section_id, "section")})


async def list_pages(section_id: str, include_recycle_bin: bool = False) -> dict[str, Any]:
    """List Page metadata in one Section."""

    return invoke(lambda: get_services().hierarchy.list_pages(section_id, include_recycle_bin))


async def query_notebook(
    name_equals: NameEquals = "",
    name_contains: NameContains = "",
    modified_after: ModifiedAfter = "",
    modified_before: ModifiedBefore = "",
    offset: QueryOffset = 0,
    page_size: QueryPageSize = 200,
) -> dict[str, Any]:
    """Find open Notebook hierarchy metadata at the live root; this never reads Page body text. offset/page_size paginate after filtering and do not reduce GetHierarchy retrieval or metadata scanning."""

    return invoke(
        lambda: get_services().hierarchy.metadata_query(
            "notebook",
            name_equals=name_equals,
            name_contains=name_contains,
            modified_after=modified_after,
            modified_before=modified_before,
            offset=offset,
            page_size=page_size,
        )
    )


async def query_section_group(
    scope: QueryScope,
    name_equals: NameEquals = "",
    name_contains: NameContains = "",
    parent_id: Annotated[
        OptionalExactId,
        Field(description="Exact direct Notebook or SectionGroup parent ID within scope."),
    ] = "",
    modified_after: ModifiedAfter = "",
    modified_before: ModifiedBefore = "",
    include_recycle_bin: Annotated[
        bool,
        Field(description="Include provable recycle-bin descendants without expanding scope."),
    ] = False,
    offset: QueryOffset = 0,
    page_size: QueryPageSize = 200,
) -> dict[str, Any]:
    """Find SectionGroup hierarchy metadata below open Notebooks or one exact Notebook/SectionGroup ID; this never reads Page body text. offset/page_size paginate after filtering and do not reduce GetHierarchy retrieval or metadata scanning."""

    return invoke(
        lambda: get_services().hierarchy.metadata_query(
            "section_group",
            scope.model_dump(),
            name_equals=name_equals,
            name_contains=name_contains,
            parent_id=parent_id,
            modified_after=modified_after,
            modified_before=modified_before,
            include_recycle_bin=include_recycle_bin,
            offset=offset,
            page_size=page_size,
        )
    )


async def query_section(
    scope: QueryScope,
    name_equals: NameEquals = "",
    name_contains: NameContains = "",
    parent_id: Annotated[
        OptionalExactId,
        Field(description="Exact direct Notebook or SectionGroup parent ID within scope."),
    ] = "",
    modified_after: ModifiedAfter = "",
    modified_before: ModifiedBefore = "",
    include_recycle_bin: Annotated[
        bool,
        Field(description="Include provable recycle-bin descendants without expanding scope."),
    ] = False,
    offset: QueryOffset = 0,
    page_size: QueryPageSize = 200,
) -> dict[str, Any]:
    """Find Section hierarchy metadata below open Notebooks or one exact Notebook/SectionGroup ID; parent_id is direct and Page body text is never read. offset/page_size paginate after filtering and do not reduce GetHierarchy retrieval or metadata scanning."""

    return invoke(
        lambda: get_services().hierarchy.metadata_query(
            "section",
            scope.model_dump(),
            name_equals=name_equals,
            name_contains=name_contains,
            parent_id=parent_id,
            modified_after=modified_after,
            modified_before=modified_before,
            include_recycle_bin=include_recycle_bin,
            offset=offset,
            page_size=page_size,
        )
    )


async def query_page(
    scope: QueryScope,
    title_equals: Annotated[
        str,
        Field(description="Case-insensitive exact hierarchy Page title; empty disables this filter."),
    ] = "",
    title_contains: Annotated[
        str,
        Field(description="Case-insensitive hierarchy Page title substring, not Page body text."),
    ] = "",
    section_id: Annotated[
        OptionalExactId,
        Field(description="Exact direct Section ID within the verified scope."),
    ] = "",
    parent_page_id: Annotated[
        OptionalExactId,
        Field(description="Exact direct indentation-parent Page ID derived within one Section."),
    ] = "",
    modified_after: ModifiedAfter = "",
    modified_before: ModifiedBefore = "",
    include_recycle_bin: Annotated[
        bool,
        Field(description="Include provable recycle-bin descendants without expanding scope."),
    ] = False,
    offset: QueryOffset = 0,
    page_size: QueryPageSize = 200,
) -> dict[str, Any]:
    """Find Page hierarchy metadata by title, Section, indentation parent, or modification time below open Notebooks or one exact Notebook/SectionGroup/Section ID; use search_pages for Page body text. offset/page_size paginate after filtering and do not reduce GetHierarchy retrieval or metadata scanning."""

    return invoke(
        lambda: get_services().hierarchy.metadata_query(
            "page",
            scope.model_dump(),
            name_equals=title_equals,
            name_contains=title_contains,
            section_id=section_id,
            parent_page_id=parent_page_id,
            modified_after=modified_after,
            modified_before=modified_before,
            include_recycle_bin=include_recycle_bin,
            offset=offset,
            page_size=page_size,
        )
    )


async def get_path(object_id: str) -> dict[str, Any]:
    """Get a display path and stable ancestor IDs."""

    return invoke(lambda: get_services().hierarchy.path(object_id))


async def get_tree(root_id: str, max_depth: int = 8, include_recycle_bin: bool = False) -> dict[str, Any]:
    """Get a typed hierarchy and Page indentation tree."""

    return invoke(lambda: get_services().hierarchy.tree(root_id, max_depth, include_recycle_bin))


TOOLS = [
    list_hierarchy,
    list_notebooks,
    get_notebook,
    list_section_groups,
    get_section_group,
    list_sections,
    get_section,
    list_pages,
    query_notebook,
    query_section_group,
    query_section,
    query_page,
    get_path,
    get_tree,
]
