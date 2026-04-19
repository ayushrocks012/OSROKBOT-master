import json

from secret_providers import ChainSecretProvider, DotenvSecretProvider, DpapiSecretProvider


class _MemorySecretProvider:
    def __init__(self, name="memory"):
        self.name = name
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def set_many(self, values):
        for key, value in values.items():
            if value in {None, ""}:
                self.values.pop(key, None)
                continue
            self.values[key] = value


def test_chain_secret_provider_reads_from_fallback_and_cleans_it_on_write():
    primary = _MemorySecretProvider(name="primary")
    fallback = _MemorySecretProvider(name="fallback")
    fallback.values["OPENAI_KEY"] = "sk-fallback-secret-value-123456"
    provider = ChainSecretProvider(
        primary=primary,
        fallbacks=(fallback,),
        cleanup_on_write=(fallback,),
    )

    assert provider.get("OPENAI_KEY") == "sk-fallback-secret-value-123456"

    provider.set_many({"OPENAI_KEY": "sk-primary-secret-value-123456"})

    assert primary.values["OPENAI_KEY"] == "sk-primary-secret-value-123456"
    assert "OPENAI_KEY" not in fallback.values


def test_dotenv_secret_provider_writes_and_removes_values(tmp_path):
    env_path = tmp_path / ".env"
    provider = DotenvSecretProvider(env_path)

    provider.set_many({"OPENAI_KEY": "sk-test-secret-value-123456"})
    assert provider.get("OPENAI_KEY") == "sk-test-secret-value-123456"

    provider.set_many({"OPENAI_KEY": None})
    assert provider.get("OPENAI_KEY") is None


def test_dpapi_secret_provider_encrypts_without_plaintext(tmp_path):
    store_path = tmp_path / "dpapi_secrets.json"
    provider = DpapiSecretProvider(
        store_path,
        protect_value=lambda data, entropy: data[::-1] + entropy[:1],
        unprotect_value=lambda data, entropy: data[:-1][::-1],
    )

    provider.set_many({"OPENAI_KEY": "sk-test-secret-value-123456"})

    payload = json.loads(store_path.read_text(encoding="utf-8"))
    rendered = store_path.read_text(encoding="utf-8")

    assert payload["provider"] == "dpapi"
    assert "sk-test-secret-value-123456" not in rendered
    assert provider.get("OPENAI_KEY") == "sk-test-secret-value-123456"
