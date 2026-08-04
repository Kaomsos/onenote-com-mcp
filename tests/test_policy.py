import pytest

from local_onenote_mcp.policy import MutationPolicy, SearchBudget


def test_mutations_are_disabled_by_default(monkeypatch):
    for name in (
        "LOCAL_ONENOTE_ENABLE_WRITES",
        "LOCAL_ONENOTE_ENABLE_DELETES",
        "LOCAL_ONENOTE_ENABLE_PERMANENT_DELETES",
        "LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_MOVE_SECTION",
        "LOCAL_ONENOTE_ENABLE_RAW_XML",
    ):
        monkeypatch.delenv(name, raising=False)

    policy = MutationPolicy.current()

    assert policy.writes_enabled is False
    assert policy.deletes_enabled is False
    assert policy.permanent_deletes_enabled is False
    with pytest.raises(PermissionError):
        policy.require_write()
    with pytest.raises(PermissionError):
        policy.require_delete()


def test_search_budget_reads_bounded_environment_values(monkeypatch):
    monkeypatch.setenv("LOCAL_ONENOTE_MAX_SEARCH_PAGES", "25")
    monkeypatch.setenv("LOCAL_ONENOTE_MAX_SEARCH_TOTAL_CHARS", "5000")

    budget = SearchBudget.current()

    assert budget.max_pages == 25
    assert budget.max_total_chars == 5000
