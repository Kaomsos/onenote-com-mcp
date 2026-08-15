import asyncio
import copy
import xml.etree.ElementTree as ET

import pytest

from local_onenote_mcp import server
from local_onenote_mcp.tools.mutations import reorder_section as public_reorder_section
from local_onenote_mcp.tools.responses import caught


def _flatten_tool_envelope(envelope):
    """Keep service-semantic assertions independent of the public envelope shape."""

    if envelope["ok"]:
        return {"ok": True, **envelope["result"]}
    error = envelope["error"]
    return {
        "ok": False,
        "code": error["code"],
        "error": error["message"],
        **error["details"],
    }


async def reorder_section(*args):
    return _flatten_tool_envelope(await public_reorder_section(*args))


async def diagnostic_reorder_section_group(*args):
    """Exercise the retained internal diagnostic service without MCP exposure."""

    try:
        return {"ok": True, **server.services.mutations.reorder_section_group(*args)}
    except Exception as exc:
        return _flatten_tool_envelope(caught(exc))


@pytest.fixture(autouse=True)
def _enable_explicit_internal_section_group_reorder_for_diagnostic_tests(monkeypatch):
    monkeypatch.setenv(
        "LOCAL_ONENOTE_ENABLE_INTERNAL_SECTION_GROUP_REORDER", "true"
    )


def item(kind, object_id, parent_id, *, name=None, section_id=None, order=None, recycle=False):
    value = {
        "resource_type": kind,
        "id": object_id,
        "name": name or object_id,
        "parent_id": parent_id,
        "notebook_id": "n" if kind != "notebook" else None,
        "is_in_recycle_bin": recycle,
        "modified": f"modified-{object_id}",
    }
    if kind == "page":
        value.update(
            title=name or object_id,
            section_id=section_id,
            order=order,
            page_level=1,
            parent_page_id=None,
        )
    return value


def section_fixture(*, nested=False):
    notebook = item("notebook", "n", None, name="Notebook")
    group = item("section_group", "g", "n", name="Group")
    parent_id = "g" if nested else "n"
    sections = [item("section", f"s{letter}", parent_id, name=letter) for letter in "ABC"]
    pages = [
        item("page", f"p{letter}", f"s{letter}", name=f"Page {letter}", section_id=f"s{letter}", order=0)
        for letter in "ABC"
    ]
    prefix = [notebook, group] if nested else [notebook, group]
    ordered = [prefix[0]]
    if nested:
        ordered.append(group)
    else:
        # A mixed-type direct child proves that full sibling XML is preserved.
        ordered.append(group)
    for section, page in zip(sections, pages):
        ordered.extend([section, page])
    return ordered


def group_fixture():
    values = [item("notebook", "n", None, name="Notebook")]
    for letter in "ABC":
        values.extend(
            [
                item("section_group", f"g{letter}", "n", name=f"Group {letter}"),
                item("section", f"s{letter}", f"g{letter}", name=f"Section {letter}"),
                item(
                    "page",
                    f"p{letter}",
                    f"s{letter}",
                    name=f"Page {letter}",
                    section_id=f"s{letter}",
                    order=0,
                ),
            ]
        )
    return values


def nested_group_fixture(order="ABC"):
    values = [
        item("notebook", "n", None, name="Notebook"),
        item("section_group", "parent", "n", name="Parent Group"),
    ]
    for letter in order:
        values.extend(
            [
                item(
                    "section_group",
                    f"nested{letter}",
                    "parent",
                    name=f"Nested Group {letter}",
                ),
                item(
                    "section",
                    f"nestedSection{letter}",
                    f"nested{letter}",
                    name=f"Nested Section {letter}",
                ),
                item(
                    "page",
                    f"nestedPage{letter}",
                    f"nestedSection{letter}",
                    name=f"Nested Page {letter}",
                    section_id=f"nestedSection{letter}",
                    order=0,
                ),
            ]
        )
    return values


