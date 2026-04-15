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
    `fallback_state` when provided, otherwise the normal failure target.
    """

    def __init__(self):
        self.states = {}
        self.current_state = None

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
            next_state = fallback_state or next_state_on_failure
            self.current_state = next_state() if callable(next_state) else next_state
            return False

        result = state.perform(context)

        next_state = next_state_on_success if result else next_state_on_failure
        self.current_state = next_state() if callable(next_state) else next_state
        return result

    @staticmethod
    def _precondition_passes(precondition, context=None):
        if hasattr(precondition, "perform"):
            return bool(precondition.perform(context))
        if callable(precondition):
            return bool(precondition(context))
        return bool(precondition)
