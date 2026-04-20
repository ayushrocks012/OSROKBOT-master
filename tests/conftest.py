"""Shared pytest fixtures for stable temp paths and headless Qt tests."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_SESSION_TEMP_ROOT = Path(tempfile.gettempdir()) / "orb"


def _safe_name(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")
    return sanitized or "test"


@pytest.fixture(scope="session", autouse=True)
def _session_temp_root() -> Path:
    _SESSION_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    yield _SESSION_TEMP_ROOT
    shutil.rmtree(_SESSION_TEMP_ROOT, ignore_errors=True)


@pytest.fixture
def tmp_path(request: pytest.FixtureRequest, _session_temp_root: Path) -> Path:
    """Return a temp directory independent from pytest's workspace basetemp."""

    path = _session_temp_root / f"{_safe_name(request.node.name)}-{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture(scope="session")
def qapp():
    """Return a shared headless QApplication for PyQt unit tests."""

    from PyQt5 import QtWidgets

    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
        app.setQuitOnLastWindowClosed(False)
    return app
