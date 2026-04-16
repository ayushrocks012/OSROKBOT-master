"""Deprecated gameplay template capture helper.

OSROKBOT no longer captures or stores root-level gameplay templates under
``Media/``. Use the DynamicPlanner correction flow and dataset exports under
``datasets/recovery/`` to collect YOLO training examples instead.
"""

from logging_config import get_logger

LOGGER = get_logger(__name__)


def main():
    LOGGER.warning("Template capture is disabled. Use DynamicPlanner Fix/correction mode to export training data.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
