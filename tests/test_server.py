import asyncio
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from local_onenote_mcp import operation_catalog, server
from local_onenote_mcp.desktop import OneNoteDesktopState
from local_onenote_mcp.onenote_errors import (
    OneNoteDesktopNotRunningError,
    OneNoteNotYetSynchronizedError,
)
from local_onenote_mcp.policy import SearchBudget
from local_onenote_mcp.services import PartialFailure
from local_onenote_mcp.tools.mutations import create_page, create_section, delete_page_content
from local_onenote_mcp.tools.operations import navigate_to, publish_object, sync_notebook
from local_onenote_mcp.tools.pages import RootSearchScope, StartNodeSearchScope, search_pages
from local_onenote_mcp.tools.system import health_check, resolve_identifier


def test_health_check_includes_runtime_diagnostics(monkeypatch):
    monkeypatch.setattr(
        operation_catalog,
        "require_onenote_desktop",
        lambda: OneNoteDesktopState(True, True),
    )
    monkeypatch.setattr(
        server.services.hierarchy,
        "resources",
        lambda include_recycle_bin=False: [
            {"resource_type": "notebook", "name": "NB", "path": "NB"},
            {"resource_type": "section", "name": "Sec", "path": "NB/Sec"},
        ],
    )

    result = asyncio.run(health_check())

    assert result["ok"] is True
    assert result["server"] == "local-onenote"
    assert result["identifier_resolution_order"] == ["id", "exact_path", "unique_name"]
    assert result["search_backend"] == "onenote_index"
    assert result["search_scope_modes"] == ["root", "start_node"]
    assert result["search_pagination"] == {
        "default_page_size": 200,
        "max_page_size": 200,
        "consistency": "live_index",
    }
    assert result["metadata_query"] == {
        "tools": [
            "query_notebook",
            "query_section_group",
            "query_section",
            "query_page",
        ],
        "scope_modes": ["root", "start_node"],
        "query_kind": "hierarchy_metadata",
        "pagination": {
            "default_page_size": 200,
            "max_page_size": 200,
            "consistency": "live_hierarchy",
        },
    }
    assert result["hierarchy_browsing"] == {
        "tools": [
            "list_notebooks",
            "expand_notebook",
            "expand_section_group",
            "expand_section",
            "expand_page",
            "expand_hierarchy",
        ],
        "tree_schema": "tree={item,children[]}",
        "max_tree_items": 10_000,
        "page_body_reads": False,
    }
    assert "search_default_backend" not in result
    assert "search_backends" not in result
    assert result["content_formats"] == ["plain", "html", "markdown"]
    assert result["operation_runtime"] == {
        "enabled": True,
        "registered_operations": 56,
        "default_operations": 56,
        "advanced_operations": 0,
        "content_free_audit": True,
    }
    assert result["copy_move"] == {
        "tools": [
            "copy_page",
            "copy_section",
            "copy_section_group",
            "copy_notebook",
            "move_page",
            "move_section",
            "move_section_group",
        ],
        "single_call": True,
        "public_planning_tools": False,
        "agent_managed_plan_state": False,
        "preview": {
            "available": False,
            "reason": "No public Preview capability is delivered in this release.",
        },
    }
    assert result["copy_budget"]["max_pages"] > 0
    assert result["python_executable"]
    assert result["module_path"].endswith("tools\\system.py") or result["module_path"].endswith("tools/system.py")
    assert result["onenote_desktop"] == {
        "process_running": True,
        "visible_window_present": True,
        "ready": True,
        "probe": "native_windows_process_and_visible_window",
    }


def test_health_check_fails_before_com_when_onenote_gui_is_absent(monkeypatch):
    monkeypatch.setattr(
        operation_catalog,
        "require_onenote_desktop",
        lambda: (_ for _ in ()).throw(
            OneNoteDesktopNotRunningError(
                "OneNote Desktop is not running with a visible GUI. Start OneNote and retry.",
                operation="health_preflight",
                details={
                    "onenote_desktop": {
                        "process_running": False,
                        "visible_window_present": False,
                        "ready": False,
                        "probe": "native_windows_process_and_visible_window",
                    },
                    "required_action": "start_onenote_desktop_and_retry",
                },
            )
        ),
    )
    monkeypatch.setattr(
        server.services.hierarchy,
        "resources",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("health preflight must not activate OneNote COM")
        ),
    )

    result = asyncio.run(health_check())

    assert result["ok"] is False
    assert result["complete"] is False
    assert result["code"] == "onenote_desktop_not_running"
    assert result["retryability"] == "after_user_action"
    assert result["operation"] == "health_preflight"
    assert result["required_action"] == "start_onenote_desktop_and_retry"
    assert result["onenote_desktop"]["ready"] is False


def test_resolve_identifier_returns_single_item(monkeypatch):
    expected = {"resource_type": "section", "id": "section-id", "path": "NB/Sec", "name": "Sec"}

    def fake_resolve(identifier, resource_type=None):
        assert identifier == "NB/Sec"
        assert resource_type == "section"
        return expected

    monkeypatch.setattr(server.services.hierarchy, "resolve", fake_resolve)

    result = asyncio.run(resolve_identifier("NB/Sec", "section"))

    assert result["ok"] is True
    assert result["item"] == expected
    assert result["identifier_resolution_order"] == ["id", "exact_path", "unique_name"]


def test_resolve_identifier_rejects_unknown_type():
    result = asyncio.run(resolve_identifier("NB/Sec", "folder"))

    assert result["ok"] is False
    assert "item_type must be empty or one of" in result["error"]


def test_without_recycle_bin_removes_container_and_children():
    items = [
        {"name": "Notebook", "path": "Notebook", "is_in_recycle_bin": False},
        {"name": "OneNote_RecycleBin", "path": "Notebook/OneNote_RecycleBin", "is_in_recycle_bin": True},
        {"name": "Deleted", "path": "Notebook/OneNote_RecycleBin/Deleted", "is_in_recycle_bin": True},
        {"name": "Active", "path": "Notebook/Section/Active", "is_in_recycle_bin": False},
    ]

    filtered = server.services.hierarchy.without_recycle_bin(items)

    assert filtered == [
        {"name": "Notebook", "path": "Notebook", "is_in_recycle_bin": False},
        {"name": "Active", "path": "Notebook/Section/Active", "is_in_recycle_bin": False},
    ]


