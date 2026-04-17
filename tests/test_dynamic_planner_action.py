from pathlib import Path
from types import SimpleNamespace

from dynamic_planner import PlannerDecision
from Actions.dynamic_planner_action import DynamicPlannerAction


class _WindowRect:
    left = 0
    top = 0
    width = 400
    height = 200


def test_dynamic_planner_action_orchestrates_services(tmp_path):
    screenshot_path = tmp_path / "planner_latest.png"
    decision = PlannerDecision.from_mapping(
        {
            "thought_process": "Need to gather.",
            "action_type": "click",
            "target_id": "det_1",
            "label": "Gather Button",
            "confidence": 0.91,
            "delay_seconds": 0.5,
            "reason": "The gather button is visible.",
            "x": 0.25,
            "y": 0.50,
        }
    )
    calls = []
    planner = SimpleNamespace(
        memory=SimpleNamespace(path=tmp_path / "vision_memory.json"),
        client=None,
        model="gpt-5.4-mini",
        plan_next=lambda *args, **kwargs: decision,
        close=lambda: None,
    )
    observation_service = SimpleNamespace(
        observe=lambda context: SimpleNamespace(
            screenshot=object(),
            window_rect=_WindowRect(),
            detections=[SimpleNamespace(label="Gather Button")],
        ),
        save_latest_screenshot=lambda _memory_path, _screenshot: screenshot_path,
        screen_change_context=lambda _screenshot: (True, "stuck warning"),
        read_planner_ocr=lambda _context, _screenshot: ("gather", []),
        read_resource_context=lambda _context: {"idle_march_slots": 1},
        visible_labels=lambda detections: [d.label for d in detections],
    )
    feedback_service = SimpleNamespace(
        ensure_task_graph=lambda goal: calls.append(("ensure_task_graph", goal)),
        advance_progress=lambda labels, text: calls.append(("advance_progress", labels, text)),
        mission_complete=lambda context: False,
        focused_goal=lambda goal: f"focus:{goal}",
        record_no_decision=lambda path, detections: calls.append(("record_no_decision", path, detections)),
        record_decision=lambda context, planner_decision: calls.append(("record_decision", planner_decision.label)),
        record_wait=lambda context, path, planner_decision, detections: calls.append(("record_wait", planner_decision.action_type)),
        record_failure=lambda context, planner_decision: calls.append(("record_failure", planner_decision.label)),
        record_success=lambda context, path, planner_decision, correction, detections: calls.append(
            ("record_success", planner_decision.label, correction)
        ),
    )
    approval_service = SimpleNamespace(
        approve_pointer_decision=lambda context, planner_decision, path, rect, detections=None: (
            planner_decision,
            False,
        )
    )
    execution_service = SimpleNamespace(
        execute=lambda context, planner_decision, rect: calls.append(("execute", planner_decision.action_type, rect.width)) or True,
        wait_after_execution=lambda delay, context: calls.append(("wait_after_execution", delay)) or True,
    )
    context = SimpleNamespace(
        planner_goal="Gather resources",
        emit_state=lambda _text: None,
        extracted={},
    )

    action = DynamicPlannerAction(
        planner=planner,
        memory=SimpleNamespace(),
        dataset=SimpleNamespace(),
        observation_service=observation_service,
        feedback_service=feedback_service,
        approval_service=approval_service,
        execution_service=execution_service,
    )

    assert action.execute(context) is True
    assert ("ensure_task_graph", "Gather resources") in calls
    assert ("record_decision", "Gather Button") in calls
    assert ("execute", "click", 400) in calls
    assert ("wait_after_execution", 0.5) in calls
    assert ("record_success", "Gather Button", None) in calls
