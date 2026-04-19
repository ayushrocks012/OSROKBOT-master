import json
import logging
from pathlib import Path

from logging_config import JsonFormatter, RedactingFilter, scoped_log_context
from security_utils import atomic_write_text, format_env_value, redact_secret, update_env_file


def test_redact_secret_masks_openai_key_patterns():
    assert redact_secret("OPENAI_KEY=sk-test-secret-value-123456") == "OPENAI_KEY=<redacted>"
    assert redact_secret("token sk-test-secret-value-123456") == "token <redacted>"


def test_redact_secret_masks_bearer_and_vault_tokens():
    assert redact_secret("Authorization: Bearer super-secret-token-value") == "Authorization: Bearer <redacted>"
    assert redact_secret("X-Vault-Token=vault-secret-token") == "X-Vault-Token=<redacted>"
    assert redact_secret('{"authorization":"super-secret-token-value"}') == '{"authorization":"<redacted>"}'


def test_logging_filter_redacts_rendered_message():
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="key=%s",
        args=("sk-test-secret-value-123456",),
        exc_info=None,
    )

    assert RedactingFilter().filter(record) is True
    assert record.getMessage() == "key=<redacted>"


def test_json_formatter_includes_bound_context_and_redacts_message():
    record = logging.LogRecord(
        name="test",
        level=logging.WARNING,
        pathname=__file__,
        lineno=12,
        msg="planner key=%s",
        args=("sk-test-secret-value-123456",),
        exc_info=None,
    )

    with scoped_log_context(run_id="run_123", session_id="run_123", run_kind="runtime_session"):
        assert RedactingFilter().filter(record) is True
        payload = json.loads(JsonFormatter().format(record))

    assert payload["message"] == "planner key=<redacted>"
    assert payload["run_id"] == "run_123"
    assert payload["session_id"] == "run_123"
    assert payload["run_kind"] == "runtime_session"


def test_format_env_value_quotes_values_with_spaces_and_comments():
    assert format_env_value("plain") == "plain"
    assert format_env_value("needs spaces") == '"needs spaces"'
    assert format_env_value("value#comment") == '"value#comment"'


def test_update_env_file_preserves_comments_and_replaces_requested_keys(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("# comment\nOPENAI_KEY=old\nUNCHANGED=1\n", encoding="utf-8")

    update_env_file(env_path, {"OPENAI_KEY": "new value", "EMAIL_PASSWORD": "secret"})

    written = env_path.read_text(encoding="utf-8")
    assert "# comment" in written
    assert 'OPENAI_KEY="new value"' in written
    assert "UNCHANGED=1" in written
    assert "EMAIL_PASSWORD=secret" in written


def test_update_env_file_removes_keys_when_value_is_none(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("OPENAI_KEY=old\nUNCHANGED=1\n", encoding="utf-8")

    update_env_file(env_path, {"OPENAI_KEY": None})

    written = env_path.read_text(encoding="utf-8")
    assert "OPENAI_KEY" not in written
    assert "UNCHANGED=1" in written


def test_atomic_write_text_replaces_existing_file_atomically(tmp_path):
    target = tmp_path / "config" / "file.txt"
    atomic_write_text(target, "updated\n")

    assert target.read_text(encoding="utf-8") == "updated\n"
    assert not Path(str(target) + ".tmp").exists()
