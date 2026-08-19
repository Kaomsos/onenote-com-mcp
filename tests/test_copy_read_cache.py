"""CopyReadCache and HierarchySnapshot contracts."""

from __future__ import annotations

import contextvars

from local_onenote_mcp.hierarchy import parse_hierarchy
from local_onenote_mcp.services.backend_operation_classification import (
    current_mutation_epoch,
    notify_backend_operation,
    reset_mutation_epoch,
    restore_mutation_epoch,
)
from local_onenote_mcp.services.copy_read_cache import (
    CopyReadCache,
    HierarchySnapshot,
    current_copy_read_cache,
    restore_copy_read_cache,
    set_copy_read_cache,
)

from tests.test_copying import page_xml


class _HierarchyStub:
    def __init__(self, *, xml: str, calls: list[tuple[str, str]]) -> None:
        self._xml = xml
        self._calls = calls

    def hierarchy_xml(self, start_id: str = "", scope: str = "pages") -> str:
        self._calls.append(("get_hierarchy", f"{start_id}:{scope}"))
        return self._xml


class _PagesStub:
    def __init__(self, *, xml_by_key: dict[str, str], calls: list[tuple[str, str]]) -> None:
        self._xml_by_key = xml_by_key
        self._calls = calls

    def xml(self, page_id: str, page_info: str = "basic") -> str:
        self._calls.append(("get_page_content", f"{page_id}:{page_info}"))
        return self._xml_by_key[f"{page_id}:{page_info}"]


_HIERARCHY_XML = """<?xml version="1.0"?>
<one:Notebooks xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote">
  <one:Notebook name="NB" ID="{nb-id}" path="NB" lastModifiedTime="2026-01-01T00:00:00.000Z">
    <one:Section name="Active" ID="{sec-id}" path="NB/Active" lastModifiedTime="2026-01-01T00:00:00.000Z">
      <one:Page name="Page" ID="{page-id}" dateTime="2026-01-01T00:00:00.000Z" pageLevel="1" />
    </one:Section>
    <one:Section name="Recycle" ID="{rec-sec-id}" path="NB/Recycle" lastModifiedTime="2026-01-01T00:00:00.000Z" isRecycleBin="true">
      <one:Page name="Deleted" ID="{rec-page-id}" dateTime="2026-01-01T00:00:00.000Z" pageLevel="1" />
    </one:Section>
  </one:Notebook>
</one:Notebooks>"""


def test_hierarchy_snapshot_derives_active_and_recycle_views():
    items = parse_hierarchy(_HIERARCHY_XML)
    snapshot = HierarchySnapshot(
        start_id="",
        scope="pages",
        epoch=0,
        all_items=tuple(items),
    )
    active = snapshot.resources(include_recycle_bin=False)
    full = snapshot.resources(include_recycle_bin=True)
    assert any(item["id"] == "{page-id}" for item in active)
    assert any(item["id"] == "{rec-page-id}" for item in full)
    assert all(item.get("is_in_recycle_bin") is not True for item in active)


def test_hierarchy_snapshot_returns_copies_that_do_not_mutate_cache():
    items = parse_hierarchy(_HIERARCHY_XML)
    snapshot = HierarchySnapshot(
        start_id="",
        scope="pages",
        epoch=0,
        all_items=tuple(items),
    )
    first = snapshot.resources(include_recycle_bin=True)
    first[0]["name"] = "mutated"
    second = snapshot.resources(include_recycle_bin=True)
    assert second[0]["name"] != "mutated"
    resource = snapshot.resource("{page-id}", "page")
    resource["title"] = "mutated-title"
    assert snapshot.resource("{page-id}", "page")["title"] != "mutated-title"


def test_copy_read_cache_reuses_hierarchy_within_epoch_and_invalidates_after_mutation():
    hierarchy_calls: list[tuple[str, str]] = []
    page_calls: list[tuple[str, str]] = []
    cache = CopyReadCache(
        _HierarchyStub(xml=_HIERARCHY_XML, calls=hierarchy_calls),
        _PagesStub(
            xml_by_key={
                "{page-id}:all": page_xml("{page-id}", "Page", "Body"),
            },
            calls=page_calls,
        ),
    )
    token = reset_mutation_epoch()
    try:
        cache.resources(reason="plan_capture", include_recycle_bin=False)
        cache.resources(reason="destination_precondition", include_recycle_bin=False)
        cache.resource("{page-id}", "page", reason="source_confirmation")
        assert len(hierarchy_calls) == 1

        cache.get_page_derivation("{page-id}", "all", reason="plan_capture")
        cache.get_page_derivation("{page-id}", "all", reason="pre_write_target_observation")
        assert len(page_calls) == 1

        notify_backend_operation("update_page_content")
        cache.resources(reason="topology_verification", include_recycle_bin=False)
        cache.get_page_derivation("{page-id}", "all", reason="post_write_reconciliation")
        assert len(hierarchy_calls) == 2
        assert len(page_calls) == 2
    finally:
        restore_mutation_epoch(token)


