class UIMap:
    """Normalized screen regions for reusable state and resource checks.

    Coordinates use `(x, y, width, height)` in normalized screenshot space.
    Keep values within `0.0` and `1.0`; `verify_integrity.py` validates this.
    """

    FULL_SCREEN = (0.0, 0.0, 1.0, 1.0)
    CENTER_MODAL = (0.25, 0.18, 0.50, 0.64)
    TOP_RIGHT_MARCH_SLOTS = (0.955, 0.175, 0.040, 0.040)
    TOP_ACTION_POINTS = (0.34, 0.015, 0.22, 0.055)
    MAP_VIEW_MARKER = (0.0, 0.0, 1.0, 1.0)
