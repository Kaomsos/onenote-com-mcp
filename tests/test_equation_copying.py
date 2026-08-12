import json
import xml.etree.ElementTree as ET

from local_onenote_mcp.page import (
    copy_verification_tier,
    page_content_capability_projection,
    page_equivalence,
    semantic_display_equation_comparison,
    semantic_mathml_comparison,
    transform_page_for_copy,
)


def equation_page_xml(page_id: str) -> str:
    namespace = "http://www.w3.org/1998/Math/MathML"
    return (
        '<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" '
        f'ID="{page_id}"><one:Title><one:OE><one:T>Equations</one:T></one:OE>'
        "</one:Title><one:Outline objectID="
        '"outline"><one:OEChildren><one:OE><one:T><![CDATA['
        f'before <math xmlns="{namespace}"><mrow><mi>E</mi><mo>=</mo><mi>m</mi>'
        "<msup><mi>c</mi><mn>2</mn></msup></mrow></math> after]]></one:T></one:OE>"
        f'<one:OE><one:T><![CDATA[<math xmlns="{namespace}" display="block">'
        "<mfrac><mi>x</mi><mi>y</mi></mfrac></math>]]></one:T></one:OE>"
        "</one:OEChildren></one:Outline></one:Page>"
    )


def standalone_display_outline_page_xml(page_id: str) -> str:
    namespace = "http://www.w3.org/1998/Math/MathML"
    return (
        '<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" '
        f'ID="{page_id}"><one:Title><one:OE><one:T>Equations</one:T></one:OE>'
        "</one:Title><one:Outline><one:Position x=\"10\" y=\"20\" />"
        '<one:Size width="100" height="40"/><one:OEChildren><one:OE><one:T>'
        f'<![CDATA[before <math xmlns="{namespace}"><mi>x</mi></math> after]]>'
        "</one:T></one:OE></one:OEChildren></one:Outline>"
        '<one:Outline><one:Position x="10" y="80" />'
        '<one:Size width="120" height="60"/><one:OEChildren><one:OE><one:T>'
        f'<![CDATA[<math xmlns="{namespace}" display="block"><mfrac><mi>x</mi>'
        "<mi>y</mi></mfrac></math>]]></one:T></one:OE></one:OEChildren>"
        "</one:Outline></one:Page>"
    )


def test_mathml_is_projected_and_copied_as_validated_rich_text() -> None:
    source = equation_page_xml("source")

    projection = page_content_capability_projection(source)
    transformed = transform_page_for_copy(
        source,
        "target",
        {"source": "target"},
    )
    target_projection = page_content_capability_projection(transformed["xml"])
    comparison = page_equivalence(source, transformed["xml"])

    assert projection["capabilities"] == ["DisplayEquation", "Outline", "RichText"]
    assert projection["embedded_markup_tag_counts"]["math"] == 2
    assert projection["embedded_markup_attribute_name_counts"] == {
        "math@display": 1,
        "math@xmlns": 2,
    }
    assert projection["complete"] is True
    assert transformed["content_types"] == ["DisplayEquation", "Outline", "RichText"]
    assert transformed["lossless_candidate"] is True
    assert transformed["normalizations"] == {
        "display_equation_empty_spans_removed": 0,
        "redundant_breaks_before_display_mathml_removed": 0,
    }
    assert target_projection["embedded_markup_tag_counts"]["math"] == 2
    assert target_projection["embedded_markup_attribute_name_counts"] == {
        "math@display": 1,
        "math@xmlns": 2,
    }
    assert comparison["equivalent"] is True
    assert not any(
        issue["code"] == "content_type_unverified"
        for issue in transformed["issues"]
    )


def prefixed_mathml(xml: str) -> str:
    result = xml.replace(
        '<math xmlns="http://www.w3.org/1998/Math/MathML"',
        '<m:math xmlns:m="http://www.w3.org/1998/Math/MathML"',
    ).replace("</math>", "</m:math>")
    for tag in ("mfrac", "mi", "mn", "mo", "mrow", "msup"):
        result = result.replace(f"<{tag}>", f"<m:{tag}>").replace(
            f"</{tag}>", f"</m:{tag}>"
        )
    return result


def test_equation_copy_uses_bounded_mathml_semantics_for_com_reserialization() -> None:
    source = equation_page_xml("source")
    target = prefixed_mathml(equation_page_xml("target"))
    tier = copy_verification_tier(
        ["Outline", "RichText"],
        page_xml=source,
    )

    result = page_equivalence(source, target, verification_tier=tier)

    assert tier == "semantic_display_equation"
    assert result["equivalent"] is True
    assert result["checks"]["canonical_xml"] is False
    assert result["checks"]["semantic_mathml"] is True
    assert result["checks"]["outside_mathml_canonical"] is True
    comparison = result["display_equation_comparison"]
    assert comparison["source_equation_count"] == 2
    assert comparison["target_equation_count"] == 2
    assert comparison["projection_equal"] is True
    assert comparison["source_projection_sha256"] == comparison[
        "target_projection_sha256"
    ]