def test_search_pages_forwards_strict_scope_and_pagination(monkeypatch):
    expected = {
        "pages": [{"resource_type": "page", "id": "page-id", "title": "Found"}],
        "count": 1,
        "search_backend": "onenote_index",
    }

    def fake_search(query, scope, offset, page_size, include_snippets, include_recycle_bin):
        assert query == "needle"
        assert scope == {"mode": "start_node", "start_node_id": "section-id"}
        assert offset == 2
        assert page_size == 3
        assert include_snippets is False
        assert include_recycle_bin is False
        return expected

    monkeypatch.setattr(server.services.search, "search", fake_search)

    result = asyncio.run(
        search_pages(
            "needle",
            StartNodeSearchScope(mode="start_node", start_node_id="section-id"),
            offset=2,
            page_size=3,
            include_snippets=False,
        )
    )

    assert result["ok"] is True
    assert result["pages"] == expected["pages"]
    assert result["search_backend"] == "onenote_index"


def test_local_search_rejects_candidate_overflow_before_page_reads(monkeypatch):
    monkeypatch.setattr(
        server.services.hierarchy,
        "resources",
        lambda include_recycle_bin=False: [
            {"resource_type": "section", "id": "section-id", "name": "S", "path": "NB/S"},
            {"resource_type": "page", "id": "p1", "title": "One", "path": "NB/S/One"},
            {"resource_type": "page", "id": "p2", "title": "Two", "path": "NB/S/Two"},
        ],
    )
    monkeypatch.setattr(
        server.services.pages,
        "xml",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Page content must not be read")),
    )
    budget = SearchBudget(
        max_pages=1,
        max_page_chars=100,
        max_total_chars=100,
        max_seconds=5,
        snippet_chars=40,
    )

    with pytest.raises(ValueError, match="candidate pages"):
        server.services.search.local_text_search("section-id", "needle", 10, False, budget)


def test_search_pages_schema_is_a_strict_discriminated_scope_union_with_bounded_pagination():
    schema = server.mcp._tool_manager._tools["search_pages"].parameters

    assert set(schema.get("required", [])) == {"query", "scope"}
    assert set(schema["properties"]) == {
        "query",
        "scope",
        "offset",
        "page_size",
        "include_snippets",
        "include_recycle_bin",
    }
    assert schema["properties"]["offset"] == {"default": 0, "minimum": 0, "title": "Offset", "type": "integer"}
    assert schema["properties"]["page_size"] == {
        "default": 200,
        "maximum": 200,
        "minimum": 1,
        "title": "Page Size",
        "type": "integer",
    }
    scope_schema = schema["properties"]["scope"]
    assert scope_schema["discriminator"]["propertyName"] == "mode"
    assert len(scope_schema["oneOf"]) == 2
    assert schema["$defs"]["RootSearchScope"]["additionalProperties"] is False
    assert schema["$defs"]["StartNodeSearchScope"]["additionalProperties"] is False
    for removed in ("backend", "scope_type", "scope_id", "max_results"):
        assert removed not in schema["properties"]


def test_search_scope_models_forbid_extra_fields_and_blank_start_ids():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        RootSearchScope(mode="root", start_node_id="section-id")
    with pytest.raises(ValidationError):
        StartNodeSearchScope(mode="start_node", start_node_id="   ")


def test_metadata_query_schemas_are_typed_strict_and_bounded():
    tools = server.mcp._tool_manager._tools
    query_names = {
        "query_notebook",
        "query_section_group",
        "query_section",
        "query_page",
    }

    assert query_names <= set(tools)
    assert "query_hierarchy" not in tools
    assert "global_query" not in tools
    notebook_schema = tools["query_notebook"].parameters
    assert "scope" not in notebook_schema["properties"]
    assert "resource_type" not in notebook_schema["properties"]
    assert "include_recycle_bin" not in notebook_schema["properties"]

    for name in query_names:
        tool = tools[name]
        schema = tool.parameters
        properties = schema["properties"]
        assert "resource_type" not in properties
        assert properties["offset"]["default"] == 0
        assert properties["offset"]["minimum"] == 0
        assert properties["page_size"]["default"] == 200
        assert properties["page_size"]["minimum"] == 1
        assert properties["page_size"]["maximum"] == 200
        assert "pattern" in properties["modified_after"]
        assert "pattern" in properties["modified_before"]
        description = tool.description.casefold()
        assert "hierarchy metadata" in description
        assert "page body text" in description
        assert "gethierarchy" in description

    for name in ("query_section_group", "query_section", "query_page"):
        schema = tools[name].parameters
        assert schema["required"] == ["scope"]
        scope = schema["properties"]["scope"]
        assert scope["discriminator"]["propertyName"] == "mode"
        assert len(scope["oneOf"]) == 2
        assert schema["$defs"]["RootQueryScope"]["additionalProperties"] is False
        start = schema["$defs"]["StartNodeQueryScope"]
        assert start["additionalProperties"] is False
        assert start["properties"]["mode"]["description"]
        assert start["properties"]["start_node_id"]["minLength"] == 1
        assert schema["$defs"]["RootQueryScope"]["properties"]["mode"]["description"]

    page_properties = tools["query_page"].parameters["properties"]
    assert {"title_equals", "title_contains", "section_id", "parent_page_id"} <= set(
        page_properties
    )
    assert "parent_id" not in page_properties
    assert page_properties["section_id"]["pattern"] == r"^$|.*\S.*"
    assert page_properties["parent_page_id"]["pattern"] == r"^$|.*\S.*"
    for name in ("query_section_group", "query_section"):
        assert tools[name].parameters["properties"]["parent_id"]["pattern"] == r"^$|.*\S.*"


