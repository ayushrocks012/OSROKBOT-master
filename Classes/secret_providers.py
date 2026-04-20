"""Secret-provider abstractions for local and enterprise-grade configuration.

This module owns the boundary between runtime configuration consumers and the
storage backends that hold sensitive values. The shipped providers keep the
current `.env` path available while adding a Windows DPAPI-backed encrypted
store for local at-rest protection.

Side Effects:
    Providers may read and write `.env` files or a local DPAPI-backed JSON
    store.
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
from collections.abc import Mapping, Sequence
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from logging_config import get_logger
from security_utils import atomic_write_text, parse_env_file, update_env_file

LOGGER = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DPAPI_STORE_PATH = PROJECT_ROOT / "data" / "secrets" / "dpapi_secrets.json"
_DPAPI_FLAG_UI_FORBIDDEN = 0x01


class SecretProvider(Protocol):
    """Read and write sensitive runtime configuration values."""

    name: str

    def get(self, key: str) -> str | None:
        """Return the secret value for `key`, if available."""

    def set_many(self, values: Mapping[str, str | None]) -> None:
        """Persist updates for one or more secret keys."""


class EnvironmentSecretProvider:
    """Read secrets from the current process environment."""

    name = "environment"

    def get(self, key: str) -> str | None:
        """Return one secret from the current process environment."""

        return os.getenv(key)

    def set_many(self, values: Mapping[str, str | None]) -> None:
        """Apply secret updates to the current process environment."""

        for key, value in values.items():
            if value in {None, ""}:
                os.environ.pop(key, None)
                continue
            os.environ[key] = str(value)


class DotenvSecretProvider:
    """Persist secrets to a local dotenv file."""

    name = "dotenv"

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def get(self, key: str) -> str | None:
        """Return one secret from the configured dotenv file."""

        value = parse_env_file(self.path).get(key)
        return value if value not in {None, ""} else None

    def set_many(self, values: Mapping[str, str | None]) -> None:
        """Persist one or more secret updates to the dotenv file."""

        update_env_file(self.path, dict(values))


@dataclass
class ChainSecretProvider:
    """Read through fallbacks while writing only to the primary provider."""

    primary: SecretProvider
    fallbacks: Sequence[SecretProvider] = ()
    cleanup_on_write: Sequence[SecretProvider] = ()
    name: str = "chain"

    @property
    def active_name(self) -> str:
        """Return the writable backend name for operator-facing diagnostics."""

        return getattr(self.primary, "name", "unknown")

    def get(self, key: str) -> str | None:
        """Resolve one secret by reading the primary provider then fallbacks."""

        for provider in (self.primary, *self.fallbacks):
            value = provider.get(key)
            if value not in {None, ""}:
                return value
        return None

    def set_many(self, values: Mapping[str, str | None]) -> None:
        """Write secrets to the primary provider and clear configured fallbacks."""

        updates = dict(values)
        self.primary.set_many(updates)
        cleanup = {key: None for key in updates}
        for provider in self.cleanup_on_write:
            provider.set_many(cleanup)


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _blob_from_bytes(data: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(data, len(data))
    blob = _DataBlob(
        cbData=len(data),
        pbData=ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
    )
    return blob, buffer


def _dpapi_protect(data: bytes, entropy: bytes) -> bytes:
    if os.name != "nt":
        raise RuntimeError("Windows DPAPI is only available on Windows.")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_blob, in_buffer = _blob_from_bytes(data)
    entropy_blob, entropy_buffer = _blob_from_bytes(entropy)
    del in_buffer, entropy_buffer
    out_blob = _DataBlob()
    if not crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        "OSROKBOT Secret",
        ctypes.byref(entropy_blob),
        None,
        None,
        _DPAPI_FLAG_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    ):
        raise OSError(ctypes.get_last_error(), "CryptProtectData failed")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _dpapi_unprotect(data: bytes, entropy: bytes) -> bytes:
    if os.name != "nt":
        raise RuntimeError("Windows DPAPI is only available on Windows.")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_blob, in_buffer = _blob_from_bytes(data)
    entropy_blob, entropy_buffer = _blob_from_bytes(entropy)
    del in_buffer, entropy_buffer
    out_blob = _DataBlob()
    description = wintypes.LPWSTR()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        ctypes.byref(description),
        ctypes.byref(entropy_blob),
        None,
        None,
        _DPAPI_FLAG_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    ):
        raise OSError(ctypes.get_last_error(), "CryptUnprotectData failed")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)
        if description:
            kernel32.LocalFree(description)


class DpapiSecretProvider:
    """Persist secrets in a Windows DPAPI-backed encrypted local store."""

    name = "dpapi"

    def __init__(
        self,
        path: Path = DEFAULT_DPAPI_STORE_PATH,
        *,
        protect_value=None,
        unprotect_value=None,
    ) -> None:
        self.path = Path(path)
        self._protect_value = protect_value or _dpapi_protect
        self._unprotect_value = unprotect_value or _dpapi_unprotect
        if (protect_value is None or unprotect_value is None) and os.name != "nt":
            raise RuntimeError("Windows DPAPI is unavailable on this platform.")

    def _load_payload(self) -> dict[str, object]:
        if not self.path.is_file():
            return {"version": 1, "provider": self.name, "secrets": {}}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            LOGGER.warning("Unable to read DPAPI secret store %s: %s", self.path, exc)
            return {"version": 1, "provider": self.name, "secrets": {}}
        if not isinstance(raw, dict):
            return {"version": 1, "provider": self.name, "secrets": {}}
        secrets = raw.get("secrets")
        if not isinstance(secrets, dict):
            raw["secrets"] = {}
        return raw

    def _entropy_for(self, key: str) -> bytes:
        return f"OSROKBOT::{key}".encode()

    def get(self, key: str) -> str | None:
        """Return one decrypted secret from the DPAPI-backed local store."""

        secrets = self._load_payload().get("secrets", {})
        if not isinstance(secrets, dict):
            return None
        encoded = secrets.get(key)
        if not isinstance(encoded, str) or not encoded:
            return None
        try:
            plaintext = self._unprotect_value(base64.b64decode(encoded), self._entropy_for(key))
            return plaintext.decode("utf-8")
        except Exception as exc:
            LOGGER.warning("Unable to decrypt DPAPI secret %s: %s", key, exc)
            return None

    def set_many(self, values: Mapping[str, str | None]) -> None:
        """Persist one or more encrypted secrets to the DPAPI local store."""

        payload = self._load_payload()
        secrets = payload.get("secrets", {})
        if not isinstance(secrets, dict):
            secrets = {}

        updated = dict(secrets)
        for key, value in values.items():
            if value in {None, ""}:
                updated.pop(key, None)
                continue
            ciphertext = self._protect_value(str(value).encode("utf-8"), self._entropy_for(key))
            updated[key] = base64.b64encode(ciphertext).decode("ascii")

        payload["version"] = 1
        payload["provider"] = self.name
        payload["secrets"] = updated
        atomic_write_text(
            self.path,
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            mode=0o600,
        )
