from termcolor import colored


class StateMachine:
    """Small deterministic state machine for automation workflows.

    States are registered with `add_state(name, action, success, failure)`.
    `action` must be an `Action`-compatible object exposing `perform(context)`.
    `execute(context)` runs the current state once, then moves to the success
    or failure target according to the action's boolean result. A missing
    failure target intentionally retries the same state.
    Optional preconditions can verify screen state before an action runs. A
    precondition can be another Action-like object, a callable that accepts the
    current Context, or a simple boolean. Failed preconditions transition to
    `fallback_state` when provided, otherwise the normal failure target. After
    repeated precondition failures for the same state, global recovery clears
    modal UI, toggles city/map, then restarts the client when an unknown state
    persists and an explicit restart hook or client path is configured.
    """

    def __init__(self):
        self.states = {}
        self.current_state = None
        self.precondition_failures = {}
        self.precondition_recovery_threshold = 3
        self.action_failures = {}
        self.ai_fallback_threshold = 3
        self.recovery_executor = None

    def add_state(
        self,
        name,
        state,
        next_state_on_success=None,
        next_state_on_failure=None,
        precondition=None,
        fallback_state=None,
    ):
        if next_state_on_failure is None:
            next_state_on_failure = name
        self.states[name] = (
            state,
            next_state_on_success,
            next_state_on_failure,
            precondition,
            fallback_state,
        )

    def set_initial_state(self, name):
        if name not in self.states:
            raise ValueError(f"Unknown initial state: {name}")
        self.current_state = name

    def execute(self, context=None):
        if self.current_state is None:
            raise RuntimeError("Initial state is not set")
        if self.current_state not in self.states:
            raise RuntimeError(f"Unknown state: {self.current_state}")

        (
            state,
            next_state_on_success,
            next_state_on_failure,
            precondition,
            fallback_state,
        ) = self.states[self.current_state]

        if precondition and not self._precondition_passes(precondition, context):
            precondition_state = self.current_state
            failure_count = self.precondition_failures.get(precondition_state, 0) + 1
            self.precondition_failures[precondition_state] = failure_count

            if failure_count >= self.precondition_recovery_threshold:
                if context and hasattr(context, "record_state"):
                    context.record_state(
                        precondition_state,
                        "precondition",
                        False,
                        next_state=precondition_state,
                        event="precondition",
                    )
                    context.save_failure_diagnostic(f"precondition_{precondition_state}")
                self.precondition_failures[precondition_state] = 0
                self.global_recovery(context)
                return False

            next_state = fallback_state or next_state_on_failure
            resolved_next_state = next_state() if callable(next_state) else next_state
            if context and hasattr(context, "record_state"):
                context.record_state(
                    precondition_state,
                    "precondition",
                    False,
                    next_state=resolved_next_state,
                    event="precondition",
                )
            self.current_state = resolved_next_state
            return False

        self.precondition_failures[self.current_state] = 0
        state_name = self.current_state
        result = state.perform(context)

        next_state = next_state_on_success if result else next_state_on_failure
        resolved_next_state = next_state() if callable(next_state) else next_state
        if context and hasattr(context, "record_state"):
            pending_recovery = getattr(context, "extracted", {}).get("pending_ai_recovery")
            context.record_state(
                state_name,
                getattr(state, "status_text", state.__class__.__name__),
                result,
                next_state=resolved_next_state,
            )
            if pending_recovery:
                self._verify_pending_recovery(context, state_name, resolved_next_state, result)

            if result:
                self.action_failures[state_name] = 0
            else:
                screenshot_path = context.save_failure_diagnostic(state_name)
                failure_count = self.action_failures.get(state_name, 0) + 1
                self.action_failures[state_name] = failure_count
                if not pending_recovery and self._should_run_guarded_recovery(state, failure_count):
                    self.action_failures[state_name] = 0
                    if not self.global_recovery(context):
                        self._run_guarded_recovery(context, state_name, state, screenshot_path)

        self.current_state = resolved_next_state
        return result

    def _should_run_guarded_recovery(self, state, failure_count):
        if failure_count < self.ai_fallback_threshold:
            return False
        image = getattr(state, "image", "")
        if image == "Media/captchachest.png":
            return False
        return bool(image)

    def _get_recovery_executor(self):
        if self.recovery_executor:
            return self.recovery_executor
        try:
            from ai_recovery_executor import AIRecoveryExecutor
        except Exception as exc:
            print(colored(f"AI recovery unavailable: {exc}", "yellow"))
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
        print("Recovery tier 1: close menu/blockers.")
        self._emit_recovery_state(context, "Recovery tier 1\nclose menu")

        for _ in range(3):
            monitor.clear_blockers()
            state = monitor.current_state()
            if self._is_known_state(state, GameState):
                print(f"Recovery tier 1 found state: {state.value}")
                return True
            if not controller.key_press("escape", hold_seconds=0.1, context=context):
                return False
            if not controller.wait(0.4, context=context):
                return False
        return False

    def _recovery_toggle_view(self, monitor, controller, context, GameState):
        print("Recovery tier 2: toggle city/map view.")
        self._emit_recovery_state(context, "Recovery tier 2\ntoggle view")

        for _ in range(4):
            monitor.clear_blockers()
            state = monitor.current_state()
            if self._is_known_state(state, GameState):
                print(f"Recovery tier 2 found state: {state.value}")
                return True
            if not controller.key_press("space", hold_seconds=0.1, context=context):
                return False
            if not controller.wait(0.8, context=context):
                return False
        return False

    def _recovery_restart_game(self, monitor, controller, context, GameState):
        state = monitor.current_state()
        if state != GameState.UNKNOWN:
            print(f"StateMachine global recovery ended without confirming a known state: {state.value}")
            return False

        print("Recovery tier 3: restart client.")
        self._emit_recovery_state(context, "Recovery tier 3\nrestart client")
        if not monitor.restart_client():
            return False

        for _ in range(10):
            state = monitor.current_state()
            if self._is_known_state(state, GameState):
                print(f"Recovery tier 3 found state: {state.value}")
                return True
            if not controller.wait(1, context=context):
                return False

        print(f"StateMachine global recovery ended without confirming a known state: {state.value}")
        return False

    def global_recovery(self, context=None):
        """Tiered recovery: close menus, toggle view, then restart if unknown."""
        from input_controller import InputController
        from state_monitor import GameState, GameStateMonitor
        from window_handler import WindowHandler

        monitor = GameStateMonitor(context=context)
        controller = InputController(context=context)
        window_title = context.window_title if context and getattr(context, "window_title", None) else "Rise of Kingdoms"

        print("StateMachine global recovery started.")
        self._emit_recovery_state(context, "Global recovery\nclearing UI")

        WindowHandler().activate_window(window_title)
        monitor.save_diagnostic_screenshot(f"recovery_{self.current_state or 'unknown'}")

        return (
            self._recovery_close_menus(monitor, controller, context, GameState)
            or self._recovery_toggle_view(monitor, controller, context, GameState)
            or self._recovery_restart_game(monitor, controller, context, GameState)
        )
