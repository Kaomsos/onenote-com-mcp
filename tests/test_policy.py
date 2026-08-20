import pytest

from local_onenote_mcp.policy import (
    BatchMutationBudget,
    CopyBudget,
    MutationPolicy,
    SearchBudget,
)


PUBLIC_GATE_ENV = (
    "LOCAL_ONENOTE_ENABLE_CREATE",
    "LOCAL_ONENOTE_ENABLE_WRITES",
    "LOCAL_ONENOTE_ENABLE_DELETES",
    "LOCAL_ONENOTE_ENABLE_ORGANIZE",
    "LOCAL_ONENOTE_ENABLE_LOCAL_FILE_IO",
    "LOCAL_ONENOTE_ENABLE_UI_CONTROL",
    "LOCAL_ONENOTE_ENABLE_NOTEBOOK_LIFECYCLE",
)


def test_public_authorization_is_disabled_by_default(monkeypatch):
    for name in (
        *PUBLIC_GATE_ENV,
        "LOCAL_ONENOTE_ENABLE_PERMANENT_DELETES",
        "LOCAL_ONENOTE_ENABLE_INTERNAL_SECTION_GROUP_REORDER",
        "LOCAL_ONENOTE_ENABLE_RAW_XML",
    ):
        monkeypatch.delenv(name, raising=False)

    policy = MutationPolicy.current()

    assert policy.create_enabled is False
    assert policy.writes_enabled is False
    assert policy.deletes_enabled is False
    assert policy.organize_enabled is False
    assert policy.local_file_io_enabled is False
    assert policy.ui_control_enabled is False
    assert policy.notebook_lifecycle_enabled is False
    for requirement in (
        policy.require_create,
        policy.require_write,
        policy.require_delete,
        policy.require_organize,
        policy.require_copy,
        policy.require_move,
        policy.require_local_file_io,
        policy.require_ui_control,
        policy.require_notebook_lifecycle,
    ):
        with pytest.raises(PermissionError):
            requirement()


def test_legacy_copy_experimental_and_move_switches_are_not_aliases(monkeypatch):
    for name in PUBLIC_GATE_ENV:
        monkeypatch.delenv(name, raising=False)
    for name in (
        "LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REPARENT",
        "LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REPARENT_SECTION",
        "LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REORDER_SECTION",
        "LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REORDER_SECTION_GROUP",
        "LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_COPY",
        "LOCAL_ONENOTE_ENABLE_COPY",
        "LOCAL_ONENOTE_ENABLE_MOVE",
        "LOCAL_ONENOTE_ENABLE_MOVE_PAGE",
        "LOCAL_ONENOTE_ENABLE_MOVE_CONTAINERS",
    ):
        monkeypatch.setenv(name, "true")

    policy = MutationPolicy.current()
    assert policy.organize_enabled is False
    assert policy.create_enabled is False
    with pytest.raises(PermissionError):
        policy.require_organize()
    with pytest.raises(PermissionError):
        policy.require_copy()
    with pytest.raises(PermissionError):
        policy.require_move()


@pytest.mark.parametrize(
    ("writes", "organize", "allowed"),
    [(False, False, False), (False, True, False), (True, False, False), (True, True, True)],
)
def test_organize_permission_matrix(monkeypatch, writes, organize, allowed):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", str(writes))
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_ORGANIZE", str(organize))
    if allowed:
        MutationPolicy.current().require_organize()
    else:
        with pytest.raises(PermissionError):
            MutationPolicy.current().require_organize()


@pytest.mark.parametrize(
    ("writes", "create", "allowed"),
    [(False, False, False), (False, True, False), (True, False, False), (True, True, True)],
)
def test_copy_permission_matrix(monkeypatch, writes, create, allowed):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", str(writes))
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_CREATE", str(create))
    if allowed:
        MutationPolicy.current().require_copy()
    else:
        with pytest.raises(PermissionError):
            MutationPolicy.current().require_copy()


