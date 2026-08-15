from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from tests.manual_validation.scenarios.common.registry import SCENARIO_REGISTRY
from tests.manual_validation.scenarios.fixture_recipes import interactive
from tests.manual_validation.test_utils import (
    mathml_oe_adjacency_projection,
    mathml_structure_projection,
)


ONE = "http://schemas.microsoft.com/office/onenote/2013/onenote"
MATH = "http://www.w3.org/1998/Math/MathML"


def _xml(
    *,
    display: bool = True,
    equation_count: int = 1,
    surrounding_text: bool = False,
) -> str:
    display_attribute = ' display="block"' if display else ""
    equation = (
        f'&lt;math xmlns="{MATH}"{display_attribute}&gt;&lt;mrow&gt;'
        "&lt;mi&gt;x&lt;/mi&gt;&lt;mo&gt;=&lt;/mo&gt;&lt;mn&gt;1&lt;/mn&gt;"
        "&lt;/mrow&gt;&lt;/math&gt;"
    )
    prefix = "before " if surrounding_text else ""
    suffix = " after" if surrounding_text else ""
    formula_oes = "".join(
        f"<one:OE><one:T>{prefix}{equation}{suffix}</one:T></one:OE>"
        for _ in range(equation_count)
    )
    return (
        f'<one:Page xmlns:one="{ONE}">'
        "<one:Title><one:OE><one:T>01-Source-Parent</one:T></one:OE></one:Title>"
        "<one:Outline objectID=" + '"outline-1"' + "><one:OEChildren>"
        "<one:OE><one:T>&lt;strong&gt;base&lt;/strong&gt;</one:T></one:OE>"
        f"{formula_oes}"
        "</one:OEChildren></one:Outline>"
        "</one:Page>"
    )


def _snapshot(xml: str, *, objects=None, capabilities=None) -> dict:
    page_id = "page"
    default_capabilities = ["Outline", "RichText", "Table", "Image"]
    if 'display="block"' in xml:
        default_capabilities.append("DisplayEquation")
    return {
        "page_objects": {
            page_id: list(
                objects
                if objects is not None
                else (
                    {"kind": "Outline"},
                    {"kind": "OE"},
                    {"kind": "Table"},
                    {"kind": "Row"},
                    {"kind": "Cell"},
                    {"kind": "Image"},
                )
            )
        },
        "page_capability_projections": {
            page_id: {
                "capabilities": list(
                    capabilities
                    if capabilities is not None
                    else default_capabilities
                ),
                "unknown_nodes": [],
                "unsupported_page_roots": [],
                "complete": True,
            }
        },
        "page_mathml_structure_projections": {
            page_id: mathml_structure_projection(xml)
        },
    }


def _split_t_inline_xml(*, break_before_formula: bool = False) -> str:
    equation = (
        f'&lt;math xmlns="{MATH}"&gt;&lt;mrow&gt;'
        "&lt;mi&gt;x&lt;/mi&gt;&lt;mo&gt;=&lt;/mo&gt;&lt;mn&gt;1&lt;/mn&gt;"
        "&lt;/mrow&gt;&lt;/math&gt;"
    )
    line_break = "&lt;br /&gt;" if break_before_formula else ""
    return (
        f'<one:Page xmlns:one="{ONE}">'
        "<one:Title><one:OE><one:T>01-Source-Parent</one:T></one:OE></one:Title>"
        '<one:Outline objectID="outline-1"><one:OEChildren>'
        "<one:OE><one:T>&lt;strong&gt;base&lt;/strong&gt;</one:T></one:OE>"
        f"<one:OE><one:T>before {line_break}</one:T><one:T>{equation}</one:T>"
        "<one:T> after</one:T></one:OE>"
        "</one:OEChildren></one:Outline>"
        "</one:Page>"
    )


def test_structure_projection_records_oe_placement_without_formula_text() -> None:
    projection = mathml_structure_projection(_xml())

    assert projection["complete"] is True
    assert projection["display_attribute_equation_count"] == 1
    assert projection["equations_without_display_attribute"] == 0
    assert projection["standalone_candidate_count"] == 1
    assert projection["candidate_text_node_count"] == 1
    candidate = projection["candidates"][0]
    assert candidate["ancestor_kinds"][:4] == ["T", "OE", "OEChildren", "Outline"]
    assert candidate["oe_sibling_index"] == 1
    assert candidate["previous_oe"]["contains_visible_text"] is True
    serialized = str(projection)
    assert ">x<" not in serialized
    assert ">base<" not in serialized


