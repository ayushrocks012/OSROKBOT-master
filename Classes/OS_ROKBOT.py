"""Runtime runner for supervised OSROKBOT automation sessions.

This module owns the executor-backed run loop, heartbeat emission, foreground
and CAPTCHA safety gates, and shared observation reuse across workflow state
machines. It coordinates the runtime but delegates input execution,
observation, and planner logic to injected collaborators.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import FIRST_EXCEPTION, Future, ThreadPoolExecutor, TimeoutError, wait
from datetime import datetime
from pathlib import Path
from typing import Any

from config_manager import ConfigManager
from context import Context, ObservationSnapshot, record_stage_timing
from diagnostic_screenshot import save_diagnostic_screenshot
from emergency_stop import EmergencyStop
from gameplay_teaching import build_teaching_brief
from input_controller import InputController
from logging_config import get_logger, scoped_log_context
from object_detector import create_detector
from runtime_contracts import ConfigProvider, DetectionProvider, EmergencyStopController, WindowCaptureProvider
from runtime_payloads import HeartbeatPayload
from signal_emitter import SignalEmitter
from window_handler import WindowHandler

try:
    import win32process
except ImportError:
    win32process = None


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CAPTCHA_LABELS = {"captcha", "captchachest", "captcha_chest"}
LOGGER = get_logger(__name__)


class OSROKBOT:
    """Executor-backed runner for one or more automation state machines.

    The runner owns pause/stop events, injects a shared Context, and performs
    foreground/captcha safety checks before each workflow step. Planner and
    YOLO/VLM recovery now handle visible prompts without gameplay media assets.
    """

    def __init__(
        self,
        window_title: str,
        delay: float = 1,
        *,
        config: ConfigProvider | None = None,
        signal_emitter: SignalEmitter | None = None,
        window_handler: WindowCaptureProvider | None = None,
        input_controller: InputController | None = None,
        detector: DetectionProvider | None = None,
        emergency_stop: type[EmergencyStopController] = EmergencyStop,
    ) -> None:
        self.window_title = window_title
        self.delay = delay
        self.config = config or ConfigManager()
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.signal_emitter = signal_emitter or SignalEmitter()
        self.is_running = False
        self.all_threads_joined = True
        self._runner_executor: ThreadPoolExecutor | None = None
        self._runner_future: Future[None] | None = None
        self.window_handler = window_handler or WindowHandler()
        self.input_controller = input_controller or InputController(context=None)
        self.detector: DetectionProvider = detector or create_detector()
        self.emergency_stop = emergency_stop
        self._heartbeat_lock = threading.Lock()
        self._last_heartbeat_at = 0.0
        self._heartbeat_executor: ThreadPoolExecutor | None = None
        self._heartbeat_future: Future[None] | None = None
        self._cached_game_pid: int | None = None
        self._cached_game_pid_window_title: str | None = None
        self._cached_game_pid_hwnd: int | None = None
        self._active_context: Context | None = None

    def _emit_state(self, context: Context | None, state_text: str) -> None:
        if context:
            context.emit_state(state_text)
        elif self.signal_emitter:
            self.signal_emitter.state_changed.emit(state_text)

    @staticmethod
    def _session_logger(context: Context | None) -> Any | None:
        return getattr(context, "session_logger", None) if context is not None else None

    @classmethod
    def _session_log_context_fields(cls, context: Context | None) -> dict[str, Any]:
        session_logger = cls._session_logger(context)
        if session_logger and hasattr(session_logger, "log_context_fields"):
            fields = session_logger.log_context_fields()
            if isinstance(fields, dict):
                return {str(key): value for key, value in fields.items()}
        return {}

    def _record_session_error(
        self,
        context: Context | None,
        detail: str,
        *,
        stage: str,
        action_type: str = "",
        label: str = "",
        target_id: str = "",
    ) -> None:
        session_logger = self._session_logger(context)
        if session_logger and hasattr(session_logger, "record_error"):
            session_logger.record_error(
                detail,
                stage=stage,
                action_type=action_type,
                label=label,
                target_id=target_id,
            )

    def _record_session_warning(
        self,
        context: Context | None,
        detail: str,
        *,
        stage: str,
        label: str = "",
    ) -> None:
        session_logger = self._session_logger(context)
        if session_logger and hasattr(session_logger, "record_warning"):
            session_logger.record_warning(detail, stage=stage, label=label)

    def _mark_terminal(self, context: Context | None, status: str, end_reason: str, detail: str = "") -> None:
        session_logger = self._session_logger(context)
        if session_logger and hasattr(session_logger, "mark_terminal"):
            session_logger.mark_terminal(status, end_reason, detail=detail)

    def _prepare_context(self, context: Context | None) -> Context:
        active_context = context or Context(bot=self, window_title=self.window_title)
        active_context.bot = active_context.bot or self
        active_context.signal_emitter = active_context.signal_emitter or self.signal_emitter
        active_context.window_title = active_context.window_title or self.window_title
        if active_context.window_handler_factory is None:
            active_context.window_handler_factory = lambda: self.window_handler
        if active_context.input_controller_factory is None:
            active_context.input_controller_factory = lambda runtime_context: InputController(
                context=runtime_context,
                window_handler=self.window_handler,
            )
        if active_context.state_monitor_factory is None:
            def _state_monitor_factory(runtime_context: Context | None) -> Any:
                from state_monitor import GameStateMonitor

                return GameStateMonitor(
                    context=runtime_context,
                    config=self.config,
                    window_handler=self.window_handler,
                    input_controller=active_context.build_input_controller(),
                    detector=self.detector,
                )

            active_context.state_monitor_factory = _state_monitor_factory
        if active_context.config_factory is None:
            active_context.config_factory = lambda: self.config
        if active_context.recovery_executor_factory is None:
            def _recovery_executor_factory(_runtime_context: Context | None) -> Any:
                from ai_recovery_executor import AIRecoveryExecutor

                return AIRecoveryExecutor(detector=self.detector)

            active_context.recovery_executor_factory = _recovery_executor_factory
        active_context.teaching_brief = build_teaching_brief(
            enabled=bool(getattr(active_context, "teaching_mode_enabled", False)),
            profile_name=str(getattr(active_context, "teaching_profile_name", "guided_general")),
            operator_notes=str(getattr(active_context, "teaching_notes", "") or ""),
            mission=str(getattr(active_context, "planner_goal", "") or ""),
        )
        return active_context

    def _hardware_input_ready(self, context: Context | None = None) -> bool:
        if InputController.is_backend_available():
            return True
        message = InputController.backend_error()
        LOGGER.error("Interception hardware input is unavailable.")
        if message:
            LOGGER.error(message)
        LOGGER.warning("Install the Oblita Interception driver as Administrator, reboot, then run OSROKBOT again.")
        self._record_session_error(
            context,
            message or "Interception hardware input is unavailable.",
            stage="startup",
        )
        self._mark_terminal(context, "failed", "interception_unavailable", detail=message or "Interception unavailable")
        self._emit_state(context, "Interception unavailable")
        return False

    def _ensure_foreground(self, context: Context | None) -> bool:
        if self.stop_event.is_set() or self.pause_event.is_set():
            return False
        window_title = context.window_title if context and getattr(context, "window_title", None) else self.window_title
        if self.window_handler.ensure_foreground(window_title, wait_seconds=0.5):
            return True

        LOGGER.error("Game is not foreground; pausing automation before hardware input.")
        self._record_session_warning(
            context,
            "Game is not foreground; pausing automation before hardware input.",
            stage="foreground_guard",
        )
        self._emit_state(context, "Game not foreground - paused")
        self.pause_event.set()
        self.signal_emitter.pause_toggled.emit(True)
        return False

    @staticmethod
    def _config_path(value: str | os.PathLike[str] | None, default: str | os.PathLike[str]) -> Path:
        path = Path(value or default)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path

    def _heartbeat_path(self) -> Path:
        return self._config_path(
            self.config.get("WATCHDOG_HEARTBEAT_PATH"),
            PROJECT_ROOT / "data" / "heartbeat.json",
        )

    def _clear_game_pid_cache(self) -> None:
        self._cached_game_pid = None
        self._cached_game_pid_window_title = None
        self._cached_game_pid_hwnd = None

    def _game_pid(self, window_title: str) -> int | None:
        if win32process is None:
            self._clear_game_pid_cache()
            return None
        try:
            window = self.window_handler.get_window(window_title)
            if not window:
                self._clear_game_pid_cache()
                return None
            hwnd = int(window._hWnd)
            if (
                self._cached_game_pid is not None
                and self._cached_game_pid_window_title == window_title
                and self._cached_game_pid_hwnd == hwnd
            ):
                return self._cached_game_pid

            _, process_id = win32process.GetWindowThreadProcessId(hwnd)
            if not process_id:
                self._clear_game_pid_cache()
                return None

            self._cached_game_pid = int(process_id)
            self._cached_game_pid_window_title = window_title
            self._cached_game_pid_hwnd = hwnd
            return self._cached_game_pid
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            self._clear_game_pid_cache()
            return None

    @staticmethod
    def _record_runtime_timing(
        context: Context | None,
        stage: str,
        started_at: float,
        *,
        detail: str = "",
    ) -> None:
        if context is None:
            return
        record_timing = getattr(context, "record_runtime_timing", None)
        if callable(record_timing):
            record_timing(stage, (time.perf_counter() - started_at) * 1000.0, detail=detail)

    def _heartbeat_payload(self, context: Context | None, now: float) -> HeartbeatPayload:
        active_context = self._prepare_context(context)
        window_title = getattr(active_context, "window_title", None) or self.window_title
        return {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "timestamp_epoch": now,
            "bot_pid": os.getpid(),
            "game_pid": self._game_pid(window_title),
            "window_title": window_title,
            "mission": getattr(active_context, "planner_goal", ""),
            "autonomy_level": getattr(active_context, "planner_autonomy_level", 1),
            "repo_root": str(PROJECT_ROOT),
            "ui_entrypoint": str(PROJECT_ROOT / "Classes" / "UI.py"),
            "python_executable": sys.executable,
        }

    @staticmethod
    def _write_heartbeat_file(heartbeat_path: Path, payload: Mapping[str, Any]) -> None:
        heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = heartbeat_path.with_suffix(heartbeat_path.suffix + ".tmp")
        temp_path.write_text(json.dumps(dict(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")

        for attempt in range(3):
            try:
                temp_path.replace(heartbeat_path)
                return
            except PermissionError as exc:
                if attempt == 2:
                    LOGGER.error("Failed to write heartbeat to %s due to file lock: %s", heartbeat_path, exc)
                    raise
                time.sleep(0.1)

    def _shutdown_runner_executor(self) -> None:
        executor = self._runner_executor
        self._runner_executor = None
        if executor:
            executor.shutdown(wait=False, cancel_futures=True)

    def _ensure_heartbeat_executor(self) -> ThreadPoolExecutor:
        if self._heartbeat_executor is None:
            self._heartbeat_executor = ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="OSROKBOT-Heartbeat",
            )
        return self._heartbeat_executor

    def _shutdown_heartbeat_executor(self) -> None:
        with self._heartbeat_lock:
            executor = self._heartbeat_executor
            future = self._heartbeat_future
            self._heartbeat_executor = None
            self._heartbeat_future = None

        if future and not future.done():
            future.cancel()
        if executor:
            executor.shutdown(wait=False, cancel_futures=True)

    @staticmethod
    def _log_future_exception(future: Future[Any]) -> None:
        try:
            future.result()
        except Exception as exc:
            LOGGER.warning("Background heartbeat write failed: %s", exc)

    def write_heartbeat(self, context: Context | None = None, force: bool = False) -> bool:
        """Schedule a best-effort heartbeat refresh for watchdog consumers.

        Args:
            context: Optional active runtime context used to populate mission
                and autonomy metadata.
            force: When `True`, bypass the normal minimum write interval.

        Returns:
            bool: `True` when the heartbeat was already fresh or a background
            write was successfully scheduled.
        """

        now = time.time()
        with self._heartbeat_lock:
            if not force and now - self._last_heartbeat_at < 5:
                return True
            if not force and self._heartbeat_future and not self._heartbeat_future.done():
                return True

            executor = self._ensure_heartbeat_executor()
            payload = self._heartbeat_payload(context, now)
            heartbeat_path = self._heartbeat_path()
            self._last_heartbeat_at = now
            self._heartbeat_future = executor.submit(
                self._write_heartbeat_file,
                heartbeat_path,
                payload,
            )
            self._heartbeat_future.add_done_callback(self._log_future_exception)
        return True

    def _observe_window(self, context: Context) -> ObservationSnapshot | None:
        capture_started_at = time.perf_counter()
        screenshot, window_rect = self.window_handler.screenshot_window(context.window_title)
        record_stage_timing(
            context,
            "window_capture",
            capture_started_at,
            detail=f"title={context.window_title}",
        )
        if screenshot is None or window_rect is None:
            return None

        started_at = time.perf_counter()
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(self.detector.detect, screenshot)
                detections = tuple(future.result(timeout=5.0))
        except TimeoutError:
            LOGGER.error("YOLO detection timed out after 5.0s")
            detections = ()
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            LOGGER.warning("Window observation detector skipped: %s", exc)
            detections = ()
        duration_ms = (time.perf_counter() - started_at) * 1000.0
        record_stage_timing(
            context,
            "yolo_detect",
            started_at,
            detail=f"detections={len(detections)}",
        )
        LOGGER.debug("YOLO observation duration_ms=%.2f detections=%s", duration_ms, len(detections))
        return context.set_current_observation(screenshot, window_rect, detections=detections)

    def _detect_captcha(self, context: Context, observation: ObservationSnapshot | None = None) -> bool:
        if self.stop_event.is_set() or self.pause_event.is_set():
            return False

        cached_observation = context.get_current_observation() if hasattr(context, "get_current_observation") else getattr(context, "current_observation", None)
        observation = observation or cached_observation or self._observe_window(context)
        if observation is None:
            return False

        screenshot = observation.screenshot
        detections = observation.detections
        labels = {str(getattr(detection, "label", "")).lower().replace(" ", "_") for detection in detections}
        if not labels.intersection(CAPTCHA_LABELS):
            return False

        LOGGER.error("Captcha detected: pausing automation for manual review.")
        session_logger = self._session_logger(context)
        if session_logger and hasattr(session_logger, "record_captcha"):
            session_logger.record_captcha()
        context.emit_state("Captcha detected - paused")
        screenshot_path = save_diagnostic_screenshot(screenshot, label="captcha_detected")
        if session_logger and screenshot_path and hasattr(session_logger, "update_metadata"):
            session_logger.update_metadata(diagnostics_path=str(screenshot_path.parent))
        if screenshot_path and hasattr(context, "export_state_history"):
            context.export_state_history(screenshot_path.with_suffix(".log"))
        self.pause_event.set()
        self.signal_emitter.pause_toggled.emit(True)
        return True

    @staticmethod
    def _close_state_machine(machine: Any) -> None:
        close = getattr(machine, "close", None)
        if not callable(close):
            return
        try:
            close()
        except Exception as exc:
            LOGGER.warning("State machine cleanup failed: %s", exc)

    def run(self, state_machines: Sequence[Any], context: Context | None = None) -> None:
        """Run one or more workflow state machines until stop or failure.

        Args:
            state_machines: The prepared workflow machines to execute.
            context: Optional pre-built runtime context for the session.
        """

        context = self._prepare_context(context)
        self._active_context = context
        base_log_context = self._session_log_context_fields(context)

        with scoped_log_context(**base_log_context):
            self.stop_event.clear()
            self.all_threads_joined = False
            self.write_heartbeat(context, force=True)

            def run_single_machine(machine: Any, machine_index: int) -> None:
                machine_id = f"machine_{machine_index + 1}"
                with scoped_log_context(**base_log_context, machine_id=machine_id):
                    bind_runtime_machine = getattr(context, "bind_runtime_machine", None)
                    clear_runtime_machine = getattr(context, "clear_runtime_machine", None)
                    clear_active_step_scope = getattr(context, "clear_active_step_scope", None)
                    if callable(bind_runtime_machine):
                        bind_runtime_machine(machine_id)
                    try:
                        while not self.stop_event.is_set():
                            observation = None
                            if getattr(machine, "halted", False):
                                LOGGER.error("Workflow state machine halted; stopping workflow thread.")
                                break
                            try:
                                if self.pause_event.is_set():
                                    self.write_heartbeat(context)
                                    self.stop_event.wait(self.delay)
                                    continue
                                self.write_heartbeat(context)
                                if not self._ensure_foreground(context):
                                    continue
                                observation = self._observe_window(context)
                                if observation is None:
                                    continue
                                if self._detect_captcha(context, observation=observation):
                                    continue
                                if not self._ensure_foreground(context):
                                    continue
                                step_result = machine.execute(context)
                                if getattr(machine, "halted", False):
                                    LOGGER.error("Workflow state machine halted after execute; stopping workflow thread.")
                                    break
                                if step_result:
                                    self.stop_event.wait(self.delay)
                            finally:
                                if hasattr(context, "clear_current_observation_if"):
                                    context.clear_current_observation_if(observation)
                                elif getattr(context, "current_observation", None) is observation:
                                    context.clear_current_observation()
                    finally:
                        if callable(clear_active_step_scope):
                            clear_active_step_scope()
                        if callable(clear_runtime_machine):
                            clear_runtime_machine()

            try:
                with ThreadPoolExecutor(
                    max_workers=max(1, len(state_machines)),
                    thread_name_prefix="OSROKBOT-Workflow",
                ) as executor:
                    futures: list[Future[None]] = [
                        executor.submit(run_single_machine, machine, index) for index, machine in enumerate(state_machines)
                    ]
                    while futures and not self.stop_event.is_set():
                        done, _pending = wait(futures, timeout=0.5, return_when=FIRST_EXCEPTION)
                        for future in done:
                            future.result()
                        futures = [future for future in futures if not future.done()]
            finally:
                for machine in state_machines:
                    self._close_state_machine(machine)
                self.stop_event.set()
                self.all_threads_joined = True
                self.is_running = False

    def _runner_done(self, future: Future[None]) -> None:
        failed = False
        context = self._active_context
        with scoped_log_context(**self._session_log_context_fields(context)):
            try:
                future.result()
            except Exception as exc:
                failed = True
                LOGGER.error("OSROKBOT runner stopped after an unhandled error: %s", exc)
                self._record_session_error(context, f"Unhandled runner error: {exc}", stage="runner")
                self._mark_terminal(context, "failed", "runner_unhandled_exception", detail=str(exc))
            if future is not self._runner_future:
                return
            if not failed and context is not None:
                session_logger = self._session_logger(context)
                if session_logger and hasattr(session_logger, "summary"):
                    status = str(session_logger.summary().get("status", "partial") or "partial")
                    if status == "partial":
                        self._mark_terminal(
                            context,
                            "interrupted",
                            "runner_stopped_without_terminal_reason",
                            detail="Runner stopped without a recorded terminal reason.",
                        )
            if failed:
                self.stop_event.set()
            if self.signal_emitter and hasattr(self.signal_emitter, "run_finished"):
                session_logger = self._session_logger(context)
                summary = session_logger.summary() if session_logger and hasattr(session_logger, "summary") else {}
                self.signal_emitter.run_finished.emit(
                    {
                        "status": str(summary.get("status", "failed" if failed else "interrupted")),
                        "end_reason": str(
                            summary.get(
                                "end_reason",
                                "runner_unhandled_exception" if failed else "runner_stopped_without_terminal_reason",
                            )
                        ),
                        "detail": str(summary.get("final_state", "") or ""),
                    }
                )
            self._runner_future = None
            self.is_running = False
            self._shutdown_runner_executor()
            self.all_threads_joined = True
            self._active_context = None

    def start(self, steps: Sequence[Any], context: Context | None = None) -> bool:
        """Start the runner asynchronously after startup safety checks pass.

        Args:
            steps: Workflow machines to execute on background worker threads.
            context: Optional runtime context to reuse for the session.

        Returns:
            bool: `True` when the run started, otherwise `False`.
        """

        prepared_context = self._prepare_context(context) if context is not None else None
        if self.is_running or not self.all_threads_joined:
            return False
        if not self._hardware_input_ready(prepared_context):
            self.is_running = False
            return False
        if not self.emergency_stop.start_once():
            LOGGER.error("Emergency stop is unavailable; refusing to start live automation.")
            self._record_session_error(prepared_context, "Emergency stop is unavailable.", stage="startup")
            self._mark_terminal(prepared_context, "failed", "emergency_stop_unavailable", detail="Emergency stop is unavailable.")
            self._emit_state(prepared_context, "Emergency stop unavailable")
            self.is_running = False
            return False

        self.stop_event.clear()
        self.pause_event.clear()
        self._ensure_heartbeat_executor()
        self.is_running = True
        self._active_context = prepared_context
        self._runner_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="OSROKBOT-Runner")
        self._runner_future = self._runner_executor.submit(self.run, steps, prepared_context)
        self._runner_future.add_done_callback(self._runner_done)
        return True

    def stop(self) -> None:
        """Request runner shutdown and stop all background executors."""

        self.stop_event.set()
        self.is_running = False
        self._shutdown_runner_executor()
        self._shutdown_heartbeat_executor()

    def toggle_pause(self) -> None:
        """Toggle the runtime pause flag and emit the updated pause state."""

        if self.pause_event.is_set():
            self.pause_event.clear()
        else:
            self.pause_event.set()
        self.signal_emitter.pause_toggled.emit(self.pause_event.is_set())

    def is_paused(self) -> bool:
        """Return whether the runtime is currently paused."""

        return self.pause_event.is_set()