def test_default_tool_profile_excludes_generic_raw_mutations():
    names = set(server.mcp._tool_manager._tools)

    assert len(names) == 56
    assert {
        "query_notebook",
        "query_section_group",
        "query_section",
        "query_page",
    } <= names
    assert "query_hierarchy" not in names
    assert "global_query" not in names
    assert {
        "list_notebooks",
        "expand_notebook",
        "expand_section_group",
        "expand_section",
        "expand_page",
        "expand_hierarchy",
    } <= names
    assert {
        "list_hierarchy",
        "list_section_groups",
        "list_sections",
        "list_pages",
        "get_tree",
    }.isdisjoint(names)
    assert {
        "reorder_section",
        "reparent_page",
        "reparent_section",
        "reparent_section_group",
    } <= names
    assert {
        "copy_page",
        "copy_section",
        "copy_section_group",
        "copy_notebook",
        "move_page",
        "move_section",
        "move_section_group",
    } <= names
    assert {
        "plan_copy",
        "plan_move_page",
        "plan_move_section",
        "plan_move_section_group",
    }.isdisjoint(names)
    assert "update_page_xml" not in names
    assert "update_hierarchy_xml" not in names
    assert "delete_hierarchy" not in names
    assert "merge_sections" not in names
    assert "reorder_section_group" not in names
    assert "plan_reconstructive_move_page" not in names
    assert "reconstructive_move_page" not in names


def test_raw_xml_switch_does_not_create_a_production_advanced_profile(monkeypatch):
    from local_onenote_mcp.bridge import POWERSHELL_BRIDGE
    from local_onenote_mcp.tools import register_tools
    from local_onenote_mcp.tools.advanced import TOOLS as ADVANCED_TOOLS

    assert ADVANCED_TOOLS == []
    assert not hasattr(server.services.mutations, "update_hierarchy_xml")
    assert '"update_hierarchy"' in POWERSHELL_BRIDGE

    class FakeMCP:
        def __init__(self):
            self.names = []

        def tool(self):
            def register(function):
                self.names.append(function.__name__)
                return function

            return register

    fake = FakeMCP()
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_RAW_XML", "true")
    register_tools(fake, server.services)
    assert len(fake.names) == 56
    assert {
        "find_meta",
        "open_hierarchy",
        "update_page_xml",
        "update_hierarchy_xml",
        "merge_sections",
        "set_filing_location",
    }.isdisjoint(fake.names)
    assert "move_section" in fake.names


def test_reparent_tool_schemas_require_exact_typed_confirmation():
    tools = server.mcp._tool_manager._tools
    assert set(tools["reparent_page"].parameters.get("required", [])) == {
        "page_id",
        "destination_section_id",
        "expected_title",
        "expected_section_id",
    }
    assert set(tools["reparent_section"].parameters.get("required", [])) == {
        "section_id",
        "destination_parent_id",
        "expected_name",
        "expected_parent_id",
    }
    assert set(tools["reparent_section_group"].parameters.get("required", [])) == {
        "section_group_id",
        "destination_parent_id",
        "expected_name",
        "expected_parent_id",
    }
    for name in ("reparent_page", "reparent_section", "reparent_section_group"):
        assert "xml" not in tools[name].parameters["properties"]
        assert "force" not in tools[name].parameters["properties"]
    assert tools["reparent_page"].parameters["properties"]["include_descendants"] == {
        "default": False,
        "title": "Include Descendants",
        "type": "boolean",
    }
    for name in ("reparent_section", "reparent_section_group"):
        assert "include_descendants" not in tools[name].parameters["properties"]
    for name in ("reparent_page", "reparent_section", "reparent_section_group"):
        description = tools[name].description.casefold()
        assert "position" in description
        assert "observed" in description


def test_copy_tool_public_schemas_are_single_call_and_require_exact_confirmation():
    tools = server.mcp._tool_manager._tools
    expected_required = {
        "copy_page": {
            "page_id",
            "destination_section_id",
            "expected_title",
            "expected_section_id",
        },
        "copy_section": {
            "section_id",
            "destination_parent_id",
            "expected_name",
            "expected_parent_id",
        },
        "copy_section_group": {
            "section_group_id",
            "destination_parent_id",
            "expected_name",
            "expected_parent_id",
        },
        "copy_notebook": {"notebook_id", "expected_name"},
        "move_page": {
            "page_id",
            "destination_section_id",
            "expected_title",
            "expected_section_id",
        },
        "move_section": {
            "section_id",
            "destination_parent_id",
            "expected_name",
            "expected_parent_id",
        },
        "move_section_group": {
            "section_group_id",
            "destination_parent_id",
            "expected_name",
            "expected_parent_id",
        },
    }

    for name, required in expected_required.items():
        assert set(tools[name].parameters.get("required", [])) == required
    assert "destination_parent_id" not in tools["copy_notebook"].parameters["properties"]
    assert tools["copy_page"].parameters["properties"]["include_descendants"]["default"] is False
    assert tools["move_page"].parameters["properties"]["include_descendants"]["default"] is False
    for name in ("copy_section", "copy_section_group", "copy_notebook"):
        assert "include_descendants" not in tools[name].parameters["properties"]
    for name in (
        "copy_page",
        "copy_section",
        "copy_section_group",
        "copy_notebook",
        "move_page",
        "move_section",
        "move_section_group",
    ):
        assert "position" in tools[name].description.casefold()
    for name in expected_required:
        properties = tools[name].parameters["properties"]
        assert "plan_digest" not in properties
        assert "operation_id" not in properties
        assert "token" not in properties


def test_container_reorder_tool_schemas_require_exact_confirmation():
    tools = server.mcp._tool_manager._tools

    assert set(tools["reorder_section"].parameters.get("required", [])) == {
        "section_id",
        "expected_name",
        "expected_parent_id",
    }
    assert tools["reorder_section"].parameters["properties"]["after_section_id"]["default"] == ""
    assert "reorder_section_group" not in tools


def test_page_content_digest_ignores_page_clock_and_hierarchy_metadata():
    first = '<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="p" name="Old" lastModifiedTime="1"><one:Outline objectID="o"><one:OE author="Old" creationTime="1" /></one:Outline></one:Page>'
    second = '<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="p" name="New" lastModifiedTime="2"><one:Outline objectID="o"><one:OE author="New" creationTime="2" selected="all" /></one:Outline></one:Page>'
    changed_object = second.replace('objectID="o"', 'objectID="changed"')

    assert server.services.pages.digest(first) == server.services.pages.digest(second)
    assert server.services.pages.digest(first) != server.services.pages.digest(changed_object)


