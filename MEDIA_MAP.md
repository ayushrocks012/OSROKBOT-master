# OSROKBOT Media Map

OSROKBOT is now DynamicPlanner/YOLO-first. Root-level gameplay templates under
`Media/*.png` are obsolete and are intentionally removed by `cleanup_media.py`.

## Protected Media

These folders remain active and must not be deleted by gameplay-template cleanup:

| Path | Purpose |
| --- | --- |
| `Media/UI/` | PyQt overlay and local interface assets. |
| `Media/Readme/` | Repository documentation images. |

## Removed Gameplay Templates

The former root-level gameplay templates, such as `gatheraction.png`,
`searchaction.png`, `captchachest.png`, `confirm.png`, `escx.png`, resource
icons, report icons, and march/attack buttons, are no longer active runtime
assets.

Navigation and recovery now use:

- YOLO detections when configured through `ROK_YOLO_WEIGHTS`.
- Vision-language planner decisions through `DynamicPlannerAction`.
- Human correction exports under `datasets/recovery/` for later training.
- Diagnostic screenshots under `diagnostics/` or `data/`, not `Media/`.

## Cleanup Rule

Use:

```powershell
python cleanup_media.py --dry-run
python cleanup_media.py
```

The cleanup script deletes only `Media/Legacy/` and loose root-level
`Media/*.png` files. It does not touch `Media/UI/` or `Media/Readme/`.