def reordered_snapshot(before, resource_type, ordered_ids):
    before = copy.deepcopy(before)
    roots = {value["id"]: value for value in before if value["resource_type"] == resource_type}
    descendants = {}
    for root_id in roots:
        captured = []
        pending = {root_id}
        while pending:
            current = pending.pop()
            children = [value for value in before if value.get("parent_id") == current]
            captured.extend(children)
            pending.update(value["id"] for value in children)
        descendants[root_id] = captured
    unrelated = [
        value
        for value in before
        if value["id"] not in roots
        and not any(value in children for children in descendants.values())
    ]
    result = list(unrelated)
    for root_id in ordered_ids:
        result.append(roots[root_id])
        result.extend(descendants[root_id])
    return result


def install_backend(monkeypatch, before, after, *, page_xml_after=None, bridge_error=None):
    state = {"mutated": False, "calls": 0, "xml": ""}
    monkeypatch.setattr(
        server.services.hierarchy,
        "resources",
        lambda include_recycle_bin=False: list(after if state["mutated"] else before),
    )

    def page_xml(page_id, page_info="basic"):
        suffix = "changed" if state["mutated"] and page_xml_after == page_id else "same"
        return (
            '<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" '
            f'ID="{page_id}" name="Page"><one:Outline><one:T>{suffix}</one:T></one:Outline></one:Page>'
        )

    monkeypatch.setattr(server.services.pages, "xml", page_xml)

    def call(operation, **params):
        state["calls"] += 1
        state["xml"] = params.get("xml", "")
        if bridge_error is not None:
            raise bridge_error
        state["mutated"] = True
        return {"updated": True}

    monkeypatch.setattr(server.services.mutations, "call", call)
    return state


@pytest.mark.write_contract
@pytest.mark.parametrize(
    ("target", "after_id", "expected"),
    [
        ("sC", "", ["sC", "sA", "sB"]),
        ("sC", "sA", ["sA", "sC", "sB"]),
        ("sA", "sC", ["sB", "sC", "sA"]),
        ("sB", "sA", ["sA", "sB", "sC"]),
    ],
)
def test_reorder_section_supports_first_forward_backward_and_noop(monkeypatch, target, after_id, expected):
    before = section_fixture()
    after = reordered_snapshot(before, "section", expected)
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    state = install_backend(monkeypatch, before, after)

    result = asyncio.run(reorder_section(target, target[-1], "n", after_id, f"modified-{target}"))

    assert result["ok"] is True
    assert [value["id"] for value in result["siblings"]] == expected
    assert all(result["verified"].values())
    assert state["calls"] == 1


@pytest.mark.write_contract
def test_reorder_section_nested_parent_xml_preserves_ancestor_and_all_direct_children(monkeypatch):
    before = section_fixture(nested=True)
    after = reordered_snapshot(before, "section", ["sA", "sC", "sB"])
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    state = install_backend(monkeypatch, before, after)

    result = asyncio.run(reorder_section("sC", "C", "g", "sA", "modified-sC"))

    assert result["ok"] is True
    root = ET.fromstring(state["xml"])
    notebook = next(node for node in root.iter() if node.attrib.get("ID") == "n")
    group = next(node for node in notebook if node.attrib.get("ID") == "g")
    assert [node.attrib["ID"] for node in group] == ["sA", "sC", "sB"]


@pytest.mark.write_contract
def test_reorder_root_section_xml_preserves_mixed_direct_container_siblings(monkeypatch):
    before = section_fixture()
    after = reordered_snapshot(before, "section", ["sC", "sA", "sB"])
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    state = install_backend(monkeypatch, before, after)

    result = asyncio.run(reorder_section("sC", "C", "n", "", "modified-sC"))

    assert result["ok"] is True
    root = ET.fromstring(state["xml"])
    notebook = next(node for node in root.iter() if node.attrib.get("ID") == "n")
    assert [node.attrib["ID"] for node in notebook] == ["g", "sC", "sA", "sB"]