def test_page_content_digest_ignores_only_empty_selection_text_placeholders():
    baseline = '<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="p"><one:Outline objectID="o"><one:OE objectID="oe" /></one:Outline></one:Page>'
    selected_placeholder = baseline.replace(
        '<one:OE objectID="oe" />',
        '<one:OE objectID="oe"><one:T selected="all" /></one:OE>',
    )
    ordinary_empty_text = baseline.replace(
        '<one:OE objectID="oe" />',
        '<one:OE objectID="oe"><one:T /></one:OE>',
    )
    selected_visible_text = baseline.replace(
        '<one:OE objectID="oe" />',
        '<one:OE objectID="oe"><one:T selected="all">visible</one:T></one:OE>',
    )

    assert server.services.pages.digest(baseline) == server.services.pages.digest(
        selected_placeholder
    )
    assert server.services.pages.digest(baseline) != server.services.pages.digest(
        ordinary_empty_text
    )
    assert server.services.pages.digest(baseline) != server.services.pages.digest(
        selected_visible_text
    )


def test_hierarchy_browsing_schemas_are_exact_and_typed():
    tools = server.mcp._tool_manager._tools
    assert tools["list_notebooks"].parameters.get("properties", {}) == {}
    assert tools["list_notebooks"].parameters.get("required", []) == []
    for name in (
        "expand_notebook",
        "expand_section_group",
        "expand_section",
        "expand_page",
    ):
        schema = tools[name].parameters
        assert set(schema["properties"]) == {"id"}
        assert schema["required"] == ["id"]
        assert schema["properties"]["id"]["minLength"] == 1
    hierarchy_schema = tools["expand_hierarchy"].parameters
    assert set(hierarchy_schema["properties"]) == {
        "root_id",
        "max_depth",
        "include_recycle_bin",
    }
    assert hierarchy_schema["required"] == ["root_id"]


def test_open_hierarchy_resolves_existing_friendly_path_without_bridge(monkeypatch):
    expected = {"resource_type": "section", "id": "section-id", "path": "Notebook/Group/Sec", "name": "Sec"}
    monkeypatch.setattr(server.services.hierarchy, "find_unique_path", lambda path, resource_type=None: expected)
    monkeypatch.setattr(
        server.services.mutations,
        "call",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Existing path must not call COM")),
    )

    result = server.services.mutations.open_hierarchy("Notebook/Group/Sec")

    assert result["object_id"] == "section-id"
    assert result["opened_existing"] is True


@pytest.mark.write_contract
def test_open_hierarchy_none_waits_for_two_live_identity_observations(monkeypatch):
    opened = {
        "resource_type": "section",
        "id": "opened-id",
        "path": "Notebook/Opened",
        "name": "Opened",
        "parent_id": None,
        "is_in_recycle_bin": False,
    }
    observations = iter([[], [opened], [opened]])
    calls = []
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setattr(server.services.hierarchy, "find_unique_path", lambda *_args: None)
    monkeypatch.setattr(
        server.services.hierarchy,
        "resolve",
        lambda *_args: (_ for _ in ()).throw(ValueError("No object found")),
    )
    monkeypatch.setattr(
        server.services.hierarchy,
        "resources",
        lambda include_recycle_bin=False: next(observations),
    )
    monkeypatch.setattr(
        server.services.mutations,
        "call",
        lambda operation, **_kwargs: calls.append(operation) or {"object_id": "opened-id"},
    )
    monkeypatch.setattr("local_onenote_mcp.services.hierarchy.time.sleep", lambda _seconds: None)

    result = server.services.mutations.open_hierarchy(
        "Notebook/Opened", create_type="none"
    )

    assert result["object_id"] == "opened-id"
    assert result["converged"] is True
    assert result["convergence"]["stable_observations"] == 2
    assert calls == ["open_hierarchy"]


@pytest.mark.write_contract
def test_publish_object_resolves_target_path_before_bridge(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        server.services.hierarchy,
        "resource",
        lambda object_id, resource_type=None: {"resource_type": "page", "id": object_id, "title": "Page"},
    )

    def fake_call(operation, **params):
        captured.update(operation=operation, params=params)
        Path(params["target_path"]).write_bytes(b"fake-pdf")
        return {"path": params["target_path"]}

    monkeypatch.setattr(server.services.operations, "call", fake_call)
    result = asyncio.run(publish_object("page-id", "exports/out.pdf", format="pdf", overwrite=True))

    expected = tmp_path / "exports" / "out.pdf"
    assert result["ok"] is True
    assert captured["operation"] == "publish"
    assert Path(captured["params"]["target_path"]) == expected
    assert expected.is_file()
    assert result["execution"]["backend_category"] == "filesystem"
    assert result["execution"]["observed_outcome"] == "filesystem_effect_completed"
    assert result["execution"]["backend_calls"] == 3


@pytest.mark.write_contract
def test_publish_object_fails_if_backend_does_not_create_exact_file(monkeypatch, tmp_path):
    monkeypatch.setattr(
        server.services.hierarchy,
        "resource",
        lambda object_id, resource_type=None: {
            "resource_type": "page",
            "id": object_id,
            "title": "Page",
        },
    )
    monkeypatch.setattr(
        server.services.operations,
        "call",
        lambda _operation, **params: {"path": params["target_path"]},
    )

    result = asyncio.run(
        publish_object(
            "page-id", str(tmp_path / "missing.pdf"), format="pdf", overwrite=False
        )
    )

    assert result["ok"] is False
    assert result["code"] == "backend_error"
    assert result["execution"]["kind"] == "filesystem_effect"
    assert result["execution"]["backend_calls"] == 3
    assert not (tmp_path / "missing.pdf").exists()


@pytest.mark.write_contract
def test_publish_object_rejects_mismatched_backend_path(monkeypatch, tmp_path):
    requested = tmp_path / "requested.pdf"
    other = tmp_path / "other.pdf"
    monkeypatch.setattr(
        server.services.hierarchy,
        "resource",
        lambda object_id, resource_type=None: {
            "resource_type": "page",
            "id": object_id,
            "title": "Page",
        },
    )

    def fake_call(_operation, **_params):
        requested.write_bytes(b"fake-pdf")
        return {"path": str(other)}

    monkeypatch.setattr(server.services.operations, "call", fake_call)

    result = asyncio.run(
        publish_object("page-id", str(requested), format="pdf", overwrite=False)
    )

    assert result["ok"] is False
    assert result["code"] == "backend_error"
    assert result["execution"]["backend_calls"] == 2