@pytest.mark.parametrize(
    "missing",
    [
        "LOCAL_ONENOTE_ENABLE_WRITES",
        "LOCAL_ONENOTE_ENABLE_DELETES",
        "LOCAL_ONENOTE_ENABLE_CREATE",
    ],
)
def test_move_permission_matrix_rejects_each_missing_gate(monkeypatch, missing):
    for name in (
        "LOCAL_ONENOTE_ENABLE_WRITES",
        "LOCAL_ONENOTE_ENABLE_DELETES",
        "LOCAL_ONENOTE_ENABLE_CREATE",
    ):
        monkeypatch.setenv(name, "true")
    monkeypatch.setenv(missing, "false")
    with pytest.raises(PermissionError):
        MutationPolicy.current().require_move()


def test_move_requires_only_create_writes_and_deletes(monkeypatch):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_CREATE", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    MutationPolicy.current().require_move()


def test_section_reorder_uses_writes_without_an_experimental_gate(monkeypatch):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REORDER_SECTION", "false")
    MutationPolicy.current().require_section_reorder()


def test_internal_section_group_reorder_keeps_an_independent_non_product_gate(monkeypatch):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.delenv(
        "LOCAL_ONENOTE_ENABLE_INTERNAL_SECTION_GROUP_REORDER", raising=False
    )
    with pytest.raises(PermissionError):
        MutationPolicy.current().require_section_reorder("section_group")

    monkeypatch.setenv(
        "LOCAL_ONENOTE_ENABLE_INTERNAL_SECTION_GROUP_REORDER", "true"
    )
    MutationPolicy.current().require_section_reorder("section_group")


@pytest.mark.parametrize(
    ("env_name", "method_name"),
    [
        ("LOCAL_ONENOTE_ENABLE_LOCAL_FILE_IO", "require_local_file_io"),
        ("LOCAL_ONENOTE_ENABLE_UI_CONTROL", "require_ui_control"),
        ("LOCAL_ONENOTE_ENABLE_NOTEBOOK_LIFECYCLE", "require_notebook_lifecycle"),
    ],
)
def test_effect_gate_is_independent_and_default_closed(monkeypatch, env_name, method_name):
    monkeypatch.delenv(env_name, raising=False)
    with pytest.raises(PermissionError):
        getattr(MutationPolicy.current(), method_name)()
    monkeypatch.setenv(env_name, "true")
    getattr(MutationPolicy.current(), method_name)()


def test_search_budget_reads_bounded_environment_values(monkeypatch):
    monkeypatch.setenv("LOCAL_ONENOTE_MAX_SEARCH_PAGES", "25")
    monkeypatch.setenv("LOCAL_ONENOTE_MAX_SEARCH_TOTAL_CHARS", "5000")
    budget = SearchBudget.current()
    assert budget.max_pages == 25
    assert budget.max_total_chars == 5000


def test_search_candidate_budget_defaults_to_one_thousand(monkeypatch):
    monkeypatch.delenv("LOCAL_ONENOTE_MAX_SEARCH_PAGES", raising=False)
    assert SearchBudget.current().max_pages == 1000


def test_copy_budget_reads_bounded_environment_values(monkeypatch):
    monkeypatch.setenv("LOCAL_ONENOTE_MAX_COPY_PAGES", "25")
    monkeypatch.setenv("LOCAL_ONENOTE_MAX_COPY_TOTAL_XML_BYTES", "5000")
    budget = CopyBudget.current()
    assert budget.max_pages == 25
    assert budget.max_total_xml_bytes == 5000


def test_batch_mutation_budget_is_independent_and_reads_bounded_environment_values(
    monkeypatch,
):
    monkeypatch.setenv("LOCAL_ONENOTE_MAX_COPY_PAGES", "3")
    monkeypatch.setenv("LOCAL_ONENOTE_MAX_BATCH_EFFECTIVE_PAGES", "25")
    monkeypatch.setenv("LOCAL_ONENOTE_MAX_BATCH_DIRECT_SIBLINGS", "5000")

    budget = BatchMutationBudget.current()

    assert budget.max_effective_pages == 25
    assert budget.max_direct_siblings == 5000
    assert CopyBudget.current().max_pages == 3
