"""Shared static command policies, fixture data, and tool allowlists."""

# Compatibility view for older imports; runtime names are built centrally.
ISOLATED_SCENARIO_NOTEBOOK_PREFIX = "__"
COPY_FIXTURE_MARKER = "LOCAL_ONENOTE_MCP_COPY_FIXTURE_V1"
REPARENT_PAGE_FIXTURE_MARKER = "LOCAL_ONENOTE_MCP_REPARENT_PAGE_FIXTURE_V1"
COPY_FIXTURE_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
AUTOMATED_COPY_CAPABILITIES = {
    "Image",
    "List",
    "Outline",
    "RichText",
    "Table",
    "Tag",
}
VALIDATED_COPY_CAPABILITIES = AUTOMATED_COPY_CAPABILITIES | {
    "DisplayEquation",
    "InkDrawing",
    "InsertedFile",
    "MediaFile",
    "UIShape",
}
ROOT_PAGE_COPY_CAPABILITIES = {"Image", "Outline", "RichText", "Table"}
RELAXED_COPY_CAPABILITIES = {"List", "Tag"}

READ_TOOLS = {
    "health_check",
    "list_notebooks",
    "get_notebook_metadata",
    "expand_notebook",
    "expand_section_group",
    "expand_section",
    "expand_page",
    "expand_hierarchy",
    "get_page_xml",
    "get_page_content_objects",
    "query_notebook",
    "query_section_group",
    "query_section",
    "query_page",
}
CREATE_TOOLS = READ_TOOLS | {
    "add_page_image_from_file",
    "append_page_content",
    "create_notebook",
    "create_section_group",
    "create_section",
    "create_page",
    "reorder_page",
}
RENAME_TOOLS = READ_TOOLS | {"rename_page", "rename_section_group", "rename_section"}
REORDER_PAGE_TOOLS = READ_TOOLS | {"get_page_text", "reorder_page", "sort_children"}
REORDER_SECTION_TOOLS = READ_TOOLS | {
    "create_section_group",
    "create_section",
    "create_page",
    "get_page_text",
    "reorder_section",
    "sort_children",
}
REORDER_SECTION_GROUP_TOOLS = READ_TOOLS | {
    "create_section_group",
    "create_section",
    "create_page",
    "get_page_text",
    "reorder_section_group",
}
REPARENT_SECTION_TOOLS = READ_TOOLS | {"reparent_section"}
REPARENT_PAGE_TOOLS = READ_TOOLS | {
    "add_page_image_from_file",
    "append_page_content",
    "create_section",
    "create_page",
    "get_page_text",
    "reparent_page",
}
REPARENT_SECTION_GROUP_TOOLS = READ_TOOLS | {
    "create_section_group",
    "create_section",
    "create_page",
    "reparent_section_group",
}
COPY_CLEANUP_TOOLS = {"delete_section_group", "delete_section", "delete_page"}
DELETE_TOOLS = READ_TOOLS | COPY_CLEANUP_TOOLS
COPY_TOOLS = READ_TOOLS | {
    "copy_page",
    "copy_section",
    "copy_section_group",
    "delete_page",
    "delete_section",
    "delete_section_group",
}
COPY_PRESERVE_TOOLS = COPY_TOOLS - COPY_CLEANUP_TOOLS
COPY_PAGE_TOOLS = READ_TOOLS | {"copy_page", "delete_page"}
COPY_PAGE_PRESERVE_TOOLS = COPY_PAGE_TOOLS - {"delete_page"}
COPY_NOTEBOOK_TOOLS = READ_TOOLS | {"copy_notebook", "close_notebook"}
COPY_NOTEBOOK_PRESERVE_TOOLS = COPY_NOTEBOOK_TOOLS - {"close_notebook"}
MOVE_PAGE_TOOLS = READ_TOOLS | {
    "move_page",
}
MOVE_PAGE_DATETIME_DRIFT_NEGATIVE_TOOLS = MOVE_PAGE_TOOLS | {
    "read_verified_page_datetime",
    "set_verified_page_datetime",
}
MOVE_SECTION_TOOLS = READ_TOOLS | {
    "move_section",
}
MOVE_SECTION_GROUP_TOOLS = READ_TOOLS | {
    "move_section_group",
}
TIMESTAMP_FIDELITY_TOOLS = READ_TOOLS | {
    "read_verified_page_datetime",
    "create_section",
    "create_page",
    "set_verified_page_datetime",
}
