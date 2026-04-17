import json

from config_manager import ConfigManager


def test_config_manager_persists_secrets_to_env_not_json(tmp_path):
    config_path = tmp_path / "config.json"
    env_path = tmp_path / ".env"
    manager = ConfigManager(config_path=config_path, env_path=env_path)

    manager.set_many({"OPENAI_KEY": "sk-test-secret-value-123456", "PLANNER_GOAL": "Gather safely"})

    public_config = json.loads(config_path.read_text(encoding="utf-8"))
    env_text = env_path.read_text(encoding="utf-8")

    assert public_config == {"PLANNER_GOAL": "Gather safely"}
    assert "OPENAI_KEY=sk-test-secret-value-123456" in env_text
    assert manager.get("OPENAI_KEY") == "sk-test-secret-value-123456"


def test_config_manager_migrates_legacy_secret_from_json(tmp_path):
    config_path = tmp_path / "config.json"
    env_path = tmp_path / ".env"
    config_path.write_text(
        json.dumps({"OPENAI_KEY": "sk-legacy-secret-value-123456", "PLANNER_GOAL": "Old"}) + "\n",
        encoding="utf-8",
    )

    manager = ConfigManager(config_path=config_path, env_path=env_path)
    manager.save()

    public_config = json.loads(config_path.read_text(encoding="utf-8"))
    env_text = env_path.read_text(encoding="utf-8")

    assert public_config == {"PLANNER_GOAL": "Old"}
    assert "OPENAI_KEY=sk-legacy-secret-value-123456" in env_text
