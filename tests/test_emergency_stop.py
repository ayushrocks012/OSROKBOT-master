from emergency_stop import EmergencyStop


def test_kill_now_uses_injected_exit_function(monkeypatch):
    exit_codes = []
    monkeypatch.setattr(EmergencyStop, "_exit_func", staticmethod(lambda code: exit_codes.append(code)))

    EmergencyStop._kill_now()

    assert exit_codes == [0]