@pytest.mark.write_contract
def test_reorder_section_group_preserves_descendant_tree_and_content(monkeypatch):
    before = group_fixture()
    after = reordered_snapshot(before, "section_group", ["gC", "gA", "gB"])
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    state = install_backend(monkeypatch, before, after)

    result = asyncio.run(
        diagnostic_reorder_section_group("gC", "Group C", "n", "", "modified-gC")
    )

    assert result["ok"] is True
    assert [value["id"] for value in result["siblings"]] == ["gC", "gA", "gB"]
    root = ET.fromstring(state["xml"])
    notebook = next(node for node in root.iter() if node.attrib.get("ID") == "n")
    assert [node.attrib["ID"] for node in notebook] == ["gC", "gA", "gB"]


@pytest.mark.write_contract
def test_reorder_section_group_supports_shared_section_group_parent(monkeypatch):
    before = nested_group_fixture()
    after = nested_group_fixture("ACB")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    state = install_backend(monkeypatch, before, after)

    result = asyncio.run(
        diagnostic_reorder_section_group(
            "nestedC",
            "Nested Group C",
            "parent",
            "nestedA",
            "modified-nestedC",
        )
    )

    assert result["ok"] is True
    assert [value["id"] for value in result["siblings"]] == [
        "nestedA",
        "nestedC",
        "nestedB",
    ]
    root = ET.fromstring(state["xml"])
    notebook = next(node for node in root.iter() if node.attrib.get("ID") == "n")
    parent = next(node for node in notebook if node.attrib.get("ID") == "parent")
    assert [node.attrib["ID"] for node in parent] == [
        "nestedA",
        "nestedC",
        "nestedB",
    ]


@pytest.mark.write_contract
def test_reorder_section_group_rejects_non_container_parent(monkeypatch):
    before = nested_group_fixture()
    target = next(value for value in before if value["id"] == "nestedC")
    target["parent_id"] = "nestedSectionA"
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    state = install_backend(monkeypatch, before, before)

    result = asyncio.run(
        diagnostic_reorder_section_group(
            "nestedC",
            "Nested Group C",
            "nestedSectionA",
            "",
            "modified-nestedC",
        )
    )

    assert result["ok"] is False
    assert "active notebook or section_group" in result["error"]
    assert state["calls"] == 0


@pytest.mark.write_contract
def test_container_reorder_rejects_recycle_bin_target(monkeypatch):
    before = section_fixture()
    next(value for value in before if value["id"] == "sC")["is_in_recycle_bin"] = True
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    state = install_backend(monkeypatch, before, before)

    result = asyncio.run(reorder_section("sC", "C", "n", "", "modified-sC"))

    assert result["ok"] is False
    assert "recycle bin" in result["error"]
    assert state["calls"] == 0


@pytest.mark.write_contract
@pytest.mark.parametrize(
    ("tool", "gate"),
    [
        (reorder_section, "LOCAL_ONENOTE_ENABLE_WRITES"),
        (
            diagnostic_reorder_section_group,
            "LOCAL_ONENOTE_ENABLE_WRITES",
        ),
    ],
)
def test_container_reorder_is_fail_closed_behind_independent_gate(monkeypatch, tool, gate):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.delenv(gate, raising=False)
    monkeypatch.setattr(
        server.services.mutations,
        "call",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("COM must not be called")),
    )
    args = ("sA", "A", "n") if tool is reorder_section else ("gA", "Group A", "n")

    result = asyncio.run(tool(*args))

    assert result["ok"] is False
    assert result["code"] == "policy_disabled"


def test_section_group_diagnostic_reorder_is_fail_closed_without_internal_gate(monkeypatch):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.delenv(
        "LOCAL_ONENOTE_ENABLE_INTERNAL_SECTION_GROUP_REORDER", raising=False
    )
    monkeypatch.setattr(
        server.services.mutations,
        "call",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("COM must not be called")),
    )

    result = asyncio.run(
        diagnostic_reorder_section_group("gA", "Group A", "n")
    )

    assert result["ok"] is False
    assert result["code"] == "policy_disabled"


