from threading import Event
from types import SimpleNamespace

from Actions.dynamic_planner_action import DynamicPlannerAction
from Actions.dynamic_planner_services import PlannerApprovalService, PlannerFeedbackService
from dynamic_planner import PlannerDecision


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
        ensure_task_graph=lambda context, goal: calls.append(("ensure_task_graph", goal)),
        advance_progress=lambda labels, text, context=None: calls.append(("advance_progress", labels, text)),
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
        approve_pointer_decision=lambda context, planner_decision, path, rect, detections=None, sub_goal=None: (
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
        emit_planner_trace=lambda payload: calls.append(
            (
                "planner_trace",
                payload["focused_goal"],
                payload["visible_labels"],
                payload["ocr_text"],
                payload["decision"]["label"],
            )
        ),
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
    assert ("planner_trace", "focus:Gather resources", ["Gather Button"], "gather", "Gather Button") in calls
    assert ("execute", "click", 400) in calls
    assert ("wait_after_execution", 0.5) in calls
    assert ("record_success", "Gather Button", None) in calls


def test_record_wait_does_not_write_visual_memory(tmp_path):
    calls = []
    memory = SimpleNamespace(record_success=lambda *args, **kwargs: calls.append(("memory", args, kwargs)))
    session_logger = SimpleNamespace(
        record_action=lambda action_type, label, target_id, outcome, source: calls.append(
            ("session", action_type, label, target_id, outcome, source)
        )
    )
    service = PlannerFeedbackService(
        task_graph=SimpleNamespace(),
        planner=SimpleNamespace(),
        memory=memory,
        dataset=SimpleNamespace(),
        change_detector=SimpleNamespace(),
    )
    decision = PlannerDecision.from_mapping(
        {
            "action_type": "wait",
            "label": "observe",
            "confidence": 0.4,
            "reason": "Need more context.",
        }
    )

    service.record_wait(
        SimpleNamespace(session_logger=session_logger),
        tmp_path / "screen.png",
        decision,
        visible_labels=[],
    )

    assert calls == [("session", "wait", "observe", "", "success", "ai")]


def test_record_no_progress_feedback_annotates_stuck_map_toggle():
    calls = []
    service = PlannerFeedbackService(
        task_graph=SimpleNamespace(),
        planner=SimpleNamespace(
            _goal_requests_world_map=lambda goal: "world map" in goal.lower(),
            _goal_requests_search_interface=lambda goal: "search interface" in goal.lower(),
            _screen_looks_like_city=lambda labels, ocr_text: "technology research" in ocr_text.lower(),
            _screen_shows_search_interface=lambda ocr_text: "food" in ocr_text.lower() and "wood" in ocr_text.lower(),
            remember_planner_feedback=lambda context, decision, reason, prefix="REJECTED": calls.append(
                (decision["action_type"], decision["key_name"], reason, prefix)
            )
        ),
        memory=SimpleNamespace(),
        dataset=SimpleNamespace(),
        change_detector=SimpleNamespace(),
    )
    context = SimpleNamespace(
        extracted={
            "planner_last_decision": {
                "action_type": "key",
                "key_name": "space",
                "label": "world map toggle",
                "target_id": "",
            }
        }
    )

    service.record_no_progress_feedback(
        context,
        goal="[Step 1/6] Open the world map/resource search interface from the main city screen.",
        visible_labels=[],
        ocr_text="technology research blacksmith apprentice",
    )

    assert calls == [
        (
            "key",
            "space",
            "world_map_toggle_did_not_reach_map_view",
            "FAILED",
        )
    ]


def test_record_no_progress_feedback_marks_failed_search_hotkey():
    calls = []
    service = PlannerFeedbackService(
        task_graph=SimpleNamespace(),
        planner=SimpleNamespace(
            _goal_requests_world_map=lambda goal: "world map" in goal.lower(),
            _goal_requests_search_interface=lambda goal: "search interface" in goal.lower(),
            _screen_looks_like_city=lambda labels, ocr_text: "technology research" in ocr_text.lower(),
            _screen_shows_search_interface=lambda ocr_text: "food" in ocr_text.lower() and "wood" in ocr_text.lower(),
            remember_planner_feedback=lambda context, decision, reason, prefix="REJECTED": calls.append(
                (decision["action_type"], decision["key_name"], reason, prefix)
            ),
        ),
        memory=SimpleNamespace(),
        dataset=SimpleNamespace(),
        change_detector=SimpleNamespace(),
    )
    context = SimpleNamespace(
        extracted={
            "planner_last_decision": {
                "action_type": "key",
                "key_name": "f",
                "label": "resource search hotkey",
                "target_id": "",
            }
        }
    )

    service.record_no_progress_feedback(
        context,
        goal="[Step 1/6] Open the world map/resource search interface from the main city screen.",
        visible_labels=[],
        ocr_text="alliance resource occupy",
    )

    assert calls == [
        (
            "key",
            "f",
            "search_hotkey_did_not_open_resource_search",
            "FAILED",
        )
    ]


def test_low_confidence_approval_requires_manual_correction(tmp_path):
    event = Event()
    event.set()
    pending = {"event": event, "result": "approved", "corrected_point": None}
    context = SimpleNamespace(
        planner_autonomy_level=1,
        set_pending_planner_decision=lambda *args, **kwargs: pending,
        clear_pending_planner_decision=lambda: None,
    )
    decision = PlannerDecision.from_mapping(
        {
            "action_type": "click",
            "target_id": "ocr_1",
            "label": "Resource node",
            "confidence": 0.46,
            "reason": "Low confidence.",
            "x": 0.25,
            "y": 0.50,
        },
        source="ai_review",
    )
    service = PlannerApprovalService(memory=SimpleNamespace())

    approved, corrected = service.approve_pointer_decision(
        context,
        decision,
        tmp_path / "screen.png",
        _WindowRect(),
    )

    assert approved is None
    assert corrected is False