def test_oe_adjacency_projection_distinguishes_literal_space_before_formula() -> None:
    xml = _xml().replace(
        "<one:OE><one:T>&lt;math",
        "<one:OE><one:T> </one:T></one:OE><one:OE><one:T>&lt;math",
        1,
    )
    projection = mathml_oe_adjacency_projection(xml)

    candidate = projection["candidates"][0]
    assert candidate["matches_literal_space_oe_then_display_formula_oe"] is True
    assert candidate["previous_oe"]["direct_t"] == [
        {
            "raw_chars": 1,
            "whitespace_chars": 1,
            "only_whitespace": True,
            "whitespace_codepoint_counts": {"U+0020": 1},
            "mathml_root_count": 0,
            "display_block_count": 0,
            "mathml_complete": True,
            "residual_chars": 1,
            "residual_whitespace_chars": 1,
            "residual_only_whitespace": True,
            "residual_visible_text": False,
        }
    ]
    assert projection["content_exposed"] is False


def test_display_equation_detector_requires_one_display_formula_and_parent_base() -> None:
    recipe = SCENARIO_REGISTRY.get("copy-display-equation").fixture_recipe

    report = recipe.content_report(_snapshot(_xml()), "page")
    assert report["passed"] is True
    assert report["representation_status"] == "display_mathml_observed"
    assert report["observed"] == {"DisplayEquation": 1}

    no_display_attribute = recipe.content_report(_snapshot(_xml(display=False)), "page")
    assert no_display_attribute["passed"] is False
    assert no_display_attribute["display_attribute_observed"] is False

    embedded = recipe.content_report(_snapshot(_xml(surrounding_text=True)), "page")
    assert embedded["passed"] is False
    assert embedded["missing"] == ["DisplayEquation"]
    assert "formula-t-has-visible-residual-text" in embedded["unexpected"]

    duplicate = recipe.content_report(_snapshot(_xml(equation_count=2)), "page")
    assert duplicate["passed"] is False
    assert "mathml-equations:2" in duplicate["unexpected"]

    missing_base = recipe.content_report(
        _snapshot(_xml(), capabilities=("Outline", "RichText", "Image")),
        "page",
    )
    assert missing_base["passed"] is False
    assert "missing-base:Table" in missing_base["unexpected"]


def test_display_equation_detector_rejects_legacy_object_schema_and_extra_capability() -> None:
    recipe = SCENARIO_REGISTRY.get("copy-display-equation").fixture_recipe
    report = recipe.content_report(
        _snapshot(
            _xml(),
            objects=({"type": "Outline"},),
            capabilities=("Outline", "RichText", "Table", "Image", "InkDrawing"),
        ),
        "page",
    )

    assert report["passed"] is False
    assert "invalid-object-schema" in report["unexpected"]
    assert "capability:InkDrawing" in report["unexpected"]


def test_display_equation_copy_readback_uses_semantic_mathml_and_structure_gates() -> None:
    recipe = SCENARIO_REGISTRY.get("copy-display-equation").fixture_recipe
    xml = _xml()
    copy_report = {
        "page_results": [
            {
                "equivalence": {
                    "equivalent": True,
                    "checks": {
                        "visible_text": True,
                        "content_objects": True,
                        "binary_sha256": True,
                        "semantic_mathml": True,
                        "display_equation_com_normalization": True,
                        "outside_mathml_canonical": True,
                    },
                }
            }
        ]
    }

    comparison = recipe.compare_copy_readback(xml, xml, copy_report)
    assert comparison["passed"] is True
    assert comparison["verification_tier"] == "semantic_display_equation"

    embedded_target = recipe.compare_copy_readback(
        xml, _xml(surrounding_text=True), copy_report
    )
    assert embedded_target["passed"] is False
    assert embedded_target["checks"]["one_standalone_equation_each"] is False


def test_display_equation_comparator_accepts_one_recorded_span_break() -> None:
    recipe = SCENARIO_REGISTRY.get("copy-display-equation").fixture_recipe
    source = _xml()
    target = source.replace(
        "<one:T>&lt;math",
        (
            "<one:T>&lt;span style='font-family:Calibri' lang=zh-CN&gt;"
            "&lt;br /&gt;&lt;/span&gt;&lt;math"
        ),
        1,
    )
    copy_report = {
        "page_results": [
            {
                "equivalence": {
                    "equivalent": True,
                    "checks": {
                        "visible_text": True,
                        "content_objects": True,
                        "binary_sha256": True,
                        "semantic_mathml": True,
                        "display_equation_com_normalization": True,
                        "outside_mathml_canonical": True,
                    },
                }
            }
        ]
    }

    accepted = recipe.compare_copy_readback(source, target, copy_report)
    assert accepted["passed"] is True
    assert accepted["temporary_known_com_normalization_accepted"] is False
    assert accepted["documented_display_equation_com_normalization_accepted"] is True
    assert accepted["display_break_observation"] == {
        "source_count": 0,
        "target_count": 1,
        "delta": 1,
        "target_matches_recorded_span_br_shape": True,
    }

    doubled = target.replace("&lt;br /&gt;", "&lt;br /&gt;&lt;br /&gt;", 1)
    rejected = recipe.compare_copy_readback(source, doubled, copy_report)
    assert rejected["passed"] is False
    assert rejected["temporary_known_com_normalization_accepted"] is False
    assert rejected["display_break_observation"]["target_count"] == 2