def test_page_cache_keys_include_scope():
    page_calls: list[tuple[str, str]] = []
    cache = CopyReadCache(
        _HierarchyStub(xml=_HIERARCHY_XML, calls=[]),
        _PagesStub(
            xml_by_key={
                "{page-id}:all": page_xml("{page-id}", "Page", "Body"),
                "{page-id}:basic": page_xml("{page-id}", "Page", "Body"),
            },
            calls=page_calls,
        ),
    )
    token = reset_mutation_epoch()
    try:
        cache.get_page_derivation("{page-id}", "all", reason="plan_capture")
        cache.get_page_derivation("{page-id}", "basic", reason="plan_capture")
        assert len(page_calls) == 2
    finally:
        restore_mutation_epoch(token)


def test_copy_read_sessions_are_isolated_across_contexts():
    hierarchy_calls_a: list[tuple[str, str]] = []
    hierarchy_calls_b: list[tuple[str, str]] = []
    page_calls_a: list[tuple[str, str]] = []
    page_calls_b: list[tuple[str, str]] = []
    cache_a = CopyReadCache(
        _HierarchyStub(xml=_HIERARCHY_XML, calls=hierarchy_calls_a),
        _PagesStub(
            xml_by_key={"{page-id}:all": page_xml("{page-id}", "A", "Body")},
            calls=page_calls_a,
        ),
    )
    cache_b = CopyReadCache(
        _HierarchyStub(xml=_HIERARCHY_XML, calls=hierarchy_calls_b),
        _PagesStub(
            xml_by_key={"{page-id}:all": page_xml("{page-id}", "B", "Body")},
            calls=page_calls_b,
        ),
    )

    ctx_a = contextvars.copy_context()
    ctx_b = contextvars.copy_context()
    token_a_holder: list[object] = []
    token_b_holder: list[object] = []

    def start_a() -> None:
        token_a_holder.append(reset_mutation_epoch())
        token_a_holder.append(set_copy_read_cache(cache_a))
        assert current_copy_read_cache() is cache_a
        cache_a.resources(reason="plan_capture")
        cache_a.get_page_derivation("{page-id}", "all", reason="plan_capture")

    def start_b() -> None:
        token_b_holder.append(reset_mutation_epoch())
        token_b_holder.append(set_copy_read_cache(cache_b))
        assert current_copy_read_cache() is cache_b
        cache_b.resources(reason="plan_capture")
        cache_b.get_page_derivation("{page-id}", "all", reason="plan_capture")

    def finish_a() -> None:
        assert current_copy_read_cache() is cache_a
        cache_a.resources(reason="destination_precondition")
        restore_copy_read_cache(token_a_holder[1])  # type: ignore[arg-type]
        restore_mutation_epoch(token_a_holder[0])  # type: ignore[arg-type]
        assert current_copy_read_cache() is None

    def finish_b() -> None:
        assert current_copy_read_cache() is cache_b
        cache_b.resources(reason="destination_precondition")
        notify_backend_operation("create_new_page")
        assert current_mutation_epoch() == 1
        cache_b.resources(reason="topology_verification")
        restore_copy_read_cache(token_b_holder[1])  # type: ignore[arg-type]
        restore_mutation_epoch(token_b_holder[0])  # type: ignore[arg-type]
        assert current_copy_read_cache() is None

    ctx_a.run(start_a)
    ctx_b.run(start_b)
    ctx_a.run(finish_a)
    ctx_b.run(finish_b)

    assert len(hierarchy_calls_a) == 1
    assert len(page_calls_a) == 1
    assert len(hierarchy_calls_b) == 2
    assert len(page_calls_b) == 1
    assert current_copy_read_cache() is None


def test_stale_preflight_falls_back_to_live_hierarchy_read():
    from local_onenote_mcp.hierarchy import find_resource_by_id
    from local_onenote_mcp.services.pages import PageService

    hierarchy_calls: list[tuple[str, str]] = []
    hierarchy = _HierarchyStub(xml=_HIERARCHY_XML, calls=hierarchy_calls)

    class _LiveHierarchy:
        def resource(self, object_id: str, resource_type: str | None = None):
            hierarchy.hierarchy_xml()
            item = find_resource_by_id(
                parse_hierarchy(_HIERARCHY_XML),
                object_id,
                resource_type,
            )
            assert item is not None
            return item

    service = PageService.__new__(PageService)
    service.hierarchy = _LiveHierarchy()  # type: ignore[assignment]
    stale = HierarchySnapshot(
        start_id="",
        scope="pages",
        epoch=0,
        all_items=tuple(parse_hierarchy(_HIERARCHY_XML)),
    )
    token = reset_mutation_epoch()
    try:
        notify_backend_operation("create_new_page")
        assert current_mutation_epoch() == 1
        item = service.confirm(
            "{page-id}",
            expected_title="Page",
            expected_section_id="{sec-id}",
            preflight=stale,
        )
        assert item["id"] == "{page-id}"
        assert len(hierarchy_calls) == 1
    finally:
        restore_mutation_epoch(token)
