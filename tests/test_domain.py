from local_onenote_mcp.domain import content_objects
from local_onenote_mcp.hierarchy import filter_resources, parse_hierarchy


HIERARCHY_XML = """<one:Notebooks xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote">
  <one:Notebook name="Work" ID="n" lastModifiedTime="2026-08-01T00:00:00Z">
    <one:SectionGroup name="Projects" ID="g">
      <one:Section name="Alpha" ID="s" isReadOnly="false">
        <one:Page name="Parent" ID="p1" pageLevel="1" />
        <one:Page name="Child" ID="p2" pageLevel="2" />
        <one:Page name="Sibling" ID="p3" pageLevel="1" unknownAttribute="not-public" />
      </one:Section>
    </one:SectionGroup>
  </one:Notebook>
</one:Notebooks>"""


def test_domain_hierarchy_has_typed_relationships_and_no_unknown_attributes():
    items = parse_hierarchy(HIERARCHY_XML)
    notebook, group, section, parent, child, sibling = items

    assert notebook["resource_type"] == "notebook"
    assert notebook["section_group_ids"] == ["g"]
    assert group["notebook_id"] == "n"
    assert group["parent_section_group_id"] is None
    assert section["parent_section_group_id"] == "g"
    assert section["page_count"] == 3
    assert parent["title"] == "Parent"
    assert "name" not in parent
    assert child["parent_page_id"] == "p1"
    assert parent["has_children"] is True
    assert sibling["order"] == 2
    assert "unknownAttribute" not in sibling


def test_filter_resources_returns_only_requested_static_type():
    pages = filter_resources(parse_hierarchy(HIERARCHY_XML), "page")
    assert [page["id"] for page in pages] == ["p1", "p2", "p3"]


def test_page_content_object_normalization_is_stable():
    objects = content_objects(
        "p1",
        [
            {
                "type": "Image",
                "object_id": "image-id",
                "container_object_id": "outline-id",
                "callback_id": "callback",
                "format": "png",
                "delete_supported": True,
                "delete_object_id": "image-id",
                "unrecognized": "not-public",
            }
        ],
    )

    assert objects == [
        {
            "id": "image-id",
            "page_id": "p1",
            "kind": "Image",
            "parent_object_id": None,
            "container_object_id": "outline-id",
            "callback_id": "callback",
            "media_type": "png",
            "can_delete": True,
            "delete_target_id": "image-id",
        }
    ]


def test_idless_binary_object_uses_page_scoped_callback_identity():
    objects = content_objects(
        "p1",
        [
            {
                "type": "Image",
                "container_object_id": "oe-id",
                "callback_id": "image-callback-id",
                "format": "png",
                "delete_supported": False,
                "delete_object_id": "outline-id",
            }
        ],
    )

    assert objects[0]["id"] == "image-callback-id"
    assert objects[0]["callback_id"] == "image-callback-id"
    assert objects[0]["container_object_id"] == "oe-id"