def test_display_equation_scenario_is_programmatic_and_owns_source_contract() -> None:
    scenario = SCENARIO_REGISTRY.get("copy-display-equation")

    assert scenario.fixture_recipe.canvas_title == "01-Source-Parent"
    assert scenario.fixture_profile.expected_structure[0].startswith(
        "Source/01-Source-Parent"
    )
    assert scenario.fixture_profile.creation_tools == {
        "create_section",
        "create_page",
        "append_page_content",
        "add_page_image_from_file",
    }
    assert scenario.fixture_recipe.build_mode.value == "programmatic"
    assert scenario.fixture_recipe.consumer_scenario is False
    assert scenario.spec.execution_contract["bounded_copy_chain"] == 3
    assert "delete_page" in scenario.spec.tool_allowlist


def test_display_equation_scaffold_generates_standalone_block_mathml(
    monkeypatch, tmp_path: Path
) -> None:
    recipe = SCENARIO_REGISTRY.get("copy-display-equation").fixture_recipe
    calls: dict[str, object] = {}

    async def ensure_section(_client, notebook_id, name):
        calls["section"] = (notebook_id, name)
        return {"id": "section", "resource_type": "section"}

    async def ensure_page(_client, section_id, title, body):
        calls["page"] = (section_id, title, body)
        return {
            "id": "page",
            "resource_type": "page",
            "section_id": section_id,
            "title": title,
            "modified": "before",
        }

    async def ensure_rich(_client, page, run_dir, *, include_equations):
        calls["rich"] = (page["id"], run_dir, include_equations)
        return dict(page), {"automated_content": ["rich_text", "table", "image"]}

    class Recorder:
        def __init__(self):
            self.structure = {}
            self.evidence = {}

        def record_structure(self, key, value):
            self.structure[key] = dict(value)
            return self.structure[key]

        def refresh_structure(self, key, value):
            self.structure[key] = dict(value)
            return self.structure[key]

        def record_evidence(self, key, value):
            self.evidence[key] = value

    class Client:
        async def call_tool(self, name, arguments):
            if name == "append_page_content":
                calls["display_content"] = arguments["content"]
                return {
                    "item": {
                        "id": "page",
                        "resource_type": "page",
                        "section_id": "section",
                        "title": "01-Source-Parent",
                        "modified": "after",
                    }
                }
            assert name == "get_page_xml"
            return {"xml": _xml(display=True)}

    monkeypatch.setattr(interactive, "ensure_section", ensure_section)
    monkeypatch.setattr(interactive, "ensure_page", ensure_page)
    monkeypatch.setattr(interactive, "ensure_copy_rich_fixture", ensure_rich)
    recorder = Recorder()
    result = asyncio.run(
        recipe.build_scaffold(
            SimpleNamespace(
                    client=Client(),
                notebook_id="notebook",
                options=SimpleNamespace(run_dir=tmp_path),
                recorder=recorder,
            )
        )
    )

    assert calls["section"] == ("notebook", "Source")
    assert calls["page"][0:2] == ("section", "01-Source-Parent")
    assert calls["rich"] == ("page", tmp_path, False)
    assert '<math xmlns="' in calls["display_content"]
    assert 'display="block"' in calls["display_content"]
    assert result.structure["canvas_page"]["id"] == "page"
    assert result.evidence["copy_fixture"]["automated_content"] == [
        "rich_text",
        "table",
        "image",
        "display_equation",
    ]


def test_inline_equation_detector_requires_inline_text_without_display_or_break() -> None:
    recipe = SCENARIO_REGISTRY.get("bootstrap-inline-equation-fixture").fixture_recipe
    inline_xml = _xml(display=False, surrounding_text=True)

    report = recipe.content_report(_snapshot(inline_xml), "page")
    assert report["passed"] is True
    assert report["representation_status"] == "inline_mathml_observed"
    assert report["display_attribute_observed"] is False

    standalone = recipe.content_report(_snapshot(_xml(display=False)), "page")
    assert standalone["passed"] is False
    assert standalone["missing"] == ["InlineEquation"]

    display = recipe.content_report(
        _snapshot(_xml(display=True, surrounding_text=True)), "page"
    )
    assert display["passed"] is False
    assert "unexpected-display-attribute" in display["unexpected"]

    with_break = inline_xml.replace("<one:T>before ", "<one:T>before &lt;br /&gt; ")
    broken = recipe.content_report(_snapshot(with_break), "page")
    assert broken["passed"] is False
    assert "inline-break-count:1" in broken["unexpected"]


