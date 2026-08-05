import asyncio
from pathlib import Path

import pytest

from local_onenote_mcp import server
from local_onenote_mcp.policy import SearchBudget
from local_onenote_mcp.services import PartialFailure
from local_onenote_mcp.tools.advanced import delete_hierarchy, open_hierarchy
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
    monkeypatch.setattr(server.services.hierarchy, "resource", lambda object_id, resource_type=None: expected_scope)

    def fake_local_text_search(
        start_id, query, max_results, include_recycle_bin, budget=None, include_snippets=True
    ):
        assert start_id == "section-id"
        assert query == "needle"
        assert max_results == 3
        assert include_recycle_bin is False
        assert include_snippets is False
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


def test_default_tool_profile_excludes_generic_raw_mutations():
    names = set(server.mcp._tool_manager._tools)

    assert len(names) == 50
    assert {
        "plan_copy",
        "copy_page",
        "copy_section",
        "copy_section_group",
        "copy_notebook",
        "plan_reconstructive_move_page",
        "reconstructive_move_page",
    } <= names
    assert "update_page_xml" not in names
    assert "update_hierarchy_xml" not in names
    assert "delete_hierarchy" not in names
    assert "merge_sections" not in names


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
        "plan_reconstructive_move_page": {"page_id", "destination_section_id"},
        "reconstructive_move_page": {
            "page_id",
            "destination_section_id",
            "expected_title",
            "expected_section_id",
            "plan_digest",
        },
    }

    for name, required in expected_required.items():
        assert set(tools[name].parameters.get("required", [])) == required
    assert "destination_parent_id" not in tools["copy_notebook"].parameters["properties"]
    assert tools["plan_copy"].parameters["properties"]["destination_base_folder"]["default"] == ""


def test_page_content_digest_ignores_page_clock_and_hierarchy_metadata():
    first = '<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="p" name="Old" lastModifiedTime="1"><one:Outline objectID="o" /></one:Page>'
    second = '<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="p" name="New" lastModifiedTime="2"><one:Outline objectID="o" /></one:Page>'

    assert server.services.pages.digest(first) == server.services.pages.digest(second)


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
    monkeypatch.setattr(server.services.hierarchy, "find_path", lambda path, resource_type=None: expected)
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
    monkeypatch.setattr(server.services.mutations, "call", lambda operation, **params: {"object_id": "stale-id"})
    monkeypatch.setattr(server.services.hierarchy, "wait_for_created", lambda *args, **kwargs: refreshed)

    result = asyncio.run(create_section("group-id", "New Sec"))

    assert result["ok"] is True
    assert result["section_id"] == "current-section-id"


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
def test_delete_hierarchy_retries_when_same_path_reappears_with_new_id(monkeypatch):
    calls = []
    initial = {"resource_type": "section_group", "id": "old-id", "path": "Notebook/Test", "name": "Test"}
    remaining = {"resource_type": "section_group", "id": "new-id", "path": "Notebook/Test", "name": "Test"}
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_RAW_XML", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_PERMANENT_DELETES", "true")
    monkeypatch.setattr(server.services.hierarchy, "resolve", lambda identifier: initial)
    monkeypatch.setattr(
        server.services.hierarchy,
        "find_path",
        lambda path, resource_type=None: remaining if len(calls) == 1 else None,
    )
    monkeypatch.setattr(
        server.services.mutations,
        "call",
        lambda operation, **params: calls.append(params["object_id"]) or {"deleted": True},
    )
    monkeypatch.setattr("local_onenote_mcp.services.mutations.time.sleep", lambda seconds: None)

    result = asyncio.run(delete_hierarchy("Notebook/Test", permanently=True))

    assert result["ok"] is True
    assert result["deleted_ids"] == ["old-id", "new-id"]
