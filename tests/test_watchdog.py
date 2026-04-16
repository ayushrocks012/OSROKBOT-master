import time
from pathlib import Path

import watchdog


def test_fresh_heartbeat_takes_no_restart_action(tmp_path, monkeypatch):
    heartbeat_path = tmp_path / "heartbeat.json"
    heartbeat_path.write_text(
        '{"timestamp_epoch": %s, "bot_pid": 111, "game_pid": 222}' % time.time(),
        encoding="utf-8",
    )
    monkeypatch.setattr(watchdog, "game_is_missing", lambda payload: False)
    monkeypatch.setattr(
        watchdog,
        "handle_stale_heartbeat",
        lambda payload: (_ for _ in ()).throw(AssertionError("stale restart should not run")),
    )

    assert watchdog.check_once(heartbeat_path, timeout_seconds=30) is True


def test_stale_heartbeat_terminates_only_tracked_pids_and_restarts(tmp_path, monkeypatch):
    heartbeat_path = tmp_path / "heartbeat.json"
    game_path = tmp_path / "RiseOfKingdoms.exe"
    ui_path = tmp_path / "UI.py"
    game_path.write_text("", encoding="utf-8")
    ui_path.write_text("", encoding="utf-8")
    heartbeat_path.write_text(
        (
            '{"timestamp_epoch": 1, "bot_pid": 111, "game_pid": 222, '
            '"python_executable": "python.exe", "ui_entrypoint": "%s", "repo_root": "%s"}'
        )
        % (str(ui_path).replace("\\", "\\\\"), str(tmp_path).replace("\\", "\\\\")),
        encoding="utf-8",
    )

    run_calls = []
    popen_calls = []
    monkeypatch.setattr(watchdog, "restart_enabled_from_config", lambda: True)
    monkeypatch.setattr(watchdog, "game_restart_wait_from_config", lambda: 0)
    monkeypatch.setattr(watchdog.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(watchdog, "_configured_path", lambda key, default=None: game_path if key == "ROK_CLIENT_PATH" else default)
    monkeypatch.setattr(watchdog.subprocess, "run", lambda args, check=False, **kwargs: run_calls.append(args))
    monkeypatch.setattr(watchdog.subprocess, "Popen", lambda args, cwd=None: popen_calls.append((args, cwd)))

    assert watchdog.check_once(heartbeat_path, timeout_seconds=30, now=100) is True

    assert ["taskkill", "/PID", "111", "/T", "/F"] in run_calls
    assert ["taskkill", "/PID", "222", "/T", "/F"] in run_calls
    assert ([str(game_path)], str(tmp_path)) in popen_calls
    assert (["python.exe", str(ui_path)], str(tmp_path)) in popen_calls


def test_invalid_pids_are_skipped_without_taskkill(monkeypatch):
    run_calls = []
    monkeypatch.setattr(watchdog.subprocess, "run", lambda *args, **kwargs: run_calls.append(args))

    assert watchdog.terminate_tracked_pid(None, "bot") is False
    assert watchdog.terminate_tracked_pid("not-a-pid", "game") is False
    assert watchdog.terminate_tracked_pid(watchdog.os.getpid(), "self") is False
    assert run_calls == []


def test_missing_rok_client_path_skips_game_relaunch(monkeypatch):
    monkeypatch.setattr(watchdog, "_configured_path", lambda key, default=None: None)

    assert watchdog.relaunch_game() is False


def test_restart_ui_uses_heartbeat_python_and_entrypoint(tmp_path, monkeypatch):
    ui_path = tmp_path / "Classes" / "UI.py"
    ui_path.parent.mkdir()
    ui_path.write_text("", encoding="utf-8")
    popen_calls = []
    monkeypatch.setattr(watchdog.subprocess, "Popen", lambda args, cwd=None: popen_calls.append((args, cwd)))

    payload = {
        "python_executable": "C:/Python/python.exe",
        "ui_entrypoint": str(ui_path),
        "repo_root": str(tmp_path),
    }

    assert watchdog.restart_ui_from_heartbeat(payload) is True
    assert popen_calls == [(["C:/Python/python.exe", str(ui_path)], str(tmp_path))]
