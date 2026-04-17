import os

import pytest
from click_overlay import ClickOverlay
from input_controller import InputController

pytestmark = [
    pytest.mark.supervised,
    pytest.mark.skipif(
        os.getenv("OSROKBOT_RUN_SUPERVISED_TESTS") != "1",
        reason="Set OSROKBOT_RUN_SUPERVISED_TESTS=1 to run supervised workstation checks.",
    ),
]


def test_supervised_interception_backend_is_available():
    """Operator-gated check for the local Interception driver binding."""
    assert InputController.is_backend_available(), InputController.backend_error()


def test_supervised_approval_overlay_geometry_helper_is_available():
    """Operator-gated check that approval overlay geometry stays importable."""
    rect = ClickOverlay.normalized_detection_rect(
        {"x": 0.50, "y": 0.50, "width": 0.20, "height": 0.10},
        width=1000,
        height=500,
    )

    assert rect is not None
    assert rect.left() == 400
    assert rect.top() == 225
