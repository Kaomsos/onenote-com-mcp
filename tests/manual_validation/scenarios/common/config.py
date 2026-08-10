"""Shared static command policies, fixture data, and tool allowlists."""

ISOLATED_SCENARIO_NOTEBOOK_PREFIX = "__LOCAL_MCP_TEST_ISOLATED__"
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
RELAXED_COPY_CAPABILITIES = {"List", "Tag"}

READ_TOOLS = {
    "health_check",
    "list_notebooks",
    "get_notebook",
    "list_section_groups",
    "list_sections",
    "list_pages",
    "get_tree",
    "get_page_xml",
    "get_page_objects",
}
CREATE_TOOLS = READ_TOOLS | {
    "add_image_to_page",
    "append_to_page",
    "create_notebook",
    "create_section_group",
    "create_section",
    "create_page",
    "reorder_page",
}
RENAME_TOOLS = READ_TOOLS | {"rename_section_group", "rename_section"}
REORDER_PAGE_TOOLS = READ_TOOLS | {"get_page_text", "reorder_page"}
REORDER_SECTION_TOOLS = READ_TOOLS | {
    "create_section_group",
    "create_section",
    "create_page",
    "get_page_text",
    "reorder_section",
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
    "add_image_to_page",
    "append_to_page",
    "create_section",
    "create_page",
    "update_hierarchy_xml",
}
REPARENT_SECTION_GROUP_TOOLS = READ_TOOLS | {
    "create_section_group",
    "create_section",
    "create_page",
    "update_hierarchy_xml",
}
COPY_CLEANUP_TOOLS = {"delete_section_group", "delete_section", "delete_page"}
DELETE_TOOLS = READ_TOOLS | COPY_CLEANUP_TOOLS
COPY_TOOLS = READ_TOOLS | {
    "plan_copy",
    "copy_page",
    "copy_section",
    "copy_section_group",
    "delete_page",
    "delete_section",
    "delete_section_group",
}
COPY_PRESERVE_TOOLS = COPY_TOOLS - COPY_CLEANUP_TOOLS
COPY_NOTEBOOK_TOOLS = READ_TOOLS | {"plan_copy", "copy_notebook", "close_notebook"}
COPY_NOTEBOOK_PRESERVE_TOOLS = COPY_NOTEBOOK_TOOLS - {"close_notebook"}
MOVE_PAGE_TOOLS = READ_TOOLS | {
    "plan_move_page",
    "move_page",
}
