# OSROKBOT Media Map

OSROKBOT is VLM/YOLO-first. Root-level gameplay template images are deprecated
and are not part of the supported runtime asset set.

`cleanup_media.py` is the source of truth for media cleanup behavior.

## Protected Assets

These directories are protected and must not be deleted by gameplay media
cleanup:

| Path | Protected | Purpose |
| --- | --- | --- |
| `Media/UI/` | Yes | PyQt overlay icons and local interface assets. |
| `Media/Readme/` | Yes | README and documentation images, GIFs, and screenshots. |

`cleanup_media.py` also avoids deleting files nested under any other
subdirectory of `Media/`; it targets only the deprecated paths listed below.

## Deprecated Assets

The cleanup policy permanently deprecates:

| Path Pattern | Status | Cleanup Behavior |
| --- | --- | --- |
| `Media/Legacy/` | Deprecated | Deleted as a directory when present. |
| `Media/*.png` | Deprecated | Loose PNG files directly under `Media/` are deleted. |

Examples of deprecated root-level files include former button, report, resource,
modal, CAPTCHA, march, attack, gather, and search templates. These assets are
obsolete because planning now uses screenshots, YOLO labels, OCR text, OpenAI
vision reasoning, and local visual memory.

## Cleanup Commands

Preview cleanup:

```powershell
python cleanup_media.py --dry-run
```

Interactive cleanup:

```powershell
python cleanup_media.py
```

Noninteractive cleanup:

```powershell
python cleanup_media.py --yes
```

## What Cleanup Does

The cleanup utility:

- Collects `Media/Legacy/` when present.
- Collects loose `*.png` files directly under `Media/`.
- Skips `Media/UI/`.
- Skips `Media/Readme/`.
- Skips files nested under other `Media/` subdirectories.
- Prints all targets before deletion.
- Requires `DELETE` confirmation unless `--yes` is passed.

## Runtime Asset Policy

New runtime perception assets should not be placed in root `Media/`.

Use these locations instead:

| Asset Type | Location |
| --- | --- |
| Overlay icons | `Media/UI/` |
| Documentation images | `Media/Readme/` |
| YOLO model weights | `models/` or an external local path referenced by `ROK_YOLO_WEIGHTS` |
| Human correction datasets | `datasets/recovery/` |
| Planner screenshots and local memory | `data/` |
| Debug captures and heatmaps | `diagnostics/` |

## Maintainer Rules

- Do not reintroduce loose root-level gameplay PNG templates.
- Do not recreate `Media/Legacy/`.
- Update this file when adding protected media directories.
- Run the cleanup dry-run before changing media policy.
- Run `python verify_integrity.py` after media or documentation updates.