def test_equation_copy_normalizes_only_complete_onenote_mathml_comments() -> None:
    source = equation_page_xml("source")
    target = equation_page_xml("target").replace(
        '<math xmlns="http://www.w3.org/1998/Math/MathML"',
        '<!-- [if mathML] >\n<math xmlns="http://www.w3.org/1998/Math/MathML"',
    ).replace("</math>", "</math>\n<! [endif] -->")

    comparison = semantic_display_equation_comparison(source, target)

    assert comparison["projection_equal"] is True
    assert comparison["expected_conditional_mathml_wrapper_count"] == 0
    assert comparison["actual_conditional_mathml_wrapper_count"] == 2
    assert comparison["outside_mathml_canonical"] is True
    assert comparison[
        "outside_mathml_canonical_after_display_equation_normalization"
    ] is True
    assert comparison["outside_mathml_mismatch"] is None
    assert comparison["passed"] is True


def test_equation_copy_keeps_unrelated_comments_strict_and_reports_no_content() -> None:
    source = equation_page_xml("source")
    target = equation_page_xml("target").replace(
        "before <math",
        "before <!--not-a-mathml-wrapper--><math",
    )

    comparison = semantic_display_equation_comparison(source, target)
    mismatch = comparison["outside_mathml_mismatch"]

    assert comparison["projection_equal"] is True
    assert comparison["passed"] is False
    assert mismatch["field"] == "text"
    assert mismatch["path"].endswith("/T[0]")
    serialized = json.dumps(mismatch, sort_keys=True)
    assert "before" not in serialized
    assert "not-a-mathml-wrapper" not in serialized


def test_equation_copy_keeps_incomplete_mathml_comment_wrapper_strict() -> None:
    source = equation_page_xml("source")
    target = equation_page_xml("target").replace(
        "before <math",
        "before <!--[if mathML]><math",
    )

    comparison = semantic_display_equation_comparison(source, target)

    assert comparison["projection_equal"] is True
    assert comparison["outside_mathml_mismatch"]["field"] == "text"
    assert comparison["passed"] is False


def test_display_equation_copy_normalizes_formula_only_outline_size() -> None:
    source = standalone_display_outline_page_xml("source")
    target = standalone_display_outline_page_xml("target").replace(
        'width="120" height="60"',
        'width="143.25" height="82.5"',
    )

    comparison = semantic_display_equation_comparison(source, target)

    assert comparison["expected_derived_size_outline_count"] == 1
    assert comparison["actual_derived_size_outline_count"] == 1
    assert comparison["outside_mathml_mismatch"] is None
    assert comparison["passed"] is True


def test_display_equation_copy_keeps_formula_only_outline_position_strict() -> None:
    source = standalone_display_outline_page_xml("source")
    target = standalone_display_outline_page_xml("target").replace(
        'x="10" y="80"',
        'x="11" y="80"',
    )

    comparison = semantic_display_equation_comparison(source, target)

    assert comparison["outside_mathml_mismatch"]["field"] == "attributes"
    assert comparison["outside_mathml_mismatch"]["path"].endswith("/Position[0]")
    assert comparison["passed"] is False


def test_display_equation_copy_rejects_extra_formula_outline_size_attribute() -> None:
    source = standalone_display_outline_page_xml("source")
    target = standalone_display_outline_page_xml("target").replace(
        'width="120" height="60"',
        'width="143.25" height="82.5" isSetByUser="true"',
    )

    comparison = semantic_display_equation_comparison(source, target)

    assert comparison["expected_derived_size_outline_count"] == 1
    assert comparison["actual_derived_size_outline_count"] == 0
    assert comparison["outside_mathml_mismatch"]["field"] == "attributes"
    assert comparison["passed"] is False


def test_display_equation_copy_keeps_mixed_content_outline_size_strict() -> None:
    source = equation_page_xml("source").replace(
        '<one:Outline objectID="outline">',
        '<one:Outline objectID="outline"><one:Size width="100" height="40"/>',
    )
    target = equation_page_xml("target").replace(
        '<one:Outline objectID="outline">',
        '<one:Outline objectID="outline"><one:Size width="101" height="40"/>',
    )

    comparison = semantic_display_equation_comparison(source, target)

    assert comparison["expected_derived_size_outline_count"] == 0
    assert comparison["actual_derived_size_outline_count"] == 0
    assert comparison["outside_mathml_mismatch"]["field"] == "attributes"
    assert comparison["passed"] is False


def test_equation_copy_rejects_changed_mathml_tokens() -> None:
    source = equation_page_xml("source")
    target = equation_page_xml("target").replace("<mi>y</mi>", "<mi>z</mi>")

    comparison = semantic_mathml_comparison(source, target)
    result = page_equivalence(
        source,
        target,
        verification_tier="semantic_mathml",
    )

    assert comparison["projection_equal"] is False
    assert comparison["outside_mathml_canonical"] is True
    assert comparison["passed"] is False
    assert result["equivalent"] is False