def test_inline_equation_detector_accepts_real_onenote_split_t_shape() -> None:
    recipe = SCENARIO_REGISTRY.get("bootstrap-inline-equation-fixture").fixture_recipe
    projection = mathml_structure_projection(_split_t_inline_xml())
    candidate = projection["candidates"][0]

    assert projection["schema_version"] == 2
    assert projection["standalone_candidate_count"] == 0
    assert candidate["oe_child_kinds"] == ["T", "T", "T"]
    assert candidate["t_sibling_index"] == 1
    assert candidate["same_oe_surrounding_visible_text"] is True
    assert candidate["inline_visible_text_context"] is True
    assert candidate["oe_direct_t_break_count"] == 0
    assert recipe.content_report(_snapshot(_split_t_inline_xml()), "page")["passed"] is True

    broken = recipe.content_report(
        _snapshot(_split_t_inline_xml(break_before_formula=True)),
        "page",
    )
    assert broken["passed"] is False
    assert "inline-break-count:1" in broken["unexpected"]


def test_inline_equation_copy_readback_rejects_added_break() -> None:
    recipe = SCENARIO_REGISTRY.get("interactive-copy-inline-equation").fixture_recipe
    source = _xml(display=False, surrounding_text=True)
    copy_report = {
        "page_results": [
            {
                "equivalence": {
                    "equivalent": True,
                    "checks": {
                        "visible_text": True,
                        "content_objects": True,
                        "binary_sha256": True,
                        "semantic_mathml": True,
                        "outside_mathml_canonical": True,
                    },
                }
            }
        ]
    }

    assert recipe.compare_copy_readback(source, source, copy_report)["passed"] is True
    target = source.replace("<one:T>before ", "<one:T>before &lt;br /&gt; ")
    changed = recipe.compare_copy_readback(source, target, copy_report)
    assert changed["passed"] is False
    assert changed["checks"]["no_break_around_inline_equation"] is False

    split_source = _split_t_inline_xml()
    assert recipe.compare_copy_readback(split_source, split_source, copy_report)[
        "passed"
    ] is True
    split_target = _split_t_inline_xml(break_before_formula=True)
    split_changed = recipe.compare_copy_readback(
        split_source,
        split_target,
        copy_report,
    )
    assert split_changed["passed"] is False
    assert split_changed["checks"]["no_break_around_inline_equation"] is False


def test_inline_equation_scaffold_generates_mathml_without_display_attribute(
    monkeypatch, tmp_path: Path
) -> None:
    recipe = SCENARIO_REGISTRY.get("bootstrap-inline-equation-fixture").fixture_recipe
    calls: dict[str, object] = {}

    async def ensure_section(_client, _notebook_id, _name):
        return {"id": "section", "resource_type": "section"}

    async def ensure_page(_client, section_id, title, _body):
        return {
            "id": "page",
            "resource_type": "page",
            "section_id": section_id,
            "title": title,
            "modified": "before",
        }

    async def ensure_rich(_client, page, _run_dir, *, include_equations):
        assert include_equations is False
        return dict(page), {"automated_content": ["rich_text", "table", "image"]}

    class Client:
        async def call_tool(self, name, arguments):
            if name == "append_page_content":
                calls["content"] = arguments["content"]
                return {"item": {**page_value, "modified": "after"}}
            assert name == "get_page_xml"
            return {"xml": _xml(display=False, surrounding_text=True)}

    class Recorder:
        def __init__(self):
            self.structure = {}
            self.evidence = {}

        def record_structure(self, key, value):
            self.structure[key] = dict(value)
            return self.structure[key]

        def refresh_structure(self, key, value):
            self.structure[key] = dict(value)
            return self.structure[key]

        def record_evidence(self, key, value):
            self.evidence[key] = value

    page_value = {
        "id": "page",
        "resource_type": "page",
        "section_id": "section",
        "title": "01-Source-Parent",
    }
    monkeypatch.setattr(interactive, "ensure_section", ensure_section)
    monkeypatch.setattr(interactive, "ensure_page", ensure_page)
    monkeypatch.setattr(interactive, "ensure_copy_rich_fixture", ensure_rich)
    recorder = Recorder()
    result = asyncio.run(
        recipe.build_scaffold(
            SimpleNamespace(
                client=Client(),
                notebook_id="notebook",
                options=SimpleNamespace(run_dir=tmp_path),
                recorder=recorder,
            )
        )
    )

    assert "<math " in calls["content"]
    assert 'display="block"' not in calls["content"]
    assert result.evidence["copy_fixture"]["automated_content"][-1] == (
        "inline_equation"
    )
    assert result.evidence["copy_fixture"]["inline_equation_structure"][
        "display_attribute_equation_count"
    ] == 0
