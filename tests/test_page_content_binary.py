from __future__ import annotations

import pytest

from local_onenote_mcp.services.pages import PageService


PAGE_XML = """<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="page-id">
<one:Image objectID="image-object-id" callbackID="internal-callback-id" format="png"/>
</one:Page>"""

IDLESS_IMAGE_XML = """<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="page-id">
<one:Outline objectID="outline-id"><one:OEChildren><one:OE objectID="oe-id">
<one:Image format="png"><one:CallbackID callbackID="image-callback-id"/></one:Image>
</one:OE></one:OEChildren></one:Outline>
</one:Page>"""


class _Bridge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def call(self, operation: str, **params):
        self.calls.append((operation, params))
        return {"base64": "cG5nLWJ5dGVz"}


class _Hierarchy:
    def resource(self, resource_id: str, resource_type: str):
        assert (resource_id, resource_type) == ("page-id", "page")
        return {"id": resource_id, "resource_type": resource_type}


def _service(monkeypatch, xml: str = PAGE_XML) -> tuple[PageService, _Bridge, list[str]]:
    bridge = _Bridge()
    service = PageService(bridge, _Hierarchy(), 10_000)
    page_infos: list[str] = []

    def page_xml(_page_id: str, page_info: str = "basic", **_kwargs) -> str:
        page_infos.append(page_info)
        return xml

    monkeypatch.setattr(service, "xml", page_xml)
    return service, bridge, page_infos


def test_binary_read_converts_public_object_id_to_internal_callback_id(monkeypatch) -> None:
    service, bridge, page_infos = _service(monkeypatch)

    result = service.get_content_object_binary("page-id", "image-object-id")

    assert result["object"]["id"] == "image-object-id"
    assert result["object"]["callback_id"] == "internal-callback-id"
    assert result["base64"] == "cG5nLWJ5dGVz"
    assert bridge.calls == [
        (
            "get_binary_page_content",
            {"page_id": "page-id", "callback_id": "internal-callback-id"},
        )
    ]
    assert page_infos == ["file_type"]


def test_binary_read_rejects_callback_id_as_public_object_identity(monkeypatch) -> None:
    service, bridge, page_infos = _service(monkeypatch)

    with pytest.raises(ValueError, match="page_content_object_id was not found"):
        service.get_content_object_binary("page-id", "internal-callback-id")

    assert bridge.calls == []
    assert page_infos == ["file_type"]


def test_idless_binary_object_uses_scoped_callback_identity(monkeypatch) -> None:
    service, bridge, page_infos = _service(monkeypatch, IDLESS_IMAGE_XML)

    listed = service.get_content_objects("page-id")
    image = next(item for item in listed["objects"] if item["kind"] == "Image")
    result = service.get_content_object_binary("page-id", image["id"])

    assert image["id"] == "image-callback-id"
    assert image["container_object_id"] == "oe-id"
    assert result["object"] == image
    assert bridge.calls == [
        (
            "get_binary_page_content",
            {"page_id": "page-id", "callback_id": "image-callback-id"},
        )
    ]
    assert page_infos == ["file_type", "file_type"]
