# OSROKBOT Skills Reference

This file names the reusable computer-vision and input capabilities available
in the current architecture. Reference these skill names in future prompts when
asking an AI agent to extend or debug the bot.

## Computer Vision Skills

### Grayscale Template Matching

- Owner: `Classes/image_finder.py`
- Use for stable UI buttons, icons, and panels.
- Templates and screenshots are converted to grayscale and histogram-equalized
  before matching to reduce lighting variance.
- Public entry points: `find_image_coordinates(...)`, `find_image(...)`, and
  `FindImageAction` / `FindAndClickImageAction`.

### Alpha-Masked Matching

- Owner: `Classes/image_finder.py`
- Use for PNG templates with transparent backgrounds.
- Four-channel PNG templates automatically use the alpha channel as a
  `cv2.matchTemplate(..., mask=mask)` mask.
- This ignores dynamic terrain or UI backgrounds behind the solid icon pixels.

### Multi-Scale Matching

- Owner: `Classes/image_finder.py`
- Use when the game window is not exactly the template capture resolution.
- The engine keeps `template_resolution=(1280, 720)` as the base and searches
  scale multipliers around the current window scaling factor.
- The best confidence and scale are logged for diagnostics.

### ROI Searching

- Owner: `Classes/image_finder.py`
- Use when the expected object appears in a known screen area.
- Pass `search_region=(x, y, width, height)`.
- Normalized values from `0.0` to `1.0` are relative to the current screenshot.
- Pixel values are interpreted in template-resolution coordinates and scaled to
  the current window size automatically.
- Returned coordinates remain full-screenshot coordinates, so click actions stay
  compatible.

### SIFT World-Object Matching

- Owner: `Classes/image_finder.py`
- Use for non-UI objects such as barbarians, marauders, or resource nodes when
  terrain, rotation, or scale changes make template matching unreliable.
- Entry point: `find_world_object(target_path, screenshot, min_matches=...)`.
- Uses SIFT keypoints/descriptors and a BFMatcher ratio test.

### Non-Maximum Suppression

- Owner: `Classes/image_finder.py`
- Use when a template can match multiple overlapping positions.
- NMS collapses overlapping detections across scale candidates and preserves
  confidence/scale metadata for click offset scaling.

## Input Skills

### Centralized Input Execution

- Owner: `Classes/input_controller.py`
- Use for every click, move, key press, and scroll.
- Direct `pyautogui` imports are forbidden outside `InputController`.

### Bounds-Validated Clicks

- Owner: `InputController.validate_bounds(...)`
- Use whenever an action clicks or moves inside the game window.
- Prevents off-window clicks by checking the requested coordinate against the
  current game client rectangle.

### Smooth Cursor Interpolation

- Owner: `InputController.smooth_move_to(...)`
- Use for cursor moves that need reliable UI hover/move event processing.
- Movement uses a smooth non-linear interpolation path instead of instant jumps.

### Target Hitbox Sampling

- Owner: `InputController.sample_click_target(...)`
- Use for clicks against buttons and icons.
- Applies small bounded coordinate variation while clamping the result to the
  target window.

### DelayPolicy Pacing

- Owner: `Classes/input_controller.py`
- Use for all waits in actions.
- Provides centralized action delay, click settle, key hold, scroll settle, and
  polling behavior with configurable jitter.

### Pause/Abort Interlock

- Owner: `InputController.is_allowed(...)`
- Use before and during any operation that can take time.
- Checks the shared `Context.bot` pause/stop events so live automation can stop
  quickly.

## State Skills

### State Preconditions

- Owner: `Classes/state_machine.py`
- Use when an action requires a specific screen before it can run.
- Add `precondition=...` and `fallback_state=...` to `machine.add_state(...)`.
- Preconditions may be action-like objects, callables, or booleans.

### GameStateMonitor

- Required pattern for future reusable screen-state checks.
- Use it for named states such as Map View, City View, modal-open, inventory,
  troop selection, and march screen.
- If a branch does not yet contain `GameStateMonitor`, add it before creating
  more duplicated state-detection logic.
