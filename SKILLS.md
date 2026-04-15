# OSROKBOT Technical Skills

This file documents the capabilities that are already present in the codebase.
Use these names when asking an agent to extend, debug, or document OSROKBOT.

## SIFT-Based World Matching

- Owner: `Classes/image_finder.py`
- Entry point: `ImageFinder.find_world_object(...)`
- Intended use: non-UI world objects such as marauders, barbarians, resource
  nodes, or terrain-embedded targets where template matching is brittle.
- Implementation: OpenCV SIFT keypoints/descriptors, BFMatcher L2 matching, and
  Lowe-style ratio filtering.
- Result shape: returns `(True, (x, y))` when enough feature matches are found;
  otherwise returns `(False, None)`.
- Extension rule: use this for world-object recognition before adding manual
  scan loops that depend only on raw template matching.

## ROI-Scaled Searches

- Owner: `Classes/image_finder.py`
- Entry points: `find_image_coordinates(...)`, `find_image(...)`,
  `FindImageAction`, and `FindAndClickImageAction`
- Intended use: buttons, icons, and state markers expected in a known screen
  region.
- Region format: `search_region=(x, y, width, height)`.
- Normalized mode: values from `0.0` to `1.0` are interpreted against the
  current screenshot size.
- Template-pixel mode: larger values are interpreted against the base
  `1280x720` template resolution and scaled to the current game window.
- Coordinate guarantee: matched coordinates are returned in full screenshot
  space, so existing click actions and offsets remain compatible.

## Alpha-Masked Matching

- Owner: `Classes/image_finder.py`
- Entry points: all template-matching paths that load a PNG template.
- Intended use: UI templates with transparent backgrounds or icons captured
  over dynamic game content.
- Implementation: four-channel PNGs automatically use their alpha channel as a
  mask for `cv2.matchTemplate(...)`.
- Matching method: masked templates use `TM_CCORR_NORMED`; unmasked templates
  use `TM_CCOEFF_NORMED`.
- Extension rule: prefer transparent PNG templates when the target shape is
  stable but the surrounding background changes.

## Multi-Scale Template Matching

- Owner: `Classes/image_finder.py`
- Intended use: normal UI detection across slight window-size differences.
- Base resolution: `1280x720`.
- Implementation: computes the current window scale and searches nearby scale
  multipliers before applying non-maximum suppression.
- Diagnostic output: logs best confidence, scale, ROI, and match count.

## Non-Maximum Suppression

- Owner: `Classes/image_finder.py`
- Intended use: collapse overlapping detections across scale candidates.
- Implementation: `ImageFinder.non_max_suppression_fast(...)` preserves score
  and scale metadata used later for click offset scaling.

## Centralized Input Execution

- Owner: `Classes/input_controller.py`
- Intended use: every click, move, key press, and scroll.
- Safety behavior: validates window bounds, checks pause/stop interlocks,
  smooths movement, adds bounded click jitter, and applies settle delays.
- Rule: do not import or call `pyautogui` or `pydirectinput` outside
  `InputController`.

## DelayPolicy Pacing

- Owner: `Classes/input_controller.py`
- Intended use: all waits in action execution.
- Behavior: supports action delays, post-action delays, key-hold timing, click
  settle timing, scroll settle timing, polling, and jitter.
- Rule: actions should use `Action(delay=..., post_delay=...)` or
  `DelayPolicy.wait(...)`; they should not call `time.sleep()`.

## State Preconditions

- Owner: `Classes/state_machine.py`
- Intended use: verify a required screen before executing an action.
- API: pass `precondition=...` and `fallback_state=...` to
  `machine.add_state(...)`.
- Accepted preconditions: action-like objects with `perform(context)`,
  callables that accept `context`, or simple booleans.
- Rule: reusable game-state checks should move into `GameStateMonitor` when that
  abstraction is present or being added.