def test_sync_and_navigation_preserve_strategy_specific_public_semantics(monkeypatch):
    monkeypatch.setattr(
        server.services.hierarchy,
        "resource",
        lambda object_id, resource_type=None: {
            "resource_type": resource_type or "page",
            "id": object_id,
            "title": "Target",
        },
    )
    monkeypatch.setattr(
        server.services.operations,
        "call",
        lambda _operation, **_params: {},
    )

    synced = asyncio.run(sync_notebook("notebook-id"))
    navigated = asyncio.run(navigate_to("page-id"))

    assert synced["ok"] is True
    assert synced["complete"] is False
    assert synced["accepted"] is True
    assert synced["completion_observable"] is False
    assert synced["execution"]["kind"] == "lifecycle"
    assert (
        synced["execution"]["observed_outcome"]
        == "accepted_completion_unobservable"
    )
    assert navigated["ok"] is True
    assert navigated["navigated"] is True
    assert navigated["execution"]["kind"] == "ui_effect"
    assert navigated["execution"]["observed_outcome"] == "action_accepted"


@pytest.mark.write_contract
def test_create_section_returns_refreshed_current_section_id(monkeypatch):
    parent = {"resource_type": "section_group", "id": "group-id", "path": "Notebook/Group", "name": "Group"}
    refreshed = {"resource_type": "section", "id": "current-section-id", "path": "Notebook/Group/New Sec", "name": "New Sec"}
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setattr(server.services.hierarchy, "resource", lambda object_id, resource_type=None: parent)
    monkeypatch.setattr(server.services.hierarchy, "resources", lambda include_recycle_bin=False: [])
    monkeypatch.setattr(server.services.mutations, "call", lambda operation, **params: {"object_id": "stale-id"})
    monkeypatch.setattr(server.services.hierarchy, "wait_for_created", lambda *args, **kwargs: refreshed)

    result = asyncio.run(create_section("group-id", "New Sec"))

    assert result["ok"] is True
    assert result["section_id"] == "current-section-id"


def test_created_page_readback_prefers_allocated_id_over_duplicate_title_path(
    monkeypatch,
):
    duplicate_path = "Notebook/Section/Duplicate"
    existing = {
        "resource_type": "page",
        "id": "existing-page-id",
        "path": duplicate_path,
    }
    allocated = {
        "resource_type": "page",
        "id": "allocated-page-id",
        "path": duplicate_path,
    }
    monkeypatch.setattr(
        server.services.hierarchy,
        "resources",
        lambda include_recycle_bin=False: [existing, allocated],
    )

    result = server.services.hierarchy.wait_for_created(
        duplicate_path,
        "page",
        "allocated-page-id",
        retries=1,
        delay_seconds=0,
    )

    assert result == allocated


def test_created_page_readback_waits_for_allocated_id_to_stabilize(monkeypatch):
    allocated = {
        "resource_type": "page",
        "id": "allocated-page-id",
        "path": "Notebook/Section/New",
        "parent_id": "section-id",
        "is_in_recycle_bin": False,
    }
    snapshots = iter([[], [allocated], [allocated]])
    monkeypatch.setattr(
        server.services.hierarchy,
        "resources",
        lambda include_recycle_bin=False: next(snapshots),
    )

    result = server.services.hierarchy.wait_for_created(
        allocated["path"],
        "page",
        allocated["id"],
        expected_parent_id="section-id",
        validate_parent=True,
        before_ids=set(),
        retries=3,
        delay_seconds=0.01,
    )

    assert result == allocated
    assert server.services.hierarchy.last_convergence_summary()["attempts"] == 3
    assert server.services.hierarchy.last_convergence_summary()["stable_observations"] == 2


def test_created_page_readback_rejects_ambiguous_path_without_allocated_id(
    monkeypatch,
):
    duplicate_path = "Notebook/Section/Duplicate"
    candidates = [
        {"resource_type": "page", "id": page_id, "path": duplicate_path}
        for page_id in ("first-page-id", "second-page-id")
    ]
    monkeypatch.setattr(
        server.services.hierarchy,
        "resources",
        lambda include_recycle_bin=False: candidates,
    )

    result = server.services.hierarchy.wait_for_created(
        duplicate_path,
        "page",
        "missing-allocated-id",
        retries=1,
        delay_seconds=0,
    )

    assert result is None


def test_created_target_readback_accepts_only_one_fresh_path_remap(monkeypatch):
    path = "Notebook/Section/New"
    old = {"resource_type": "page", "id": "old", "path": "Notebook/Section/Old"}
    remapped = {
        "resource_type": "page",
        "id": "remapped",
        "path": path,
        "parent_id": "section-id",
        "is_in_recycle_bin": False,
    }
    monkeypatch.setattr(
        server.services.hierarchy,
        "resources",
        lambda include_recycle_bin=False: [old, remapped],
    )

    result = server.services.hierarchy.wait_for_created(
        path,
        "page",
        "missing-allocated-id",
        expected_parent_id="section-id",
        validate_parent=True,
        before_ids={"old"},
        retries=2,
        delay_seconds=0.01,
    )

    assert result == remapped
    assert server.services.hierarchy.last_convergence_summary()["identity_remap"] == {
        "missing-allocated-id": "remapped"
    }
    assert server.services.hierarchy.last_convergence_summary()["stable_observations"] == 2


@pytest.mark.parametrize(
    "candidate",
    [
        {
            "resource_type": "section",
            "id": "allocated",
            "path": "Notebook/Section/New",
            "parent_id": "notebook-id",
        },
        {
            "resource_type": "page",
            "id": "allocated",
            "path": "Notebook/Section/New",
            "parent_id": "wrong-section",
        },
        {
            "resource_type": "page",
            "id": "allocated",
            "path": "Notebook/Section/New",
            "parent_id": "section-id",
            "is_in_recycle_bin": True,
        },
    ],
)
def test_created_target_readback_rejects_wrong_type_parent_or_recycle_state(
    monkeypatch, candidate
):
    monkeypatch.setattr(
        server.services.hierarchy,
        "resources",
        lambda include_recycle_bin=False: [candidate],
    )

    assert (
        server.services.hierarchy.wait_for_created(
            "Notebook/Section/New",
            "page",
            "allocated",
            expected_parent_id="section-id",
            validate_parent=True,
            retries=1,
            delay_seconds=0,
        )
        is None
    )


