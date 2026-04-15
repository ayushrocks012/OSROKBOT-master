class StateMachine:
    def __init__(self):
        self.states = {}
        self.current_state = None

    def add_state(self, name, state, next_state_on_success=None, next_state_on_failure=None):
        if next_state_on_failure is None:
            next_state_on_failure = name
        self.states[name] = (state, next_state_on_success, next_state_on_failure)

    def set_initial_state(self, name):
        if name not in self.states:
            raise ValueError(f"Unknown initial state: {name}")
        self.current_state = name

    def execute(self, context=None):
        if self.current_state is None:
            raise RuntimeError("Initial state is not set")
        if self.current_state not in self.states:
            raise RuntimeError(f"Unknown state: {self.current_state}")

        state, next_state_on_success, next_state_on_failure = self.states[self.current_state]
        result = state.perform(context)

        next_state = next_state_on_success if result else next_state_on_failure
        self.current_state = next_state() if callable(next_state) else next_state
        return result