def test_equation_copy_rejects_non_mathml_formatting_drift() -> None:
    source = equation_page_xml("source")
    target = equation_page_xml("target").replace("before ", "<b>before</b> ")

    result = page_equivalence(
        source,
        target,
        verification_tier="semantic_mathml",
    )

    assert result["checks"]["visible_text"] is True
    assert result["checks"]["semantic_mathml"] is True
    assert result["checks"]["outside_mathml_canonical"] is False
    assert result["equivalent"] is False


def test_incomplete_mathml_does_not_select_semantic_tier() -> None:
    source = equation_page_xml("source").replace("</math>", "", 1)

    assert copy_verification_tier(
        ["Outline", "RichText"],
        page_xml=source,
    ) == "strict_canonical"


def test_copy_removes_only_the_redundant_break_before_display_mathml() -> None:
    source = equation_page_xml("source").replace(
        '<math xmlns="http://www.w3.org/1998/Math/MathML" display="block">',
        '<br/><br/><math xmlns="http://www.w3.org/1998/Math/MathML" display="block">',
    )

    transformed = transform_page_for_copy(source, "target", {"source": "target"})
    display_text = next(
        node.text or ""
        for node in ET.fromstring(transformed["xml"]).iter()
        if node.tag.rsplit("}", 1)[-1] == "T" and 'display="block"' in (node.text or "")
    )

    assert '<br/><math xmlns="http://www.w3.org/1998/Math/MathML" display="block">' not in display_text
    assert '<br/><br/><math xmlns="http://www.w3.org/1998/Math/MathML"' not in display_text
    assert transformed["normalizations"] == {
        "display_equation_empty_spans_removed": 0,
        "redundant_breaks_before_display_mathml_removed": 2,
    }


def test_display_equation_copy_strips_accumulated_empty_span_before_write() -> None:
    display_mathml = (
        '<math xmlns="http://www.w3.org/1998/Math/MathML" display="block">'
        "<mfrac><mi>x</mi><mi>y</mi></mfrac></math>"
    )
    source = equation_page_xml("source").replace(
        display_mathml,
        (
            "<span style='font-family:Anything' lang=zh-CN><br /><br /></span>"
            f"<!--[if mathML]>{display_mathml}<![endif]-->"
        ),
    )

    transformed = transform_page_for_copy(source, "target", {"source": "target"})
    display_text = next(
        node.text or ""
        for node in ET.fromstring(transformed["xml"]).iter()
        if node.tag.rsplit("}", 1)[-1] == "T" and 'display="block"' in (node.text or "")
    )

    assert "<span" not in display_text
    assert "<br" not in display_text
    assert "<!--[if mathML]><math" in display_text
    assert transformed["normalizations"] == {
        "display_equation_empty_spans_removed": 1,
        "redundant_breaks_before_display_mathml_removed": 2,
    }


def test_display_equation_comparator_accepts_one_empty_span_break_regardless_of_font() -> None:
    expected = equation_page_xml("expected")
    actual = equation_page_xml("actual").replace(
        '<math xmlns="http://www.w3.org/1998/Math/MathML" display="block">',
        (
            "<span style='font-family:Arbitrary Font'><br /></span>"
            '<math xmlns="http://www.w3.org/1998/Math/MathML" display="block">'
        ),
    )

    result = page_equivalence(
        expected,
        actual,
        verification_tier="semantic_display_equation",
    )

    assert result["equivalent"] is True
    assert result["checks"]["canonical_xml"] is False
    assert result["checks"]["display_equation_com_normalization"] is True
    comparison = result["display_equation_comparison"]
    assert comparison["actual_empty_markup"] == {
        "span_sequence_count": 1,
        "span_count": 1,
        "span_break_count": 1,
        "direct_break_count": 0,
    }


def test_display_equation_comparator_rejects_accumulation_or_visible_span_text() -> None:
    expected = equation_page_xml("expected")
    one_break = equation_page_xml("actual").replace(
        '<math xmlns="http://www.w3.org/1998/Math/MathML" display="block">',
        (
            "<span style='font-family:Calibri'><br /></span>"
            '<math xmlns="http://www.w3.org/1998/Math/MathML" display="block">'
        ),
    )
    two_breaks = one_break.replace("<br />", "<br /><br />")
    visible_text = one_break.replace("<br />", "unexpected<br />")
    semantic_attribute = one_break.replace(
        "style='font-family:Calibri'",
        "data-tag='important'",
    )

    assert semantic_display_equation_comparison(expected, two_breaks)["passed"] is False
    assert semantic_display_equation_comparison(expected, visible_text)["passed"] is False
    assert semantic_display_equation_comparison(expected, semantic_attribute)["passed"] is False
