import asyncio

import pytest

from local_onenote_mcp.page import build_page_update_xml

from tests.manual_validation.runtime import InvariantFailure
from tests.manual_validation.scenarios.common.fixture_builders import (
    DISPLAY_EQUATION_MARKER,
    INLINE_EQUATION_MARKER,
    ensure_copy_rich_fixture,
)
from tests.manual_validation.scenarios.fixture_recipes.copy_page import RECIPE
from tests.manual_validation.scenarios.fixture_recipes.layered_copy import (
    LayeredFixtureKind,
    _merge_automated_content,
)
from tests.manual_validation.test_utils import read_json


def test_copy_page_recipe_requires_inline_and_display_equations() -> None:
    assert RECIPE.recipe_version == 9
    assert RECIPE.config.kind is LayeredFixtureKind.PAGE
    assert RECIPE.config.include_equations is True


def test_semantic_fixture_capabilities_do_not_overwrite_equation_evidence() -> None:
    evidence = {
        "automated_content": [
            "rich_text",
            "table",
            "image",
            "inline_equation",
            "display_equation",
        ]
    }

    _merge_automated_content(evidence, ("list", "tag"))

    assert evidence["automated_content"] == [
        "rich_text",
        "table",
        "image",
        "inline_equation",
        "display_equation",
        "list",
        "tag",
    ]


def test_copy_page_rich_fixture_builds_and_reuses_exact_equation_pair(tmp_path) -> None:
    state = {
        "xml": (
            '<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" '
            'ID="page-id"><one:Title><one:OE><one:T>Parent</one:T></one:OE>'
            "</one:Title></one:Page>"
        ),
        "objects": [],
    }
    page = {
        "resource_type": "page",
        "id": "page-id",
        "title": "Parent",
        "section_id": "section-id",
        "modified": "m1",
    }

    class FakeClient:
        def __init__(self) -> None:
            self.append_arguments = []
            self.image_calls = 0

        async def call_tool(self, name, arguments):
            if name == "get_page_xml":
                return {"xml": state["xml"]}
            if name == "get_page_objects":
                return {"objects": state["objects"]}
            if name == "list_pages":
                return {"pages": [page]}
            if name == "append_to_page":
                self.append_arguments.append(dict(arguments))
                state["xml"] = build_page_update_xml(
                    "page-id",
                    title="Parent",
                    content=arguments["content"],
                    content_format=arguments["content_format"],
                )
                return {"appended": True}
            if name == "add_image_to_page":
                self.image_calls += 1
                state["objects"] = [{"kind": "Image", "media_type": "png"}]
                return {"image_path": arguments["image_path"]}
            raise AssertionError(name)

    client = FakeClient()
    _, first = asyncio.run(
        ensure_copy_rich_fixture(
            client,
            page,
            tmp_path,
            include_equations=True,
        )
    )
    _, second = asyncio.run(
        ensure_copy_rich_fixture(
            client,
            page,
            tmp_path,
            include_equations=True,
        )
    )

    assert len(client.append_arguments) == 1
    assert client.image_calls == 1
    assert client.append_arguments[0]["content_format"] == "html"
    assert INLINE_EQUATION_MARKER in client.append_arguments[0]["content"]
    assert DISPLAY_EQUATION_MARKER in client.append_arguments[0]["content"]
    assert client.append_arguments[0]["content"].count("<math ") == 2
    assert client.append_arguments[0]["content"].count('display="block"') == 1
    assert f"<span>{DISPLAY_EQUATION_MARKER}</span><math" in client.append_arguments[0][
        "content"
    ]
    assert first == second
    assert first["automated_content"] == [
        "rich_text",
        "table",
        "image",
        "inline_equation",
        "display_equation",
    ]
    assert first["equations"] == {
        "mathml_roots": 2,
        "inline_equations": 1,
        "display_equations": 1,
        "namespace_declarations": 2,
        "redundant_breaks_before_display": 0,
        "standalone_display_oes": 1,
        "nonempty_display_predecessors": 1,
        "empty_oes_before_display": 0,
    }
    detection = read_json(tmp_path / "fixture-equation-detection.json")
    assert detection["passed"] is True
    assert detection["checks"] == {
        "equations_passed": True,
        "image_present": True,
        "rich_text_marker_present": True,
        "table_present": True,
    }
    assert detection["equations"]["mismatches"] == {}
    assert "xml" not in detection


def test_copy_page_rich_fixture_writes_equation_detection_before_failure(tmp_path) -> None:
    state = {
        "xml": (
            '<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" '
            'ID="page-id"><one:Title><one:OE><one:T>Parent</one:T></one:OE>'
            "</one:Title></one:Page>"
        ),
        "objects": [],
    }
    page = {
        "resource_type": "page",
        "id": "page-id",
        "title": "Parent",
        "section_id": "section-id",
        "modified": "m1",
    }

    class ComNormalizedClient:
        async def call_tool(self, name, arguments):
            if name == "get_page_xml":
                return {"xml": state["xml"]}
            if name == "get_page_objects":
                return {"objects": state["objects"]}
            if name == "list_pages":
                return {"pages": [page]}
            if name == "append_to_page":
                state["xml"] = build_page_update_xml(
                    "page-id",
                    title="Parent",
                    content=arguments["content"],
                    content_format=arguments["content_format"],
                ).replace(
                    "<![CDATA[<math xmlns=",
                    "<![CDATA[<br/><math xmlns=",
                    1,
                )
                return {"appended": True}
            if name == "add_image_to_page":
                state["objects"] = [{"kind": "Image", "media_type": "png"}]
                return {"image_path": arguments["image_path"]}
            raise AssertionError(name)

    with pytest.raises(
        InvariantFailure,
        match=r"equation_mismatches=.*evidence=fixture-equation-detection.json",
    ):
        asyncio.run(
            ensure_copy_rich_fixture(
                ComNormalizedClient(),
                page,
                tmp_path,
                include_equations=True,
            )
        )

    detection = read_json(tmp_path / "fixture-equation-detection.json")
    assert detection["passed"] is False
    assert detection["checks"]["equations_passed"] is False
    assert detection["equations"]["actual"]["redundant_breaks_before_display"] == 1
    assert detection["equations"]["mismatches"]["redundant_breaks_before_display"] == {
        "actual": 1,
        "expected": 0,
    }
    assert "xml" not in detection