@pytest.mark.write_contract
def test_create_page_twice_with_duplicate_title_returns_distinct_allocated_ids(monkeypatch):
    section = {
        "resource_type": "section",
        "id": "section-id",
        "path": "Notebook/Section",
        "name": "Section",
    }
    state = [section]
    allocated = []
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setattr(server.services.hierarchy, "resource", lambda *args, **kwargs: section)
    monkeypatch.setattr(
        server.services.hierarchy,
        "resources",
        lambda include_recycle_bin=False: [dict(item) for item in state],
    )

    def fake_call(operation, **params):
        if operation == "create_new_page":
            page_id = f"allocated-{len(allocated) + 1}"
            allocated.append(page_id)
            return {"page_id": page_id}
        if operation == "update_page_content":
            root = ET.fromstring(params["xml"])
            page_id = root.attrib["ID"]
            state.append(
                {
                    "resource_type": "page",
                    "id": page_id,
                    "title": "Duplicate",
                    "path": "Notebook/Section/Duplicate",
                    "parent_id": "section-id",
                    "section_id": "section-id",
                    "is_in_recycle_bin": False,
                }
            )
            return {"updated": True}
        raise AssertionError(operation)

    monkeypatch.setattr(server.services.mutations, "call", fake_call)

    first = asyncio.run(create_page("section-id", "Duplicate", content="first"))
    second = asyncio.run(create_page("section-id", "Duplicate", content="second"))

    assert first["ok"] is True and second["ok"] is True
    assert first["page_id"] == "allocated-1"
    assert second["page_id"] == "allocated-2"
    assert first["page_id"] != second["page_id"]


@pytest.mark.write_contract
def test_create_page_reports_allocated_id_when_initial_content_write_fails(monkeypatch):
    section = {
        "resource_type": "section",
        "id": "section-id",
        "path": "Notebook/Section",
        "name": "Section",
    }
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setattr(server.services.hierarchy, "resource", lambda *args, **kwargs: section)
    monkeypatch.setattr(server.services.hierarchy, "resources", lambda include_recycle_bin=False: [])

    def fake_call(operation, **params):
        if operation == "create_new_page":
            return {"page_id": "allocated-page-id"}
        raise RuntimeError("initial content failed")

    monkeypatch.setattr(server.services.mutations, "call", fake_call)

    result = asyncio.run(create_page("section-id", "Title"))

    assert result["ok"] is False
    assert result["code"] == "partial_failure"
    assert result["created_ids"] == ["allocated-page-id"]
    assert result["failed_step"] == "initialize_created_page"


@pytest.mark.write_contract
def test_create_page_rejects_preexisting_allocated_id_before_content_write(monkeypatch):
    section = {
        "resource_type": "section",
        "id": "section-id",
        "path": "Notebook/Section",
        "name": "Section",
    }
    existing = {
        "resource_type": "page",
        "id": "existing-page-id",
        "path": "Notebook/Section/Existing",
        "section_id": "section-id",
        "parent_id": "section-id",
    }
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setattr(server.services.hierarchy, "resource", lambda *args, **kwargs: section)
    monkeypatch.setattr(
        server.services.hierarchy,
        "resources",
        lambda include_recycle_bin=False: [section, existing],
    )

    def fake_call(operation, **params):
        assert operation == "create_new_page"
        return {"page_id": "existing-page-id"}

    monkeypatch.setattr(server.services.mutations, "call", fake_call)

    result = asyncio.run(create_page("section-id", "Duplicate"))

    assert result["ok"] is False
    assert result["allocated_ids"] == ["existing-page-id"]
    assert result["created_ids"] == []
    assert result["source_touched"] is False
    assert result["topology_touched"] is False
    assert result["manual_recovery_required"] is False


@pytest.mark.write_contract
@pytest.mark.parametrize("kind", ["notebook", "section_group", "section"])
def test_create_container_reports_allocated_id_when_readback_fails(monkeypatch, tmp_path, kind):
    parent = {
        "resource_type": "notebook",
        "id": "parent-id",
        "path": "Notebook",
        "name": "Notebook",
    }
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setattr(server.services.hierarchy, "resource", lambda *args, **kwargs: parent)
    monkeypatch.setattr(server.services.hierarchy, "resources", lambda include_recycle_bin=False: [])
    monkeypatch.setattr(
        server.services.mutations,
        "call",
        lambda operation, **params: {"object_id": f"allocated-{kind}-id"},
    )
    monkeypatch.setattr(server.services.hierarchy, "wait_for_created", lambda *args, **kwargs: None)

    with pytest.raises(PartialFailure) as caught:
        if kind == "notebook":
            server.services.mutations.create_notebook("Copy", str(tmp_path))
        elif kind == "section_group":
            server.services.mutations.create_section_group("parent-id", "Copy")
        else:
            server.services.mutations.create_section("parent-id", "Copy")

    assert caught.value.details["created_ids"] == [f"allocated-{kind}-id"]
    assert caught.value.details["failed_step"] == f"verify_created_{kind}"


