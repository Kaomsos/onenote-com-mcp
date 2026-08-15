from __future__ import annotations

import pytest

from local_onenote_mcp.onenote_errors import (
    OneNoteFileUnavailableError,
    OneNoteModalUIBlockedError,
    OneNoteNotYetSynchronizedError,
    OneNoteObjectUnavailableError,
    OneNoteOperationTimeoutError,
)
from local_onenote_mcp.services.mutation_control import (
    MUTATION_ATTEMPT_POLICY_BINDINGS,
    MUTATION_ATTEMPT_POLICIES,
    MutationAttemptExecutor,
    MutationAttemptPolicy,
    MutationIdentityPolicy,
    MutationReplayPolicy,
    ReconciliationState,
    mutation_attempt_policy,
)
from local_onenote_mcp.services.errors import MutationFailure, MutationPreflightFailure
from local_onenote_mcp.tools.responses import caught


def test_attempt_policy_catalog_is_explicit_and_excludes_multistage_and_nonmutation() -> None:
    assert {
        "update_page_title",
        "rename_resource",
        "reorder_page",
        "reorder_section",
        "append_to_page",
        "add_image_to_page",
        "delete_page_content",
        "delete_hierarchy",
        "close_notebook",
        "reparent_page",
        "reparent_section",
        "reparent_section_group",
    } == set(MUTATION_ATTEMPT_POLICIES)
    for excluded in (
        "create_page",
        "copy_page",
        "move_page",
        "replace_page_body",
        "sync_notebook",
        "open_hierarchy",
        "publish_object",
        "navigate_to",
    ):
        assert excluded not in MUTATION_ATTEMPT_POLICIES

    assert all(
        policy.replay_policy is MutationReplayPolicy.NEVER
        for policy in MUTATION_ATTEMPT_POLICIES.values()
    )


def test_public_attempt_policy_inventory_is_complete_and_references_known_policies() -> None:
    assert set(MUTATION_ATTEMPT_POLICY_BINDINGS) == {
        "update_page_title",
        "rename_section",
        "rename_section_group",
        "reorder_page",
        "reorder_section",
        "append_to_page",
        "add_image_to_page",
        "delete_page_content",
        "delete_page",
        "delete_section",
        "delete_section_group",
        "close_notebook",
        "reparent_page",
        "reparent_section",
        "reparent_section_group",
    }
    assert set(MUTATION_ATTEMPT_POLICY_BINDINGS.values()).issubset(
        MUTATION_ATTEMPT_POLICIES
    )


def test_reparent_policy_is_execute_once_and_forbids_readiness_side_effects() -> None:
    policy = mutation_attempt_policy("reparent_page")

    assert policy.replay_policy is MutationReplayPolicy.NEVER
    assert policy.max_execute_attempts == 1
    assert set(policy.forbidden_backend_operations) == {
        "sync_hierarchy",
        "close_notebook",
        "open_hierarchy",
        "filesystem_readiness_probe",
    }


def test_execute_once_reconciles_backend_error_to_applied_without_replay() -> None:
    calls = 0

    def execute() -> None:
        nonlocal calls
        calls += 1
        raise OneNoteNotYetSynchronizedError("safe", operation="update_hierarchy")

    outcome = MutationAttemptExecutor().execute(
        mutation_attempt_policy("reparent_page"),
        execute=execute,
        observe=lambda: "post",
        is_pre_state=lambda value: value == "pre",
        is_post_state=lambda value: value == "post",
    )

    assert calls == 1
    assert outcome.state is ReconciliationState.APPLIED
    assert outcome.summary()["execute_error_reconciled"] is True
    assert outcome.summary()["mutation_replayed"] is False
    assert outcome.summary()["retry_safety"] == "not_needed"


def test_execute_once_not_applied_has_typed_user_action_without_replay() -> None:
    calls = 0

    def execute() -> None:
        nonlocal calls
        calls += 1
        raise OneNoteFileUnavailableError("safe", operation="update_hierarchy")

    outcome = MutationAttemptExecutor().execute(
        mutation_attempt_policy("reparent_section"),
        execute=execute,
        observe=lambda: "pre",
        is_pre_state=lambda value: value == "pre",
        is_post_state=lambda value: value == "post",
    )

    assert calls == 1
    assert outcome.state is ReconciliationState.NOT_APPLIED
    assert outcome.failure_details() == {
        "mutation_stage": "reconciliation",
        "mutation_attempted": True,
        "mutation_attempts": 1,
        "mutation_replayed": False,
        "observed_outcome": "not_applied",
        "preflight_state": "logical_ready",
        "persistence_checkpoint": "not_observable",
        "retry_safety": "new_call_after_user_action",
        "recommended_action": (
            "close_and_reopen_the_notebook_in_onenote_then_submit_a_new_call"
        ),
        "manual_recovery_required": False,
        "observation_attempts": 1,
    }


