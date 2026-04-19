import json

from config_manager import ConfigManager


class _FakeSecretProvider:
    name = "fake"

    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def set_many(self, values):
        for key, value in values.items():
            if value in {None, ""}:
                self.values.pop(key, None)
                continue
            self.values[key] = value


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


def test_config_manager_persists_secrets_to_injected_provider(tmp_path):
    config_path = tmp_path / "config.json"
    env_path = tmp_path / ".env"
    provider = _FakeSecretProvider()
    manager = ConfigManager(
        config_path=config_path,
        env_path=env_path,
        secret_provider=provider,
    )

    manager.set_many({"OPENAI_KEY": "sk-provider-secret-value-123456", "PLANNER_GOAL": "Gather safely"})

    public_config = json.loads(config_path.read_text(encoding="utf-8"))

    assert public_config == {"PLANNER_GOAL": "Gather safely"}
    assert provider.values["OPENAI_KEY"] == "sk-provider-secret-value-123456"
    assert not env_path.exists()


def test_config_manager_clears_secret_from_backend(tmp_path):
    config_path = tmp_path / "config.json"
    env_path = tmp_path / ".env"
    provider = _FakeSecretProvider()
    manager = ConfigManager(
        config_path=config_path,
        env_path=env_path,
        secret_provider=provider,
    )

    manager.set_many({"OPENAI_KEY": "sk-provider-secret-value-123456"})
    manager.set_many({"OPENAI_KEY": ""})

    assert "OPENAI_KEY" not in provider.values


def test_config_manager_public_only_save_does_not_create_empty_env_file(tmp_path):
    config_path = tmp_path / "config.json"
    env_path = tmp_path / ".env"
    manager = ConfigManager(config_path=config_path, env_path=env_path)

    manager.set_many({"PLANNER_GOAL": "Gather safely"})

    assert env_path.exists() is False
