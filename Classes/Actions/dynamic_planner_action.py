import random

from Actions.action import Action
from config_manager import ConfigManager
from detection_dataset import DetectionDataset
from dynamic_planner import DynamicPlanner
from input_controller import DelayPolicy, InputController
from logging_config import get_logger
from object_detector import create_detector
from ocr_service import OCRService
from screen_change_detector import ScreenChangeDetector
from task_graph import TaskGraph
from vision_memory import VisionMemory
from window_handler import WindowHandler

LOGGER = get_logger(__name__)


class DynamicPlannerAction(Action):
    def __init__(self, goal=None, delay=0, post_delay=0.5):
        super().__init__(delay=delay, post_delay=post_delay)
        self.goal = goal
        self.window_handler = WindowHandler()
        self.detector = create_detector()
        self.ocr = OCRService()
        self.memory = VisionMemory()
        self.planner = DynamicPlanner(memory=self.memory)
        self.dataset = DetectionDataset()
        self.change_detector = ScreenChangeDetector()
        self.task_graph = TaskGraph()
        self._task_graph_initialized = False
        self._state_monitor = None

    def _get_state_monitor(self, context):
        if self._state_monitor is None:
            from state_monitor import GameStateMonitor
            self._state_monitor = GameStateMonitor(context=context)
        return self._state_monitor

    @property
    def status_text(self):
        return "DynamicPlanner\nAI guarded step"

    @staticmethod
    def _autonomy_level(context):
        try:
            return int(getattr(context, "planner_autonomy_level", 1))
        except Exception:
            return 1

    @staticmethod
    def _trusted_count():
        try:
            return int(ConfigManager().get("PLANNER_TRUSTED_SUCCESS_COUNT", "3"))
        except Exception:
            return 3

    @staticmethod
    def _absolute_point(decision, window_rect):
        return (
            int(round(window_rect.left + window_rect.width * decision.x)),
            int(round(window_rect.top + window_rect.height * decision.y)),
        )

    @staticmethod
    def _absolute_end_point(decision, window_rect):
        return (
            int(round(window_rect.left + window_rect.width * decision.end_x)),
            int(round(window_rect.top + window_rect.height * decision.end_y)),
        )

    def _wait_for_approval(self, context, decision, screenshot_path, window_rect):
        pending = context.set_pending_planner_decision(decision, screenshot_path=screenshot_path, window_rect=window_rect)
        event = pending.get("event")
        delay_policy = DelayPolicy()
        while event and not event.is_set():
            if not InputController.is_allowed(context):
                return None
            if not delay_policy.wait(0.1, context=context):
                return None
        return pending

    def _approved_decision(self, context, decision, screenshot_path, window_rect):
        autonomy = self._autonomy_level(context)
        if autonomy >= 3:
            return decision, False
        if autonomy == 2 and self.memory.is_trusted_label(decision.label, min_success=self._trusted_count()):
            context.emit_state("Planner trusted auto-click")
            return decision, False

        pending = self._wait_for_approval(context, decision, screenshot_path, window_rect)
        if not pending or pending.get("result") != "approved":
            LOGGER.warning("Dynamic planner action rejected by user.")
            return None, False

        corrected_point = pending.get("corrected_point")
        if corrected_point:
            corrected = decision.model_copy(
                update={
                    "x": float(corrected_point["x"]),
                    "y": float(corrected_point["y"]),
                    "confidence": 1.0,
                    "source": "manual",
                }
            )
            return corrected, True
        return decision, False

    def _execute_click(self, context, decision, window_rect):
        target_x, target_y = self._absolute_point(decision, window_rect)
        if not InputController.validate_bounds(target_x, target_y, window_rect):
            LOGGER.error(f"Dynamic planner target outside window: {target_x}, {target_y}")
            return False
        return InputController(context=context, coordinate_noise_px=0).click(
            target_x,
            target_y,
            window_rect=window_rect,
            remember_position=False,
            context=context,
        )

    def _execute_long_press(self, context, decision, window_rect):
        """Execute a long press by holding the mouse button for an extended time."""
        target_x, target_y = self._absolute_point(decision, window_rect)
        if not InputController.validate_bounds(target_x, target_y, window_rect):
            LOGGER.error(f"Dynamic planner long_press target outside window: {target_x}, {target_y}")
            return False
        controller = InputController(context=context, coordinate_noise_px=0)
        if not controller.smooth_move_to(target_x, target_y, context=context, window_rect=window_rect):
            return False
        # Hold for 1-2 seconds for a long press.
        hold_duration = random.uniform(1.0, 2.0)
        try:
            controller._mouse_down()
            if not DelayPolicy().wait(hold_duration, context=context):
                return False
            controller._mouse_up()
            return DelayPolicy().wait(0.1, context=context)
        except Exception as exc:
            LOGGER.error(f"Error during long press: {exc}")
            try:
                controller._mouse_up()
            except Exception:
                pass
            return False

    def _execute_drag(self, context, decision, window_rect):
        """Execute a drag from the start target to end target or direction."""
        import math

        start_x, start_y = self._absolute_point(decision, window_rect)
        if not InputController.validate_bounds(start_x, start_y, window_rect):
            LOGGER.error(f"Dynamic planner drag start outside window: {start_x}, {start_y}")
            return False

        # Determine end point: either from end_target or direction.
        if math.isfinite(decision.end_x) and math.isfinite(decision.end_y):
            end_x, end_y = self._absolute_end_point(decision, window_rect)
        elif decision.drag_direction:
            # Direction-based drag: move a percentage of the window in that direction.
            drag_dx = int(window_rect.width * 0.3)
            drag_dy = int(window_rect.height * 0.3)
            direction = decision.drag_direction.lower()
            end_x = start_x - drag_dx if "left" in direction else start_x + drag_dx if "right" in direction else start_x
            end_y = start_y - drag_dy if "up" in direction else start_y + drag_dy if "down" in direction else start_y
        else:
            LOGGER.error("Dynamic planner drag has no end target or direction.")
            return False

        if not InputController.validate_bounds(end_x, end_y, window_rect):
            LOGGER.error(f"Dynamic planner drag end outside window: {end_x}, {end_y}")
            return False

        controller = InputController(context=context, coordinate_noise_px=0)
        if not controller.smooth_move_to(start_x, start_y, context=context, window_rect=window_rect):
            return False

        try:
            controller._mouse_down()
            if not controller.smooth_move_to(end_x, end_y, context=context, window_rect=window_rect):
                controller._mouse_up()
                return False
            if not DelayPolicy().wait(0.1, context=context):
                controller._mouse_up()
                return False
            controller._mouse_up()
            return DelayPolicy().wait(0.2, context=context)
        except Exception as exc:
            LOGGER.error(f"Error during drag: {exc}")
            try:
                controller._mouse_up()
            except Exception:
                pass
            return False

    def _execute_key(self, context, decision, window_rect):
        """Execute a key press."""
        controller = InputController(context=context)
        LOGGER.info(f"Dynamic planner key press: {decision.key_name}")
        return controller.key_press(
            decision.key_name, hold_seconds=0.1, context=context
        )

    def _execute_type(self, context, decision, window_rect):
        """Execute typing text by pressing individual keys."""
        controller = InputController(context=context)
        LOGGER.info(f"Dynamic planner typing: {decision.text_content[:30]}...")
        for char in decision.text_content:
            if not InputController.is_allowed(context):
                return False
            if not controller.key_press(char, hold_seconds=0.05, context=context):
                return False
        return True

    def _read_resource_context(self, context):
        """Read march slot and action point data for planner context."""
        try:
            monitor = self._get_state_monitor(context)
            march_slots = monitor.count_idle_march_slots()
            action_points = monitor.read_action_points()
            result = {}
            if march_slots is not None:
                result["idle_march_slots"] = march_slots
            if action_points is not None:
                result["action_points"] = action_points
            return result if result else None
        except Exception as exc:
            LOGGER.warning("Resource context read failed: %s", exc)
            return None

    def execute(self, context=None):
        if not context:
            return False
        goal = self.goal or getattr(context, "planner_goal", None) or ConfigManager().get(
            "PLANNER_GOAL",
            "Safely continue the selected Rise of Kingdoms task.",
        )
        context.emit_state("DynamicPlanner\nobserving")
        screenshot, window_rect = self.window_handler.screenshot_window(context.window_title)
        if screenshot is None or window_rect is None:
            return False

        screenshot_path = self.planner.memory.path.parent / "planner_latest.png"
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        screenshot.save(screenshot_path)

        # Screen change detection.
        self.change_detector.record_screenshot(screenshot)
        screen_changed = self.change_detector.screen_changed_since_last()
        stuck_warning = self.change_detector.stuck_warning_text()

        # Task graph decomposition (once per mission).
        if not self._task_graph_initialized:
            self.task_graph.decompose(goal, openai_client=self.planner.client, model=self.planner.model)
            self._task_graph_initialized = True

        # Determine focused goal from task graph.
        focused_goal = self.task_graph.focused_goal_text(goal)

        detections = self.detector.detect(screenshot)
        ocr_regions = self.ocr.read_regions(screenshot, purpose="planner")
        ocr_text = " ".join(region.text for region in ocr_regions if getattr(region, "text", "")).strip()
        if not ocr_text:
            ocr_text = self.ocr.read(screenshot, purpose="planner")

        # Read resource context for planner.
        resource_context = self._read_resource_context(context)

        # Advance sub-goals based on current observations.
        visible_labels = [str(getattr(d, "label", "")) for d in detections if getattr(d, "label", "")]
        self.task_graph.advance_if_completed(visible_labels, ocr_text)
        # Re-compute focused goal after possible advancement.
        focused_goal = self.task_graph.focused_goal_text(goal)

        if self.task_graph.is_complete():
            context.emit_state("Mission complete")
            LOGGER.info("TaskGraph reports all sub-goals completed.")
            if getattr(context, "bot", None):
                context.bot.stop()
            return False

        decision = self.planner.plan_next(
            context, screenshot_path, detections, ocr_text, focused_goal,
            ocr_regions=ocr_regions,
            resource_context=resource_context,
            stuck_warning=stuck_warning,
            screen_changed=screen_changed,
        )
        if not decision:
            self.dataset.export_stub(screenshot_path, "planner_no_decision", detections=detections)
            return False

        # Track the action for loop detection.
        self.change_detector.record_action(
            decision.action_type,
            target_id=decision.target_id,
            label=decision.label,
        )

        context.extracted["planner_last_decision"] = decision.to_dict()
        context.emit_state(f"Planner: {decision.action_type}\n{decision.label}")

        if decision.action_type == "wait":
            self.memory.record_success(screenshot_path, decision, visible_labels=detections, source=decision.source)
            return DelayPolicy().wait(decision.delay_seconds, context=context)
        if decision.action_type == "stop":
            if getattr(context, "bot", None):
                context.bot.stop()
            return False

        # Actions that need approval: click, drag, long_press, key, type.
        needs_approval = decision.action_type in {"click", "drag", "long_press"}
        if needs_approval:
            decision, corrected = self._approved_decision(context, decision, screenshot_path, window_rect)
            context.clear_pending_planner_decision()
            if not decision:
                return False
        else:
            corrected = False

        # Execute the action based on type.
        handlers = {
            "click": self._execute_click,
            "long_press": self._execute_long_press,
            "drag": self._execute_drag,
            "key": self._execute_key,
            "type": self._execute_type,
        }
        handler = handlers.get(decision.action_type)
        executed = handler(context, decision, window_rect) if handler else False

        if executed:
            if not DelayPolicy().wait(decision.delay_seconds, context=context):
                return False
            if corrected:
                self.memory.record_correction(screenshot_path, decision, {"x": decision.x, "y": decision.y}, visible_labels=detections)
                self.dataset.export_correction(screenshot_path, decision, {"x": decision.x, "y": decision.y}, detections=detections)
            else:
                self.memory.record_success(screenshot_path, decision, visible_labels=detections, source=decision.source)
        else:
            self.memory.record_failure(decision.to_dict())
        return executed
