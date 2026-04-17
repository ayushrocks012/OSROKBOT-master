from click_overlay import ClickOverlay


def test_normalized_detection_rect_maps_box_geometry():
    rect = ClickOverlay.normalized_detection_rect(
        {
            "x": 0.25,
            "y": 0.50,
            "width": 0.10,
            "height": 0.20,
        },
        width=400,
        height=200,
    )

    assert rect is not None
    assert rect.left() == 80
    assert rect.top() == 80
    assert rect.width() == 40
    assert rect.height() == 40