def test_explicit_future_policy_replays_once_only_after_exact_prestate() -> None:
    calls = 0
    state = "pre"

    def execute() -> None:
        nonlocal calls, state
        calls += 1
        if calls == 1:
            raise OneNoteNotYetSynchronizedError("safe", operation="update_page_content")
        state = "post"

    policy = MutationAttemptPolicy(
        policy_id="future_exact_prestate_operation",
        replay_policy=MutationReplayPolicy.EXACT_PRESTATE_TYPED_TRANSIENT,
        identity_policy=MutationIdentityPolicy.PRESERVED,
        observer_description="complete_test_state",
        partial_boundary_description="any state other than exact pre or post",
    )
    outcome = MutationAttemptExecutor().execute(
        policy,
        execute=execute,
        observe=lambda: state,
        is_pre_state=lambda value: value == "pre",
        is_post_state=lambda value: value == "post",
    )

    assert calls == 2
    assert outcome.state is ReconciliationState.APPLIED
    assert outcome.summary()["mutation_replayed"] is True
    assert outcome.summary()["execution_succeeded"] is True
    assert outcome.summary()["execute_error_reconciled"] is False


def test_digest_only_content_observer_cannot_reconcile_execute_error_as_success() -> None:
    outcome = MutationAttemptExecutor().execute(
        mutation_attempt_policy("append_to_page"),
        execute=lambda: (_ for _ in ()).throw(
            OneNoteNotYetSynchronizedError(
                "safe", operation="update_page_content"
            )
        ),
        observe=lambda: "some_changed_digest",
        is_pre_state=lambda value: value == "before_digest",
        is_post_state=lambda value: value != "before_digest",
    )

    assert outcome.state is ReconciliationState.INDETERMINATE
    assert outcome.summary()["execute_error_reconciled"] is False
    assert outcome.summary()["retry_safety"] == "do_not_replay"


def test_reconciliation_observation_may_retry_once_but_mutation_does_not() -> None:
    execute_calls = 0
    observe_calls = 0

    def execute() -> None:
        nonlocal execute_calls
        execute_calls += 1
        raise OneNoteModalUIBlockedError("safe", operation="update_hierarchy")

    def observe() -> str:
        nonlocal observe_calls
        observe_calls += 1
        if observe_calls == 1:
            raise RuntimeError("transient evidence read")
        return "pre"

    outcome = MutationAttemptExecutor().execute(
        mutation_attempt_policy("reparent_page"),
        execute=execute,
        observe=observe,
        is_pre_state=lambda value: value == "pre",
        is_post_state=lambda value: value == "post",
        retry_observation_if=lambda exc: isinstance(exc, RuntimeError),
    )

    assert execute_calls == 1
    assert observe_calls == 2
    assert outcome.state is ReconciliationState.NOT_APPLIED
    assert outcome.summary()["observation_attempts"] == 2
    assert outcome.summary()["recommended_action"] == (
        "close_blocking_onenote_dialog_then_submit_a_new_call"
    )


def test_catalog_lookup_fails_closed_for_unregistered_operation() -> None:
    with pytest.raises(KeyError, match="No mutation attempt policy"):
        mutation_attempt_policy("copy_page")


def test_outcome_summary_is_content_free_even_when_observation_contains_content() -> None:
    secret_content = "PRIVATE PAGE BODY"
    outcome = MutationAttemptExecutor().execute(
        mutation_attempt_policy("reparent_page"),
        execute=lambda: None,
        observe=lambda: {"state": "post", "content": secret_content},
        is_pre_state=lambda value: value["state"] == "pre",
        is_post_state=lambda value: value["state"] == "post",
    )

    rendered = repr(outcome.summary())
    assert secret_content not in rendered
    assert "PRIVATE" not in rendered
    assert "page_xml" not in outcome.summary()
    assert "observation" not in outcome.summary()


@pytest.mark.parametrize(
    ("error", "retry_safety", "recommended_action", "manual"),
    [
        (
            OneNoteObjectUnavailableError("safe", operation="update_hierarchy"),
            "new_call_after_read_only_refresh",
            "query_the_current_object_id_and_location_then_submit_a_new_call",
            False,
        ),
        (
            OneNoteOperationTimeoutError("safe", operation="update_hierarchy"),
            "new_call_after_read_only_confirmation",
            "refresh_confirmation_fields_then_submit_a_new_call",
            False,
        ),
        (
            RuntimeError("safe unknown failure"),
            "do_not_replay",
            "inspect_the_typed_error_and_current_state_before_any_new_call",
            True,
        ),
    ],
)
def test_not_applied_recovery_matrix_is_typed_and_content_free(
    error: Exception,
    retry_safety: str,
    recommended_action: str,
    manual: bool,
) -> None:
    outcome = MutationAttemptExecutor().execute(
        mutation_attempt_policy("reparent_section"),
        execute=lambda: (_ for _ in ()).throw(error),
        observe=lambda: "pre",
        is_pre_state=lambda value: value == "pre",
        is_post_state=lambda value: value == "post",
    )

    assert outcome.state is ReconciliationState.NOT_APPLIED
    assert outcome.recovery.retry_safety == retry_safety
    assert outcome.recovery.recommended_action == recommended_action
    assert outcome.recovery.manual_recovery_required is manual


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (
            MutationPreflightFailure(
                "safe preflight",
                mutation_stage="preflight",
                mutation_attempted=False,
            ),
            "validation_error",
        ),
        (
            MutationFailure(
                "safe failure",
                code="mutation_not_applied",
                mutation_stage="reconciliation",
                mutation_attempted=True,
            ),
            "mutation_not_applied",
        ),
    ],
)
def test_attempt_failures_preserve_stable_response_envelope(
    error: Exception, expected_code: str
) -> None:
    response = caught(error)

    assert response["ok"] is False
    assert response["complete"] is False
    assert response["code"] == expected_code
    assert response["mutation_stage"] in {"preflight", "reconciliation"}
    assert isinstance(response["mutation_attempted"], bool)