@pytest.mark.write_contract
@pytest.mark.parametrize(
    ("after_id", "message"),
    [
        ("sC", "cannot equal"),
        ("pA", "another section"),
        ("sOther", "same parent"),
        ("missing", "does not exist"),
        ("sDeleted", "recycle bin"),
    ],
)
def test_reorder_section_rejects_invalid_predecessor_before_mutation(monkeypatch, after_id, message):
    before = section_fixture()
    before.extend(
        [
            item("section_group", "other", "n", name="Other"),
            item("section", "sOther", "other", name="Other Section"),
            item("section", "sDeleted", "n", name="Deleted", recycle=True),
        ]
    )
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    state = install_backend(monkeypatch, before, before)

    result = asyncio.run(reorder_section("sC", "C", "n", after_id, "modified-sC"))

    assert result["ok"] is False
    assert message in result["error"]
    assert state["calls"] == 0


@pytest.mark.write_contract
@pytest.mark.parametrize("field", ["name", "parent", "modified"])
def test_reorder_section_rejects_confirmation_mismatch(monkeypatch, field):
    before = section_fixture()
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    state = install_backend(monkeypatch, before, before)
    name = "wrong" if field == "name" else "C"
    parent = "wrong" if field == "parent" else "n"
    modified = "wrong" if field == "modified" else "modified-sC"

    result = asyncio.run(reorder_section("sC", name, parent, "sA", modified))

    assert result["ok"] is False
    assert "Confirmation mismatch" in result["error"]
    assert state["calls"] == 0


@pytest.mark.write_contract
@pytest.mark.parametrize("failure", ["parent", "siblings", "sibling_added", "descendants", "content"])
def test_reorder_section_fails_closed_on_readback_invariant_change(monkeypatch, failure):
    before = section_fixture()
    after = reordered_snapshot(before, "section", ["sA", "sC", "sB"])
    if failure == "parent":
        next(value for value in after if value["id"] == "sC")["parent_id"] = "g"
    elif failure == "siblings":
        after = [value for value in after if value["id"] != "sB"]
    elif failure == "sibling_added":
        after.append(item("section", "sAdded", "n", name="Added"))
    elif failure == "descendants":
        next(value for value in after if value["id"] == "pC")["order"] = 1
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    state = install_backend(
        monkeypatch,
        before,
        after,
        page_xml_after="pC" if failure == "content" else None,
    )

    result = asyncio.run(reorder_section("sC", "C", "n", "sA", "modified-sC"))

    assert result["ok"] is False
    assert result["partial"] is True
    if failure in {"parent", "siblings", "sibling_added"}:
        assert result["code"] == "partial_failure"
        assert result["reconciliation"] == "partially_applied"
        assert result["observed_outcome"] == "partially_applied"
        assert result["retry_safety"] == "do_not_replay"
    else:
        assert result["code"] == "onenote_convergence_timeout"
        assert result["reconciliation"] == "indeterminate"
    assert result["manual_recovery_required"] is True
    assert state["calls"] == 1


@pytest.mark.write_contract
@pytest.mark.parametrize("failure", ["descendant_parent", "content"])
def test_reorder_section_group_fails_on_descendant_or_content_change(monkeypatch, failure):
    before = group_fixture()
    after = reordered_snapshot(before, "section_group", ["gC", "gA", "gB"])
    if failure == "descendant_parent":
        next(value for value in after if value["id"] == "sC")["parent_id"] = "gA"
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    state = install_backend(
        monkeypatch,
        before,
        after,
        page_xml_after="pC" if failure == "content" else None,
    )

    result = asyncio.run(
        diagnostic_reorder_section_group(
            "gC", "Group C", "n", "", "modified-gC"
        )
    )

    assert result["ok"] is False
    assert result["code"] == "onenote_convergence_timeout"
    assert state["calls"] == 1


@pytest.mark.write_contract
def test_reorder_section_bridge_failure_is_not_retried(monkeypatch):
    before = section_fixture()
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    state = install_backend(monkeypatch, before, before, bridge_error=RuntimeError("bridge failed"))

    result = asyncio.run(reorder_section("sC", "C", "n", "sA", "modified-sC"))

    assert result["ok"] is False
    assert state["calls"] == 1