@pytest.mark.write_contract
@pytest.mark.parametrize("kind", ["notebook", "section_group", "section"])
def test_create_container_accepts_only_one_fresh_path_remap(monkeypatch, tmp_path, kind):
    parent = {
        "resource_type": "notebook",
        "id": "parent-id",
        "path": "Notebook",
        "name": "Notebook",
        "parent_id": None,
    }
    candidate = {
        "resource_type": kind,
        "id": f"remapped-{kind}-id",
        "path": "Copy" if kind == "notebook" else "Notebook/Copy",
        "name": "Copy",
        "parent_id": None if kind == "notebook" else "parent-id",
        "is_in_recycle_bin": False,
    }
    state = {"called": False}
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setattr(server.services.hierarchy, "resource", lambda *args, **kwargs: parent)
    monkeypatch.setattr(
        server.services.hierarchy,
        "resources",
        lambda include_recycle_bin=False: [parent, candidate]
        if state["called"]
        else [parent],
    )

    def fake_call(operation, **params):
        state["called"] = True
        return {"object_id": f"stale-{kind}-id"}

    monkeypatch.setattr(server.services.mutations, "call", fake_call)

    if kind == "notebook":
        result = server.services.mutations.create_notebook("Copy", str(tmp_path))
        result_id = result["notebook_id"]
    elif kind == "section_group":
        result = server.services.mutations.create_section_group("parent-id", "Copy")
        result_id = result["section_group_id"]
    else:
        result = server.services.mutations.create_section("parent-id", "Copy")
        result_id = result["section_id"]

    assert result_id == candidate["id"]
    assert result["allocated_id"] == f"stale-{kind}-id"
    assert result["identity_remapped"] is True


@pytest.mark.write_contract
@pytest.mark.parametrize("kind", ["notebook", "section_group", "section"])
def test_create_container_rejects_preexisting_returned_id(monkeypatch, tmp_path, kind):
    parent = {
        "resource_type": "notebook",
        "id": "parent-id",
        "path": "Notebook",
        "name": "Notebook",
        "parent_id": None,
    }
    existing = {
        "resource_type": kind,
        "id": f"existing-{kind}-id",
        "path": "Copy" if kind == "notebook" else "Notebook/Copy",
        "name": "Copy",
        "parent_id": None if kind == "notebook" else "parent-id",
        "is_in_recycle_bin": False,
    }
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setattr(server.services.hierarchy, "resource", lambda *args, **kwargs: parent)
    monkeypatch.setattr(
        server.services.hierarchy,
        "resources",
        lambda include_recycle_bin=False: [parent, existing],
    )
    monkeypatch.setattr(
        server.services.mutations,
        "call",
        lambda operation, **params: {"object_id": existing["id"]},
    )
    monkeypatch.setattr("local_onenote_mcp.services.hierarchy.time.sleep", lambda seconds: None)

    with pytest.raises(PartialFailure) as caught:
        if kind == "notebook":
            server.services.mutations.create_notebook("Copy", str(tmp_path))
        elif kind == "section_group":
            server.services.mutations.create_section_group("parent-id", "Copy")
        else:
            server.services.mutations.create_section("parent-id", "Copy")

    assert caught.value.details["allocated_ids"] == [existing["id"]]
    assert caught.value.details["resolved_target_ids"] == []
    assert caught.value.details["created_ids"] == []


@pytest.mark.write_contract
@pytest.mark.parametrize("kind", ["notebook", "section_group", "section"])
def test_create_container_rejects_ambiguous_fresh_path_remap(monkeypatch, tmp_path, kind):
    parent = {
        "resource_type": "notebook",
        "id": "parent-id",
        "path": "Notebook",
        "name": "Notebook",
        "parent_id": None,
    }
    path = "Copy" if kind == "notebook" else "Notebook/Copy"
    candidates = [
        {
            "resource_type": kind,
            "id": f"remapped-{kind}-{index}",
            "path": path,
            "name": "Copy",
            "parent_id": None if kind == "notebook" else "parent-id",
            "is_in_recycle_bin": False,
        }
        for index in (1, 2)
    ]
    state = {"called": False}
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setattr(server.services.hierarchy, "resource", lambda *args, **kwargs: parent)
    monkeypatch.setattr(
        server.services.hierarchy,
        "resources",
        lambda include_recycle_bin=False: [parent, *candidates]
        if state["called"]
        else [parent],
    )

    def fake_call(operation, **params):
        state["called"] = True
        return {"object_id": f"missing-{kind}-id"}

    monkeypatch.setattr(server.services.mutations, "call", fake_call)
    monkeypatch.setattr("local_onenote_mcp.services.hierarchy.time.sleep", lambda seconds: None)

    with pytest.raises(PartialFailure) as caught:
        if kind == "notebook":
            server.services.mutations.create_notebook("Copy", str(tmp_path))
        elif kind == "section_group":
            server.services.mutations.create_section_group("parent-id", "Copy")
        else:
            server.services.mutations.create_section("parent-id", "Copy")

    assert caught.value.details["allocated_ids"] == [f"missing-{kind}-id"]
    assert caught.value.details["resolved_target_ids"] == []


@pytest.mark.write_contract
def test_delete_page_content_rejects_non_deletable_child_with_parent_suggestion(monkeypatch):
    page_xml = """<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="p">
    <one:Outline objectID="outline-id"><one:OEChildren><one:OE objectID="oe-id"><one:T>hello</one:T>
    </one:OE></one:OEChildren></one:Outline></one:Page>"""
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    monkeypatch.setattr(server.services.pages, "confirm", lambda *args, **kwargs: {})
    monkeypatch.setattr(server.services.pages, "xml", lambda *args, **kwargs: page_xml)

    result = asyncio.run(delete_page_content("page-id", "oe-id", "Page", "section-id"))

    assert result["ok"] is False
    assert "not directly deletable" in result["error"]
    assert "outline-id" in result["error"]


@pytest.mark.write_contract
def test_delete_page_content_accepts_removal_of_target_descendant_closure(monkeypatch):
    before_xml = """<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="page-id">
    <one:Outline objectID="target-id"><one:OEChildren><one:OE objectID="target-oe"><one:T>target</one:T>
    <one:Image objectID="target-image" /></one:OE></one:OEChildren></one:Outline>
    <one:Outline objectID="other-id"><one:OEChildren><one:OE objectID="other-oe"><one:T>other</one:T></one:OE></one:OEChildren></one:Outline>
    </one:Page>"""
    after_xml = """<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="page-id">
    <one:Outline objectID="other-id"><one:OEChildren><one:OE objectID="other-oe"><one:T>other</one:T></one:OE></one:OEChildren></one:Outline>
    </one:Page>"""
    reads = iter([before_xml, after_xml, after_xml, after_xml])
    calls = 0

    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    monkeypatch.setattr(server.services.pages, "confirm", lambda *args, **kwargs: {})
    monkeypatch.setattr(server.services.pages, "xml", lambda *args, **kwargs: next(reads))
    monkeypatch.setattr(
        server.services.hierarchy,
        "resource",
        lambda *_args, **_kwargs: {
            "id": "page-id",
            "resource_type": "page",
            "title": "Page",
            "section_id": "section-id",
        },
    )
    monkeypatch.setattr(
        "local_onenote_mcp.services.mutations.time.sleep", lambda _seconds: None
    )

    def delete_once(_operation, **_kwargs):
        nonlocal calls
        calls += 1
        return {"deleted": True}

    monkeypatch.setattr(server.services.mutations, "call", delete_once)

    result = asyncio.run(
        delete_page_content("page-id", "target-id", "Page", "section-id")
    )

    assert calls == 1
    assert result["ok"] is True
    assert result["deleted"] is True
    assert result["reconciliation"]["state"] == "applied"
    assert result["reconciliation"]["mutation_attempts"] == 1
    assert result["reconciliation"]["mutation_replayed"] is False
    assert result["convergence"]["converged"] is True


