"""Deterministic workflow execution for guarded OSROKBOT runs.

The state machine owns supported action registration, precondition checking,
transition recording, and conservative recovery escalation. It remains
synchronous and delegates side effects to Action-like objects, the runtime
Context, and focused recovery helpers.

Safety:
    Invalid transitions halt the machine instead of guessing a next state.
    Recovery escalates from UI cleanup to client restart only when known-state
    checks fail repeatedly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from logging_config import get_logger
from runtime_contracts import ActionLike, Precondition, TransitionTarget

LOGGER = get_logger(__name__)


@dataclass(frozen=True)
class StateDefinition:
    """Registered state and transition metadata."""

    action: ActionLike
    next_state_on_success: TransitionTarget
    next_state_on_failure: TransitionTarget
    precondition: Precondition = None
    fallback_state: TransitionTarget = None


class StateMachine:
    """Run one deterministic workflow step and own recovery escalation.

    States are registered with `add_state(...)` and execute through Action-like
    objects exposing `perform(context)`. `execute(context)` runs exactly one
    state step, records diagnostics into `Context`, and routes failures through
    precondition handling, guarded recovery, or global recovery as needed.

    Collaborators:
        Action-like state objects perform the actual work.
        `Context` records state history, emits UI status, and stores
        diagnostics.
        `AIRecoveryExecutor` and `GameStateMonitor` are optional recovery
        helpers, loaded only when needed.

    Invariants:
        - Invalid transitions halt the machine instead of guessing.
        - Missing failure targets retry the current state by design.
        - Recovery escalates from menu cleanup to view toggle to restart.
    """

    def __init__(self) -> None:
        self.states: dict[str, StateDefinition] = {}
        self.current_state: str | None = None
        self.halted = False
        self.precondition_failures: dict[str, int] = {}
        self.precondition_recovery_threshold = 3
        self.action_failures: dict[str, int] = {}
        self.ai_fallback_threshold = 3
        self.recovery_executor: Any | None = None

    def add_state(
        self,
        name: str,
        state: ActionLike,
        next_state_on_success: TransitionTarget = None,
        next_state_on_failure: TransitionTarget = None,
        precondition: Precondition = None,
        fallback_state: TransitionTarget = None,
    ) -> None:
        """Register one state and its transition policy.

        Args:
            name: State identifier used for execution and diagnostics.
            state: Action-like object exposing `perform(context)`.
            next_state_on_success: Next state or resolver used when the action
                returns `True`.
            next_state_on_failure: Next state or resolver used when the action
                returns `False`. Defaults to the current state for retries.
            precondition: Optional Action-like object, callable, or boolean
                checked before the state action runs.
            fallback_state: Optional transition used when the precondition
                fails.
        """
        if next_state_on_failure is None:
            next_state_on_failure = name
        self.states[name] = StateDefinition(
            action=state,
            next_state_on_success=next_state_on_success,
            next_state_on_failure=next_state_on_failure,
            precondition=precondition,
            fallback_state=fallback_state,
        )

    def set_initial_state(self, name: str) -> None:
        if name not in self.states:
            raise ValueError(f"Unknown initial state: {name}")
        self.current_state = name
        self.halted = False

    def _record_transition(
        self,
        context: Any | None,
        state_name: str,
        status_text: str,
        result: bool,
        next_state: str | None = None,
        event: str = "action",
    ) -> None:
        if context and hasattr(context, "record_state"):
            context.record_state(state_name, status_text, result, next_state=next_state, event=event)

    def _halt_invalid_transition(
        self,
        context: Any | None,
        state_name: str,
        source: str,
        status_text: str,
        result: bool,
        next_state: str | None = None,
        event: str = "action",
    ) -> None:
        LOGGER.error("State resolution failed for %s during %s. Halting workflow.", state_name, source)
        self._record_transition(context, state_name, status_text, result, next_state=next_state, event=event)
        self.halted = True
        return

    def _resolve_next_state(
        self,
        next_state: TransitionTarget,
        context: Any | None,
        state_name: str,
        source: str,
        status_text: str,
        result: bool,
        event: str = "action",
    ) -> str | None:
        try:
            resolved_next_state = next_state() if callable(next_state) else next_state
        except Exception as exc:
            LOGGER.error("State resolution callable failed for %s during %s: %s", state_name, source, exc)
            return self._halt_invalid_transition(context, state_name, source, status_text, result, event=event)
        if not resolved_next_state:
            return self._halt_invalid_transition(
                context,
                state_name,
                source,
                status_text,
                result,
                next_state=resolved_next_state,
                event=event,
            )
        return resolved_next_state

    def _current_definition(self) -> StateDefinition:
        if self.halted:
            raise RuntimeError("State machine is halted")
        if self.current_state is None:
            raise RuntimeError("Initial state is not set")
        if self.current_state not in self.states:
            raise RuntimeError(f"Unknown state: {self.current_state}")
        return self.states[self.current_state]

    def _handle_precondition_failure(
        self,
        context: Any | None,
        state_name: str,
        definition: StateDefinition,
    ) -> bool:
        failure_count = self.precondition_failures.get(state_name, 0) + 1
        self.precondition_failures[state_name] = failure_count

        if failure_count >= self.precondition_recovery_threshold:
            if context and hasattr(context, "record_state"):
                context.record_state(
                    state_name,
                    "precondition",
                    False,
                    next_state=state_name,
                    event="precondition",
                )
                context.save_failure_diagnostic(f"precondition_{state_name}")
            self.precondition_failures[state_name] = 0
            self.global_recovery(context)
            return False

        next_state = definition.fallback_state or definition.next_state_on_failure
        resolved_next_state = self._resolve_next_state(
            next_state,
            context,
            state_name,
            "precondition",
            "precondition",
            False,
            event="precondition",
        )
        if resolved_next_state is None:
            return False
        if context and hasattr(context, "record_state"):
            context.record_state(
                state_name,
                "precondition",
                False,
                next_state=resolved_next_state,
                event="precondition",
            )
        self.current_state = resolved_next_state
        return False

    def _record_action_result(
        self,
        context: Any,
        state_name: str,
        definition: StateDefinition,
        status_text: str,
        result: bool,
        next_state: str,
    ) -> None:
        pending_recovery = getattr(context, "extracted", {}).get("pending_ai_recovery")
        context.record_state(state_name, status_text, result, next_state=next_state)
        if pending_recovery:
            self._verify_pending_recovery(context, state_name, next_state, result)

        if result:
            self.action_failures[state_name] = 0
            return

        screenshot_path = context.save_failure_diagnostic(state_name)
        failure_count = self.action_failures.get(state_name, 0) + 1
        self.action_failures[state_name] = failure_count
        if not pending_recovery and self._should_run_guarded_recovery(definition.action, failure_count):
            self.action_failures[state_name] = 0
            if not self.global_recovery(context):
                self._run_guarded_recovery(context, state_name, definition.action, screenshot_path)

    def _perform_action(self, context: Any | None, state_name: str, definition: StateDefinition) -> bool:
        result = definition.action.perform(context)
        next_state = definition.next_state_on_success if result else definition.next_state_on_failure
        status_text = getattr(definition.action, "status_text", definition.action.__class__.__name__)
        resolved_next_state = self._resolve_next_state(
            next_state,
            context,
            state_name,
            "action",
            status_text,
            result,
        )
        if resolved_next_state is None:
            return False
        if context and hasattr(context, "record_state"):
            self._record_action_result(context, state_name, definition, status_text, result, resolved_next_state)

        self.current_state = resolved_next_state
        return result

    def execute(self, context: Any | None = None) -> bool:
        """Run exactly one state transition for the current workflow step.

        Args:
            context: Optional runtime context used for state history,
                diagnostics, UI signals, and recovery helpers.

        Returns:
            bool: `True` when the state action succeeds. `False` when the
            action fails, precondition handling reroutes execution, or the
            machine is halted.

        Raises:
            RuntimeError: If the initial state is unset or current-state
            metadata is invalid.
        """
        if self.halted:
            LOGGER.error("State machine is halted; refusing to execute.")
            return False

        definition = self._current_definition()
        state_name = self.current_state
        if state_name is None:
            raise RuntimeError("Initial state is not set")

        if definition.precondition and not self._precondition_passes(definition.precondition, context):
            return self._handle_precondition_failure(context, state_name, definition)

        self.precondition_failures[state_name] = 0
        return self._perform_action(context, state_name, definition)

    def close(self) -> None:
        """Release resources owned by registered actions."""
        closed: set[int] = set()
        for definition in self.states.values():
            action = definition.action
            if id(action) in closed:
                continue
            close = getattr(action, "close", None)
            if not callable(close):
                continue
            try:
                close()
            except Exception as exc:
                LOGGER.warning("State action close failed for %s: %s", action.__class__.__name__, exc)
            closed.add(id(action))

    def _should_run_guarded_recovery(self, state: ActionLike, failure_count: int) -> bool:
        if failure_count < self.ai_fallback_threshold:
            return False
        image = getattr(state, "image", "")
        if "captcha" in str(image).lower():
            return False
        return bool(image)

    def _get_recovery_executor(self):
        if self.recovery_executor:
            return self.recovery_executor
        try:
            from ai_recovery_executor import AIRecoveryExecutor
        except Exception as exc:
            LOGGER.warning(f"AI recovery unavailable: {exc}")
            return None
        self.recovery_executor = AIRecoveryExecutor()
        return self.recovery_executor

    def _run_guarded_recovery(self, context, state_name, state, screenshot_path):
        executor = self._get_recovery_executor()
        if not executor:
            return False
        return executor.try_recover(context, state_name, state, screenshot_path)

    def _verify_pending_recovery(self, context, previous_state, next_state, result):
        executor = self._get_recovery_executor()
        if not executor:
            return
        executor.verify_pending(context, previous_state, next_state, result)

    @staticmethod
    def _precondition_passes(precondition, context=None):
        if hasattr(precondition, "perform"):
            return bool(precondition.perform(context))
        if callable(precondition):
            return bool(precondition(context))
        return bool(precondition)

    @staticmethod
    def _emit_recovery_state(context, state_text):
        if context:
            context.emit_state(state_text)

    @staticmethod
    def _is_known_state(state, GameState):
        return state in {GameState.CITY, GameState.MAP}

    def _recovery_close_menus(self, monitor, controller, context, GameState):
        LOGGER.info("Recovery tier 1: close menu/blockers.")
        self._emit_recovery_state(context, "Recovery tier 1\nclose menu")

        for _ in range(3):
            monitor.clear_blockers()
            state = monitor.current_state()
            if self._is_known_state(state, GameState):
                LOGGER.info("Recovery tier 1 found state: %s", state.value)
                return True
            if not controller.key_press("escape", hold_seconds=0.1, context=context):
                return False
            if not controller.wait(0.4, context=context):
                return False
        return False

    def _recovery_toggle_view(self, monitor, controller, context, GameState):
        LOGGER.info("Recovery tier 2: toggle city/map view.")
        self._emit_recovery_state(context, "Recovery tier 2\ntoggle view")

        for _ in range(4):
            monitor.clear_blockers()
            state = monitor.current_state()
            if self._is_known_state(state, GameState):
                LOGGER.info("Recovery tier 2 found state: %s", state.value)
                return True
            if not controller.key_press("space", hold_seconds=0.1, context=context):
                return False
            if not controller.wait(0.8, context=context):
                return False
        return False

    def _recovery_restart_game(self, monitor, controller, context, GameState):
        state = monitor.current_state()
        if state != GameState.UNKNOWN:
            LOGGER.warning("StateMachine global recovery ended without confirming a known state: %s", state.value)
            return False

        LOGGER.info("Recovery tier 3: restart client.")
        self._emit_recovery_state(context, "Recovery tier 3\nrestart client")
        if not monitor.restart_client():
            return False

        for _ in range(10):
            state = monitor.current_state()
            if self._is_known_state(state, GameState):
                LOGGER.info("Recovery tier 3 found state: %s", state.value)
                return True
            if not controller.wait(1, context=context):
                return False

        LOGGER.warning("StateMachine global recovery ended without confirming a known state: %s", state.value)
        return False

    def global_recovery(self, context=None):
        """Escalate recovery from UI cleanup to optional client restart.

        Args:
            context: Optional runtime context used for diagnostics, window
                title selection, wait interruption, and UI status updates.

        Returns:
            bool: `True` when recovery restores a known CITY or MAP state.
            `False` when all recovery tiers fail or are interrupted.

        Side Effects:
            Brings the game window to foreground, writes a diagnostic
            screenshot, sends guarded `escape` or `space` input through
            `InputController`, and may restart the client through
            `GameStateMonitor`.
        """
        from input_controller import InputController
        from state_monitor import GameState, GameStateMonitor
        from window_handler import WindowHandler

        monitor = GameStateMonitor(context=context)
        controller = InputController(context=context)
        window_title = context.window_title if context and getattr(context, "window_title", None) else "Rise of Kingdoms"

        LOGGER.info("StateMachine global recovery started.")
        self._emit_recovery_state(context, "Global recovery\nclearing UI")

        WindowHandler().ensure_foreground(window_title, wait_seconds=0.5)
        monitor.save_diagnostic_screenshot(f"recovery_{self.current_state or 'unknown'}")

        return (
            self._recovery_close_menus(monitor, controller, context, GameState)
            or self._recovery_toggle_view(monitor, controller, context, GameState)
            or self._recovery_restart_game(monitor, controller, context, GameState)
        )
