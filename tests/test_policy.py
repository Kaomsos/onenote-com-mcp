import pytest

from local_onenote_mcp.policy import CopyBudget, MutationPolicy, SearchBudget


def test_mutations_are_disabled_by_default(monkeypatch):
    for name in (
        "LOCAL_ONENOTE_ENABLE_WRITES",
        "LOCAL_ONENOTE_ENABLE_DELETES",
        "LOCAL_ONENOTE_ENABLE_PERMANENT_DELETES",
        "LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REPARENT",
        "LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REORDER_SECTION",
        "LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REORDER_SECTION_GROUP",
        "LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_COPY",
        "LOCAL_ONENOTE_ENABLE_MOVE_PAGE",
        "LOCAL_ONENOTE_ENABLE_MOVE_CONTAINERS",
        "LOCAL_ONENOTE_ENABLE_RAW_XML",
    ):
        monkeypatch.delenv(name, raising=False)

    policy = MutationPolicy.current()

    assert policy.writes_enabled is False
    assert policy.deletes_enabled is False
    assert policy.permanent_deletes_enabled is False
    assert policy.experimental_copy_enabled is False
    assert policy.experimental_reorder_section_enabled is False
    assert policy.experimental_reorder_section_group_enabled is False
    assert policy.experimental_reparent_enabled is False
    assert policy.move_page_enabled is False
    assert policy.move_containers_enabled is False
    with pytest.raises(PermissionError):
        policy.require_write()
    with pytest.raises(PermissionError):
        policy.require_delete()
    with pytest.raises(PermissionError):
        policy.require_experimental_reparent()
    with pytest.raises(PermissionError):
        policy.require_experimental_copy()
    with pytest.raises(PermissionError):
        policy.require_experimental_reorder("section")
    with pytest.raises(PermissionError):
        policy.require_experimental_reorder("section_group")
    with pytest.raises(PermissionError):
        policy.require_move_page()
    with pytest.raises(PermissionError):
        policy.require_move_containers()


def test_move_page_requires_all_three_mutation_capabilities(monkeypatch):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_COPY", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_MOVE_PAGE", "true")

    MutationPolicy.current().require_move_page()


def test_move_containers_requires_its_independent_gate(monkeypatch):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_COPY", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_MOVE_CONTAINERS", "true")

    MutationPolicy.current().require_move_containers()


@pytest.mark.parametrize(
    ("writes", "reparent", "allowed"),
    [
        (False, False, False),
        (False, True, False),
        (True, False, False),
        (True, True, True),
    ],
)
def test_reparent_permission_matrix(monkeypatch, writes, reparent, allowed):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", str(writes))
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REPARENT", str(reparent))

    if allowed:
        MutationPolicy.current().require_experimental_reparent()
    else:
        with pytest.raises(PermissionError):
            MutationPolicy.current().require_experimental_reparent()


def test_legacy_section_only_reparent_switch_is_not_an_implicit_alias(monkeypatch):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REPARENT_SECTION", "true")
    monkeypatch.delenv("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REPARENT", raising=False)

    with pytest.raises(PermissionError, match="LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REPARENT"):
        MutationPolicy.current().require_experimental_reparent()


@pytest.mark.parametrize(
    ("resource_type", "env_name"),
    [
        ("section", "LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REORDER_SECTION"),
        ("section_group", "LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REORDER_SECTION_GROUP"),
    ],
)
def test_container_reorder_requires_write_and_its_independent_gate(monkeypatch, resource_type, env_name):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv(env_name, "true")

    MutationPolicy.current().require_experimental_reorder(resource_type)

    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "false")
    with pytest.raises(PermissionError):
        MutationPolicy.current().require_experimental_reorder(resource_type)


@pytest.mark.parametrize(
    ("writes", "copy", "allowed"),
    [
        (False, False, False),
        (False, True, False),
        (True, False, False),
        (True, True, True),
    ],
)
def test_copy_permission_matrix(monkeypatch, writes, copy, allowed):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", str(writes))
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_COPY", str(copy))

    if allowed:
        MutationPolicy.current().require_experimental_copy()
    else:
        with pytest.raises(PermissionError):
            MutationPolicy.current().require_experimental_copy()


@pytest.mark.parametrize(
    "missing",
    [
        "LOCAL_ONENOTE_ENABLE_WRITES",
        "LOCAL_ONENOTE_ENABLE_DELETES",
        "LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_COPY",
        "LOCAL_ONENOTE_ENABLE_MOVE_PAGE",
    ],
)
def test_move_page_permission_matrix_rejects_each_missing_gate(monkeypatch, missing):
    for name in (
        "LOCAL_ONENOTE_ENABLE_WRITES",
        "LOCAL_ONENOTE_ENABLE_DELETES",
        "LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_COPY",
        "LOCAL_ONENOTE_ENABLE_MOVE_PAGE",
    ):
        monkeypatch.setenv(name, "true")
    monkeypatch.setenv(missing, "false")

    with pytest.raises(PermissionError):
        MutationPolicy.current().require_move_page()


@pytest.mark.parametrize(
    "missing",
    [
        "LOCAL_ONENOTE_ENABLE_WRITES",
        "LOCAL_ONENOTE_ENABLE_DELETES",
        "LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_COPY",
        "LOCAL_ONENOTE_ENABLE_MOVE_CONTAINERS",
    ],
)
def test_move_containers_permission_matrix_rejects_each_missing_gate(monkeypatch, missing):
    for name in (
        "LOCAL_ONENOTE_ENABLE_WRITES",
        "LOCAL_ONENOTE_ENABLE_DELETES",
        "LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_COPY",
        "LOCAL_ONENOTE_ENABLE_MOVE_CONTAINERS",
    ):
        monkeypatch.setenv(name, "true")
    monkeypatch.setenv(missing, "false")

    with pytest.raises(PermissionError):
        MutationPolicy.current().require_move_containers()


def test_search_budget_reads_bounded_environment_values(monkeypatch):
    monkeypatch.setenv("LOCAL_ONENOTE_MAX_SEARCH_PAGES", "25")
    monkeypatch.setenv("LOCAL_ONENOTE_MAX_SEARCH_TOTAL_CHARS", "5000")

    budget = SearchBudget.current()

    assert budget.max_pages == 25
    assert budget.max_total_chars == 5000


def test_copy_budget_reads_bounded_environment_values(monkeypatch):
    monkeypatch.setenv("LOCAL_ONENOTE_MAX_COPY_PAGES", "25")
    monkeypatch.setenv("LOCAL_ONENOTE_MAX_COPY_TOTAL_XML_BYTES", "5000")

    budget = CopyBudget.current()

    assert budget.max_pages == 25
    assert budget.max_total_xml_bytes == 5000