@pytest.mark.write_contract
def test_delete_page_content_classifies_non_target_identity_change_as_partial_without_replay(
    monkeypatch,
):
    before_xml = """<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="page-id">
    <one:Outline objectID="target-id"><one:OEChildren><one:OE objectID="target-oe"><one:T>target</one:T></one:OE></one:OEChildren></one:Outline>
    <one:Outline objectID="other-id"><one:OEChildren><one:OE objectID="other-oe"><one:T>other</one:T></one:OE></one:OEChildren></one:Outline>
    </one:Page>"""
    changed_xml = """<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="page-id">
    <one:Outline objectID="target-id"><one:OEChildren><one:OE objectID="target-oe"><one:T>target</one:T></one:OE></one:OEChildren></one:Outline>
    </one:Page>"""
    reads = iter([before_xml, changed_xml])
    calls = 0

    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    monkeypatch.setattr(server.services.pages, "confirm", lambda *args, **kwargs: {})
    monkeypatch.setattr(server.services.pages, "xml", lambda *args, **kwargs: next(reads))
    monkeypatch.setattr(
        server.services.hierarchy,
        "resource",
        lambda *_args, **_kwargs: {
            "id": "page-id",
            "resource_type": "page",
            "title": "Page",
            "section_id": "section-id",
        },
    )

    def fail_once(_operation, **_kwargs):
        nonlocal calls
        calls += 1
        raise OneNoteNotYetSynchronizedError(
            "safe typed failure", operation="delete_page_content"
        )

    monkeypatch.setattr(server.services.mutations, "call", fail_once)

    result = asyncio.run(
        delete_page_content("page-id", "target-id", "Page", "section-id")
    )

    assert calls == 1
    assert result["ok"] is False
    assert result["code"] == "partial_failure"
    assert result["observed_outcome"] == "partially_applied"
    assert result["mutation_replayed"] is False
    assert result["retry_safety"] == "do_not_replay"


def test_generic_delete_hierarchy_is_removed_from_advanced_registration():
    from local_onenote_mcp.tools.advanced import TOOLS as ADVANCED_TOOLS

    assert ADVANCED_TOOLS == []
    assert not hasattr(server.services.mutations, "delete_hierarchy")


def test_open_hierarchy_rejects_duplicate_exact_path_before_bridge(monkeypatch):
    monkeypatch.setattr(
        server.services.hierarchy,
        "find_unique_path",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ValueError("Ambiguous page path 'Notebook/Section/Duplicate'. Use an exact object ID.")
        ),
    )
    monkeypatch.setattr(
        server.services.mutations,
        "call",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("COM must not be called")),
    )

    with pytest.raises(ValueError, match="Ambiguous"):
        server.services.mutations.open_hierarchy("Notebook/Section/Duplicate")


def test_internal_merge_and_filing_location_methods_use_exact_id_names():
    import inspect

    assert list(inspect.signature(server.services.operations.merge_sections).parameters) == [
        "source_section_id",
        "destination_section_id",
    ]
    assert list(
        inspect.signature(server.services.operations.set_filing_location).parameters
    ) == [
        "filing_location",
        "filing_location_type",
        "section_or_page_id",
    ]


@pytest.mark.write_contract
def test_internal_merge_and_filing_location_resolve_only_exact_ids(monkeypatch):
    items = {
        "source-section": {
            "resource_type": "section",
            "id": "source-section",
            "name": "Same",
        },
        "destination-section": {
            "resource_type": "section",
            "id": "destination-section",
            "name": "Same",
        },
        "page-id": {
            "resource_type": "page",
            "id": "page-id",
            "title": "Same",
        },
    }
    calls = []
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_RAW_XML", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setattr(
        server.services.hierarchy,
        "resolve",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("advanced mutation must not resolve a name or path")
        ),
    )
    monkeypatch.setattr(
        server.services.hierarchy,
        "resource",
        lambda object_id, resource_type=None: items[object_id]
        if resource_type is None or items[object_id]["resource_type"] == resource_type
        else (_ for _ in ()).throw(ValueError("wrong type")),
    )
    monkeypatch.setattr(
        server.services.operations,
        "call",
        lambda operation, **params: calls.append((operation, params)) or {},
    )

    merged = server.services.operations.merge_sections(
        "source-section", "destination-section"
    )
    filed = server.services.operations.set_filing_location(
        "email", "current_page", "page-id"
    )

    assert merged["merged"] is True
    assert filed["updated"] is True
    assert calls[0][1]["source_section_id"] == "source-section"
    assert calls[0][1]["destination_section_id"] == "destination-section"
    assert calls[1][1]["section_or_page_id"] == "page-id"


def test_typed_mutation_target_paths_have_no_name_or_created_path_fallback():
    import inspect

    service_type = type(server.services.mutations)
    methods = (
        service_type.update_page_title,
        service_type.append_to_page,
        service_type.replace_page_body,
        service_type.reorder_page,
        service_type._reorder_container,
        service_type._reparent,
        service_type.delete_resource,
        service_type.delete_page_content,
    )
    forbidden = (".find_path(", ".resolve(", ".wait_for_created(")
    for method in methods:
        source = inspect.getsource(method)
        assert not any(token in source for token in forbidden), method.__name__
