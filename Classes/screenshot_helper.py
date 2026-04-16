"""Deprecated gameplay template capture helper.

OSROKBOT no longer captures or stores root-level gameplay templates under
``Media/``. Use the DynamicPlanner correction flow and dataset exports under
``datasets/recovery/`` to collect YOLO training examples instead.
"""

from termcolor import colored


def main():
    print(
        colored(
            "Template capture is disabled. Use DynamicPlanner Fix/correction mode to export training data.",
            "yellow",
        )
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
