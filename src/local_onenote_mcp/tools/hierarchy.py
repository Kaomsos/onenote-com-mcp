"""Typed hierarchy read/query MCP tools."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

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
ExactHierarchyId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
    Field(description="Exact OneNote COM hierarchy object ID."),
]


async def list_notebooks() -> dict[str, Any]:
    """List currently open Notebook metadata in stable hierarchy order."""

    return invoke("list_notebooks")


async def get_notebook(notebook_id: str) -> dict[str, Any]:
    """Get stable metadata for one Notebook by ID."""

    return invoke("get_notebook", notebook_id=notebook_id)


async def get_section_group(section_group_id: str) -> dict[str, Any]:
    """Get stable metadata for one SectionGroup by ID."""

    return invoke("get_section_group", section_group_id=section_group_id)


async def get_section(section_id: str) -> dict[str, Any]:
    """Get stable metadata for one Section by ID."""

    return invoke("get_section", section_id=section_id)


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
        "query_notebook",
        name_equals=name_equals,
        name_contains=name_contains,
        modified_after=modified_after,
        modified_before=modified_before,
        offset=offset,
        page_size=page_size,
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
        "query_section_group",
        scope=scope.model_dump(),
        name_equals=name_equals,
        name_contains=name_contains,
        parent_id=parent_id,
        modified_after=modified_after,
        modified_before=modified_before,
        include_recycle_bin=include_recycle_bin,
        offset=offset,
        page_size=page_size,
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
        "query_section",
        scope=scope.model_dump(),
        name_equals=name_equals,
        name_contains=name_contains,
        parent_id=parent_id,
        modified_after=modified_after,
        modified_before=modified_before,
        include_recycle_bin=include_recycle_bin,
        offset=offset,
        page_size=page_size,
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
        "query_page",
        scope=scope.model_dump(),
        title_equals=title_equals,
        title_contains=title_contains,
        section_id=section_id,
        parent_page_id=parent_page_id,
        modified_after=modified_after,
        modified_before=modified_before,
        include_recycle_bin=include_recycle_bin,
        offset=offset,
        page_size=page_size,
    )


async def get_path(object_id: str) -> dict[str, Any]:
    """Get a display path and stable ancestor IDs."""

    return invoke("get_path", object_id=object_id)


async def expand_notebook(id: ExactHierarchyId) -> dict[str, Any]:
    """Expand one exact open Notebook through nested SectionGroups to Section leaves."""

    return invoke("expand_notebook", id=id)


async def expand_section_group(id: ExactHierarchyId) -> dict[str, Any]:
    """Expand one exact SectionGroup through nested groups to Section leaves."""

    return invoke("expand_section_group", id=id)


async def expand_section(id: ExactHierarchyId) -> dict[str, Any]:
    """Expand one exact Section to its complete Page indentation tree."""

    return invoke("expand_section", id=id)


async def expand_page(id: ExactHierarchyId) -> dict[str, Any]:
    """Expand one exact Page to its complete indentation-descendant subtree."""

    return invoke("expand_page", id=id)


async def expand_hierarchy(
    root_id: ExactHierarchyId,
    max_depth: int = 8,
    include_recycle_bin: bool = False,
) -> dict[str, Any]:
    """Expand any exact hierarchy root to a numeric depth without reading Page body text."""

    return invoke(
        "expand_hierarchy",
        root_id=root_id,
        max_depth=max_depth,
        include_recycle_bin=include_recycle_bin,
    )


TOOLS = [
    list_notebooks,
    get_notebook,
    get_section_group,
    get_section,
    query_notebook,
    query_section_group,
    query_section,
    query_page,
    get_path,
    expand_notebook,
    expand_section_group,
    expand_section,
    expand_page,
    expand_hierarchy,
]
