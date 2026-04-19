import json

from runtime_journal import RuntimeJournal, reconcile_runtime_journal_artifacts


def test_runtime_journal_checkpoint_tracks_last_committed_transition(tmp_path):
    journal = RuntimeJournal(
        run_id="runtime_session_test",
        output_dir=tmp_path,
        secret_key="ab" * 32,
    )

    step_id = journal.record_step_started(
        machine_id="machine_1",
        state_name="gather",
        action_name="DynamicPlanner",
    )
    decision_id = journal.record_decision_selected(
        step_id=step_id,
        machine_id="machine_1",
        state_name="gather",
        action_type="click",
        label="Farm",
        target_id="det_1",
        source="ai",
        confidence=0.93,
    )
    approval_id = journal.record_approval_requested(
        step_id=step_id,
        machine_id="machine_1",
        state_name="gather",
        decision_id=decision_id,
        action_type="click",
        label="Farm",
        target_id="det_1",
    )
    journal.record_approval_resolved(
        step_id=step_id,
        machine_id="machine_1",
        state_name="gather",
        decision_id=decision_id,
        approval_id=approval_id,
        outcome="approved",
    )
    input_id = journal.record_input_started(
        step_id=step_id,
        machine_id="machine_1",
        state_name="gather",
        decision_id=decision_id,
        action_type="click",
        label="Farm",
        target_id="det_1",
    )
    journal.record_input_completed(
        step_id=step_id,
        machine_id="machine_1",
        state_name="gather",
        decision_id=decision_id,
        input_id=input_id,
        action_type="click",
        outcome="success",
        label="Farm",
        target_id="det_1",
    )
    journal.record_transition_committed(
        step_id=step_id,
        machine_id="machine_1",
        state_name="gather",
        action_name="DynamicPlanner",
        event="action",
        result=True,
        next_state="finish",
        decision_id=decision_id,
        action_type="click",
        label="Farm",
        target_id="det_1",
    )

    checkpoint = json.loads(journal.paths.checkpoint_path.read_text(encoding="utf-8"))

    assert checkpoint["resume_checkpoint"]["verified"] is True
    assert checkpoint["resume_checkpoint"]["state_name"] == "gather"
    assert checkpoint["resume_checkpoint"]["next_state"] == "finish"
    assert checkpoint["resume_checkpoint"]["decision_id"] == decision_id
    assert checkpoint["resume_checkpoint"]["pending_tail_count"] == 0
    assert checkpoint["journal_integrity"]["last_committed_sequence"] == checkpoint["resume_checkpoint"]["last_committed_sequence"]


def test_reconcile_runtime_journal_artifacts_reports_uncommitted_tail(tmp_path):
    secret_key = "cd" * 32
    journal = RuntimeJournal(
        run_id="runtime_session_test",
        output_dir=tmp_path,
        secret_key=secret_key,
    )

    committed_step = journal.record_step_started(
        machine_id="machine_1",
        state_name="start",
        action_name="DynamicPlanner",
    )
    journal.record_transition_committed(
        step_id=committed_step,
        machine_id="machine_1",
        state_name="start",
        action_name="DynamicPlanner",
        event="action",
        result=True,
        next_state="finish",
    )

    pending_step = journal.record_step_started(
        machine_id="machine_1",
        state_name="finish",
        action_name="DynamicPlanner",
    )
    decision_id = journal.record_decision_selected(
        step_id=pending_step,
        machine_id="machine_1",
        state_name="finish",
        action_type="click",
        label="Next",
        target_id="det_2",
        source="ai",
        confidence=0.88,
    )
    input_id = journal.record_input_started(
        step_id=pending_step,
        machine_id="machine_1",
        state_name="finish",
        decision_id=decision_id,
        action_type="click",
        label="Next",
        target_id="det_2",
    )
    journal.record_input_completed(
        step_id=pending_step,
        machine_id="machine_1",
        state_name="finish",
        decision_id=decision_id,
        input_id=input_id,
        action_type="click",
        outcome="success",
        label="Next",
        target_id="det_2",
    )

    updates = reconcile_runtime_journal_artifacts(
        run_id="runtime_session_test",
        journal_path=journal.paths.journal_path,
        checkpoint_path=journal.paths.checkpoint_path,
        status="interrupted",
        end_reason="previous_run_incomplete",
        detail="Previous runtime session did not finalize cleanly.",
        secret_key=secret_key,
    )

    checkpoint = json.loads(journal.paths.checkpoint_path.read_text(encoding="utf-8"))

    assert updates["resume_checkpoint"]["verified"] is True
    assert updates["resume_checkpoint"]["next_state"] == "finish"
    assert updates["resume_checkpoint"]["pending_tail_count"] == 4
    assert updates["resume_checkpoint"]["pending_tail_events"] == [
        "step_started",
        "decision_selected",
        "input_started",
        "input_completed",
    ]
    assert checkpoint["terminal"]["status"] == "interrupted"
    journal_lines = [json.loads(line) for line in journal.paths.journal_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert journal_lines[-1]["event_type"] == "terminal"
