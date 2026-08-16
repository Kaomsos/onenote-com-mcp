"""Page content and Search MCP tools."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from ..settings import MAX_TEXT_CHARS
from .responses import invoke


class RootSearchScope(BaseModel):
    """Search every Notebook visible below the live OneNote root."""

    model_config = ConfigDict(extra="forbid")
    mode: Literal["root"]


class StartNodeSearchScope(BaseModel):
    """Search below one exact Notebook, SectionGroup, or Section ID."""

    model_config = ConfigDict(extra="forbid")
    mode: Literal["start_node"]
    start_node_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


SearchScope = Annotated[
    RootSearchScope | StartNodeSearchScope,
    Field(discriminator="mode"),
]


async def get_page_metadata(page_id: str) -> dict[str, Any]:
    """Get Page metadata only."""

    return invoke("get_page_metadata", page_id=page_id)


async def get_page_text(page_id: str, max_chars: int = MAX_TEXT_CHARS) -> dict[str, Any]:
    """Return visible text extracted from one Page."""

    return invoke("get_page_text", page_id=page_id, max_chars=max_chars)


async def get_page_content_objects(page_id: str) -> dict[str, Any]:
    """Return typed PageContentObjects for one exact Page."""

    return invoke("get_page_content_objects", page_id=page_id)


async def get_page_content_object_binary(
    page_id: str, page_content_object_id: str
) -> dict[str, Any]:
    """Read one exact PageContentObject binary after ownership validation and within the hard response budget."""

    return invoke(
        "get_page_content_object_binary",
        page_id=page_id,
        page_content_object_id=page_content_object_id,
    )


async def search_pages(
    query: str,
    scope: SearchScope,
    offset: Annotated[int, Field(ge=0)] = 0,
    page_size: Annotated[int, Field(ge=1, le=200)] = 200,
    include_snippets: bool = True,
    include_recycle_bin: bool = False,
) -> dict[str, Any]:
    """Search the live OneNote index below the root or one exact hierarchy node."""

    return invoke(
        "search_pages",
        query=query,
        scope=scope.model_dump(),
        offset=offset,
        page_size=page_size,
        include_snippets=include_snippets,
        include_recycle_bin=include_recycle_bin,
    )


TOOLS = [
    get_page_metadata,
    get_page_text,
    get_page_content_objects,
    get_page_content_object_binary,
    search_pages,
]
