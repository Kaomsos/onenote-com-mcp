import asyncio
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from local_onenote_mcp import server
from local_onenote_mcp.policy import SearchBudget
from local_onenote_mcp.services import PartialFailure
from local_onenote_mcp.tools.advanced import merge_sections, open_hierarchy, set_filing_location
from local_onenote_mcp.tools.hierarchy import list_hierarchy
from local_onenote_mcp.tools.mutations import create_page, create_section, delete_page_content
from local_onenote_mcp.tools.operations import publish_object
from local_onenote_mcp.tools.pages import search_pages
from local_onenote_mcp.tools.system import health_check, resolve_identifier


def test_health_check_includes_runtime_diagnostics(monkeypatch):
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
    assert result["search_default_backend"] == "local_scan"
    assert result["search_backends"] == ["local_scan", "onenote_index"]
    assert result["search_scope_types"] == [
        "all_open_notebooks",
        "notebook",
        "section_group",
        "section",
    ]
    assert result["content_formats"] == ["plain", "html", "markdown"]
    assert result["copy_budget"]["max_pages"] > 0
    assert result["python_executable"]
    assert result["module_path"].endswith("tools\\system.py") or result["module_path"].endswith("tools/system.py")


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


def test_search_pages_uses_explicit_local_scan_without_index_fallback(monkeypatch):
    expected_scope = {"resource_type": "section", "id": "section-id", "name": "Sec"}
    monkeypatch.setattr(
        server.services.hierarchy,
        "resources",
        lambda include_recycle_bin=False: [expected_scope],
    )

    def fake_local_text_search(
        start_id,
        query,
        max_results,
        include_recycle_bin,
        budget=None,
        include_snippets=True,
        catalog=None,
        notebook_ids=None,
    ):
        assert start_id == "section-id"
        assert query == "needle"
        assert max_results == 3
        assert include_recycle_bin is False
        assert include_snippets is False
        assert catalog is not None
        assert notebook_ids is None
        return ([{"resource_type": "page", "id": "page-id", "title": "Found"}], {"scanned_pages": 1})

    monkeypatch.setattr(server.services.search, "local_text_search", fake_local_text_search)
    monkeypatch.setattr(
        server.services.search,
        "call",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("OneNote index must not be used")),
    )

    result = asyncio.run(
        search_pages(
            "needle",
            scope_type="section",
            scope_id="section-id",
            max_results=3,
            include_snippets=False,
        )
    )

    assert result["ok"] is True
    assert result["count"] == 1
    assert result["search_backend"] == "local_scan"


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


def test_search_pages_schema_allows_omitted_scope_id():
    schema = server.mcp._tool_manager._tools["search_pages"].parameters

    assert set(schema.get("required", [])) == {"query", "scope_type"}
    assert schema["properties"]["scope_id"]["default"] == ""


def test_default_tool_profile_excludes_generic_raw_mutations():
    names = set(server.mcp._tool_manager._tools)

    assert len(names) == 58
    assert {
        "reorder_section",
        "reorder_section_group",
        "reparent_page",
        "reparent_section",
        "reparent_section_group",
    } <= names
    assert {
        "plan_copy",
        "copy_page",
        "copy_section",
        "copy_section_group",
        "copy_notebook",
        "plan_move_page",
        "move_page",
        "plan_move_section",
        "move_section",
        "plan_move_section_group",
        "move_section_group",
    } <= names
    assert "update_page_xml" not in names
    assert "update_hierarchy_xml" not in names
    assert "delete_hierarchy" not in names
    assert "merge_sections" not in names
    assert "plan_reconstructive_move_page" not in names
    assert "reconstructive_move_page" not in names


def test_raw_hierarchy_xml_is_absent_from_advanced_and_every_registration_profile():
    from local_onenote_mcp.bridge import POWERSHELL_BRIDGE
    from local_onenote_mcp.tools import ADVANCED_TOOLS, register_tools

    assert "update_hierarchy_xml" not in {tool.__name__ for tool in ADVANCED_TOOLS}
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
    register_tools(fake, server.services, raw_xml_enabled=True)
    assert "update_page_xml" in fake.names
    assert "update_hierarchy_xml" not in fake.names
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


