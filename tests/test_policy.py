import pytest

from local_onenote_mcp.policy import CopyBudget, MutationPolicy, SearchBudget


def test_mutations_are_disabled_by_default(monkeypatch):
    for name in (
        "LOCAL_ONENOTE_ENABLE_WRITES",
        "LOCAL_ONENOTE_ENABLE_DELETES",
        "LOCAL_ONENOTE_ENABLE_PERMANENT_DELETES",
        "LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_MOVE_SECTION",
        "LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_COPY",
        "LOCAL_ONENOTE_ENABLE_RECONSTRUCTIVE_MOVE_PAGE",
        "LOCAL_ONENOTE_ENABLE_RAW_XML",
    ):
        monkeypatch.delenv(name, raising=False)

    policy = MutationPolicy.current()

    assert policy.writes_enabled is False
    assert policy.deletes_enabled is False
    assert policy.permanent_deletes_enabled is False
    assert policy.experimental_copy_enabled is False
    assert policy.reconstructive_move_page_enabled is False
    with pytest.raises(PermissionError):
        policy.require_write()
    with pytest.raises(PermissionError):
        policy.require_delete()
    with pytest.raises(PermissionError):
        policy.require_experimental_copy()
    with pytest.raises(PermissionError):
        policy.require_reconstructive_move_page()


def test_reconstructive_move_requires_all_three_mutation_capabilities(monkeypatch):
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_WRITES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_DELETES", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_COPY", "true")
    monkeypatch.setenv("LOCAL_ONENOTE_ENABLE_RECONSTRUCTIVE_MOVE_PAGE", "true")

    MutationPolicy.current().require_reconstructive_move_page()


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
        "LOCAL_ONENOTE_ENABLE_RECONSTRUCTIVE_MOVE_PAGE",
    ],
)
def test_reconstructive_move_permission_matrix_rejects_each_missing_gate(monkeypatch, missing):
    for name in (
        "LOCAL_ONENOTE_ENABLE_WRITES",
        "LOCAL_ONENOTE_ENABLE_DELETES",
        "LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_COPY",
        "LOCAL_ONENOTE_ENABLE_RECONSTRUCTIVE_MOVE_PAGE",
    ):
        monkeypatch.setenv(name, "true")
    monkeypatch.setenv(missing, "false")

    with pytest.raises(PermissionError):
        MutationPolicy.current().require_reconstructive_move_page()


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
