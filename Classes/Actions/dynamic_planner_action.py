from dataclasses import replace

from Actions.action import Action
from config_manager import ConfigManager
from detection_dataset import DetectionDataset
from dynamic_planner import DynamicPlanner
from input_controller import DelayPolicy, InputController
from logging_config import get_logger
from object_detector import create_detector
from ocr_service import OCRService
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
            corrected = replace(
                decision,
                x=float(corrected_point["x"]),
                y=float(corrected_point["y"]),
                confidence=1.0,
                source="manual",
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

        detections = self.detector.detect(screenshot)
        ocr_regions = self.ocr.read_regions(screenshot, purpose="planner")
        ocr_text = " ".join(region.text for region in ocr_regions if getattr(region, "text", "")).strip()
        if not ocr_text:
            ocr_text = self.ocr.read(screenshot, purpose="planner")
        decision = self.planner.plan_next(context, screenshot_path, detections, ocr_text, goal, ocr_regions=ocr_regions)
        if not decision:
            self.dataset.export_stub(screenshot_path, "planner_no_decision", detections=detections)
            return False

        context.extracted["planner_last_decision"] = decision.to_dict()
        context.emit_state(f"Planner: {decision.action_type}\n{decision.label}")

        if decision.action_type == "wait":
            self.memory.record_success(screenshot_path, decision, visible_labels=detections, source=decision.source)
            return DelayPolicy().wait(decision.delay_seconds, context=context)
        if decision.action_type == "stop":
            if getattr(context, "bot", None):
                context.bot.stop()
            return False

        decision, corrected = self._approved_decision(context, decision, screenshot_path, window_rect)
        context.clear_pending_planner_decision()
        if not decision:
            return False

        clicked = self._execute_click(context, decision, window_rect)
        if clicked:
            if not DelayPolicy().wait(decision.delay_seconds, context=context):
                return False
            if corrected:
                self.memory.record_correction(screenshot_path, decision, {"x": decision.x, "y": decision.y}, visible_labels=detections)
                self.dataset.export_correction(screenshot_path, decision, {"x": decision.x, "y": decision.y}, detections=detections)
            else:
                self.memory.record_success(screenshot_path, decision, visible_labels=detections, source=decision.source)
        else:
            self.memory.record_failure(decision.to_dict())
        return clicked