def test_copy_tool_public_schemas_require_exact_confirmation_and_plan_digest():
    tools = server.mcp._tool_manager._tools
    expected_required = {
        "plan_copy": {"source_id"},
        "copy_page": {
            "page_id",
            "destination_section_id",
            "expected_title",
            "expected_section_id",
            "plan_digest",
        },
        "copy_section": {
            "section_id",
            "destination_parent_id",
            "expected_name",
            "expected_parent_id",
            "plan_digest",
        },
        "copy_section_group": {
            "section_group_id",
            "destination_parent_id",
            "expected_name",
            "expected_parent_id",
            "plan_digest",
        },
        "copy_notebook": {"notebook_id", "expected_name", "plan_digest"},
        "plan_move_page": {"page_id", "destination_section_id"},
        "move_page": {
            "page_id",
            "destination_section_id",
            "expected_title",
            "expected_section_id",
            "plan_digest",
        },
        "plan_move_section": {"section_id", "destination_parent_id"},
        "move_section": {
            "section_id",
            "destination_parent_id",
            "expected_name",
            "expected_parent_id",
            "plan_digest",
        },
        "plan_move_section_group": {"section_group_id", "destination_parent_id"},
        "move_section_group": {
            "section_group_id",
            "destination_parent_id",
            "expected_name",
            "expected_parent_id",
            "plan_digest",
        },
    }

    for name, required in expected_required.items():
        assert set(tools[name].parameters.get("required", [])) == required
    assert "destination_parent_id" not in tools["copy_notebook"].parameters["properties"]
    assert tools["plan_copy"].parameters["properties"]["destination_base_folder"]["default"] == ""
    assert tools["plan_copy"].parameters["properties"]["include_descendants"]["default"] is False
    assert tools["copy_page"].parameters["properties"]["include_descendants"]["default"] is False
    assert tools["plan_move_page"].parameters["properties"]["include_descendants"]["default"] is False
    assert tools["move_page"].parameters["properties"]["include_descendants"]["default"] is False
    for name in ("copy_section", "copy_section_group", "copy_notebook"):
        assert "include_descendants" not in tools[name].parameters["properties"]


def test_container_reorder_tool_schemas_require_exact_confirmation():
    tools = server.mcp._tool_manager._tools

    assert set(tools["reorder_section"].parameters.get("required", [])) == {
        "section_id",
        "expected_name",
        "expected_parent_id",
    }
    assert set(tools["reorder_section_group"].parameters.get("required", [])) == {
        "section_group_id",
        "expected_name",
        "expected_parent_id",
    }
    assert tools["reorder_section"].parameters["properties"]["after_section_id"]["default"] == ""
    assert (
        tools["reorder_section_group"].parameters["properties"]["after_section_group_id"]["default"]
        == ""
    )


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


def test_list_hierarchy_children_returns_only_direct_typed_children(monkeypatch):
    xml = """<one:Notebooks xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote">
      <one:Notebook name="NB" ID="n"><one:SectionGroup name="G" ID="g"><one:Section name="S" ID="s" /></one:SectionGroup></one:Notebook>
    </one:Notebooks>"""
    monkeypatch.setattr(server.services.hierarchy, "hierarchy_xml", lambda start_id="", scope="pages": xml)

    result = asyncio.run(list_hierarchy("n", scope="children"))

    assert result["ok"] is True
    assert [item["id"] for item in result["items"]] == ["g"]
    assert result["items"][0]["resource_type"] == "section_group"


def test_open_hierarchy_resolves_existing_friendly_path_without_bridge(monkeypatch):
    expected = {"resource_type": "section", "id": "section-id", "path": "Notebook/Group/Sec", "name": "Sec"}
    monkeypatch.setattr(server.services.hierarchy, "find_unique_path", lambda path, resource_type=None: expected)
    monkeypatch.setattr(
        server.services.mutations,
        "call",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Existing path must not call COM")),
    )

    result = asyncio.run(open_hierarchy("Notebook/Group/Sec"))

    assert result["ok"] is True
    assert result["object_id"] == "section-id"
    assert result["opened_existing"] is True


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
        return {"path": params["target_path"]}

    monkeypatch.setattr(server.services.operations, "call", fake_call)
    result = asyncio.run(publish_object("page-id", "exports/out.pdf", format="pdf", overwrite=True))

    expected = tmp_path / "exports" / "out.pdf"
    assert result["ok"] is True
    assert captured["operation"] == "publish"
    assert Path(captured["params"]["target_path"]) == expected


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
        retries=1,
        delay_seconds=0,
    )

    assert result == remapped


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


def test_generic_delete_hierarchy_is_removed_from_advanced_registration():
    from local_onenote_mcp.tools import ADVANCED_TOOLS

    assert "delete_hierarchy" not in {tool.__name__ for tool in ADVANCED_TOOLS}
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

    result = asyncio.run(open_hierarchy("Notebook/Section/Duplicate"))

    assert result["ok"] is False
    assert "Ambiguous" in result["error"]


def test_advanced_merge_and_filing_location_schemas_use_exact_id_names():
    import inspect

    assert list(inspect.signature(merge_sections).parameters) == [
        "source_section_id",
        "destination_section_id",
    ]
    assert list(inspect.signature(set_filing_location).parameters) == [
        "filing_location",
        "filing_location_type",
        "section_or_page_id",
    ]


@pytest.mark.write_contract
def test_advanced_merge_and_filing_location_resolve_only_exact_ids(monkeypatch):
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

    merged = asyncio.run(merge_sections("source-section", "destination-section"))
    filed = asyncio.run(set_filing_location("email", "current_page", "page-id"))

    assert merged["ok"] is True
    assert filed["ok"] is True
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
