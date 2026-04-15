import cv2
import numpy as np
from input_controller import InputController
from termcolor import colored


class ImageFinder:
    """OpenCV image lookup service for game UI and world-object detection.

    UI templates use grayscale, multi-scale template matching. Four-channel PNG
    templates automatically use their alpha channel as a mask, which ignores
    transparent/background pixels during matching. Callers can provide an
    optional ROI as `(x, y, width, height)` in either normalized screen units
    (`0.0` to `1.0`) or template-resolution pixels; pixel regions are scaled to
    the current window size before matching. World-map objects can use SIFT via
    `find_world_object()` when shape/terrain variance makes template matching
    unreliable.
    """

    def __init__(self, threshold=0.8, debug_output=False):
        self.threshold = threshold
        self.debug_output = debug_output
        self.template_resolution = (1280, 720)  # original resolution at which the template was taken
        self.scale_multipliers = np.arange(0.8, 1.2001, 0.05)
        self.max_raw_matches_per_scale = 250
        self._template_cache = {}
        self._sift = None

    def _get_scaling_factor(self, screenshot):
        win_width = screenshot.shape[1]
        win_height = screenshot.shape[0]

        # If the window is perfectly sized (native 1280x720 client area),
        # bounds are around 1296x759 on Windows 10/11. Return exact 1.0
        # to prevent destructive cv2.resize interpolation on text buttons.
        if 1280 <= win_width <= 1300 and 720 <= win_height <= 770:
            return (1.0, 1.0)

        screen_resolution = (win_width - 8, win_height - 31)  # (width, height)
        scaling_factor = (
            screen_resolution[0] / self.template_resolution[0],
            screen_resolution[1] / self.template_resolution[1],
        )  # (scale_x, scale_y)
        return scaling_factor

    def _load_template(self, target_image_path):
        if target_image_path in self._template_cache:
            return self._template_cache[target_image_path]

        template = cv2.imread(target_image_path, cv2.IMREAD_UNCHANGED)
        if template is None:
            print(colored(f"Template image not found: {target_image_path}", "red"))
            self._template_cache[target_image_path] = (None, None)
            return None, None

        mask = None
        if template.ndim == 3 and template.shape[2] == 4:
            mask = template[:, :, 3]
            template = template[:, :, :3]
            print(colored(f"template match: alpha mask enabled for {target_image_path}", "cyan"))

        if template.ndim == 3:
            template = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)

        template = self._normalize_gray(template)
        self._template_cache[target_image_path] = (template, mask)
        return template, mask

    @staticmethod
    def _normalize_gray(image):
        if image.dtype != np.uint8:
            image = cv2.normalize(image, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
        return cv2.equalizeHist(image)

    @staticmethod
    def _to_gray_screenshot(screenshot):
        screenshot_cv = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)
        screenshot_gray = cv2.cvtColor(screenshot_cv, cv2.COLOR_BGR2GRAY)
        return ImageFinder._normalize_gray(screenshot_gray), screenshot_cv

    def _scale_search_region(self, search_region, base_scale, screenshot_shape):
        """Scale and clamp an optional ROI to screenshot pixel coordinates."""
        if not search_region:
            return None

        x, y, width, height = search_region
        screenshot_h, screenshot_w = screenshot_shape[:2]

        # Support normalized regions for future callers, but default to
        # template-resolution pixels scaled by the current window size.
        if all(0 <= value <= 1 for value in search_region):
            scaled_x = int(round(x * screenshot_w))
            scaled_y = int(round(y * screenshot_h))
            scaled_w = int(round(width * screenshot_w))
            scaled_h = int(round(height * screenshot_h))
        else:
            scaled_x = int(round(x * base_scale[0]))
            scaled_y = int(round(y * base_scale[1]))
            scaled_w = int(round(width * base_scale[0]))
            scaled_h = int(round(height * base_scale[1]))

        scaled_x = max(0, min(scaled_x, screenshot_w - 1))
        scaled_y = max(0, min(scaled_y, screenshot_h - 1))
        scaled_w = max(1, min(scaled_w, screenshot_w - scaled_x))
        scaled_h = max(1, min(scaled_h, screenshot_h - scaled_y))
        return scaled_x, scaled_y, scaled_w, scaled_h

    def _resize_template(self, template, mask, scale_x, scale_y):
        resized_template = cv2.resize(
            template,
            None,
            fx=scale_x,
            fy=scale_y,
            interpolation=cv2.INTER_AREA,
        )
        resized_mask = None
        if mask is not None:
            resized_mask = cv2.resize(
                mask,
                (resized_template.shape[1], resized_template.shape[0]),
                interpolation=cv2.INTER_NEAREST,
            )
        return resized_template, resized_mask

    def _match_image(self, target_image_path, screenshot, search_region=None):
        """Return multi-scale template matches for a screenshot.

        `search_region` narrows matching to a scaled ROI but returned match
        coordinates are still relative to the full screenshot, preserving
        compatibility with click actions.
        """
        screenshot_gray, screenshot_cv = self._to_gray_screenshot(screenshot)
        template, mask = self._load_template(target_image_path)

        base_scale = self._get_scaling_factor(screenshot_gray)
        region = self._scale_search_region(search_region, base_scale, screenshot_gray.shape)
        region_offset_x = 0
        region_offset_y = 0
        search_gray = screenshot_gray
        if region:
            region_offset_x, region_offset_y, region_w, region_h = region
            search_gray = screenshot_gray[
                region_offset_y:region_offset_y + region_h,
                region_offset_x:region_offset_x + region_w,
            ]

        if template is None:
            return base_scale, [], 0, 0, None, screenshot_cv

        best_scale = base_scale
        best_max_val = 0
        best_template = template
        matches = []
        method_without_mask = cv2.TM_CCOEFF_NORMED
        method_with_mask = cv2.TM_CCORR_NORMED
        method_name = "masked multi-scale template" if mask is not None else "multi-scale template"

        for scale_multiplier in self.scale_multipliers:
            scale = (base_scale[0] * scale_multiplier, base_scale[1] * scale_multiplier)
            resized_template, resized_mask = self._resize_template(template, mask, scale[0], scale[1])

            template_h, template_w = resized_template.shape[:2]
            if template_h <= 0 or template_w <= 0:
                continue
            if template_h > search_gray.shape[0] or template_w > search_gray.shape[1]:
                continue

            method = method_with_mask if resized_mask is not None else method_without_mask
            try:
                if resized_mask is not None:
                    result = cv2.matchTemplate(
                        search_gray,
                        resized_template,
                        method,
                        mask=resized_mask,
                    )
                else:
                    result = cv2.matchTemplate(search_gray, resized_template, method)
            except cv2.error as exc:
                print(colored(f"Unable to match {target_image_path} at scale {scale}: {exc}", "red"))
                continue

            result = np.nan_to_num(result, nan=0.0, posinf=0.0, neginf=0.0)
            max_val = float(result.max()) if result.size else 0
            if max_val > best_max_val:
                best_max_val = max_val
                best_scale = scale
                best_template = resized_template

            locations = np.where(result >= self.threshold)
            if not locations[0].size:
                continue

            scores = result[locations]
            if scores.size > self.max_raw_matches_per_scale:
                top_indexes = np.argpartition(scores, -self.max_raw_matches_per_scale)[-self.max_raw_matches_per_scale:]
            else:
                top_indexes = np.arange(scores.size)

            for index in top_indexes:
                y = int(locations[0][index])
                x = int(locations[1][index])
                score = float(scores[index])
                matches.append((
                    x + region_offset_x,
                    y + region_offset_y,
                    template_w,
                    template_h,
                    scale[0],
                    scale[1],
                    score,
                ))

        boxes = self._match_records_to_boxes(matches)
        picked_boxes = ImageFinder.non_max_suppression_fast(boxes, 0.3)
        picked_matches = self._boxes_to_match_records(picked_boxes)

        for box in picked_boxes:
            start_x, start_y, end_x, end_y = box[:4]
            cv2.rectangle(
                screenshot_cv,
                (int(start_x), int(start_y)),
                (int(end_x), int(end_y)),
                (255, 0, 255),
                2,
            )

        if self.debug_output:
            cv2.imwrite("screenshot.png", screenshot_cv)

        color = "green" if best_max_val >= self.threshold else "red"
        print(
            colored(
                f"{method_name}: {target_image_path} confidence={best_max_val:.4f} "
                f"scale=({best_scale[0]:.2f},{best_scale[1]:.2f}) "
                f"roi={region if region else 'full'} matches={len(picked_matches)}",
                color,
            )
        )

        return best_scale, picked_matches, len(picked_matches), best_max_val, best_template, screenshot_cv

    @staticmethod
    def _match_records_to_boxes(matches):
        if not matches:
            return np.empty((0, 5), dtype=float)
        return np.array(
            [
                (x, y, x + width, y + height, score, scale_x, scale_y)
                for x, y, width, height, scale_x, scale_y, score in matches
            ],
            dtype=float,
        )

    @staticmethod
    def _boxes_to_match_records(boxes):
        if len(boxes) == 0:
            return []
        records = []
        for box in boxes:
            start_x, start_y, end_x, end_y = box[:4]
            score = box[4] if len(box) >= 5 else 0.0
            scale_x = box[5] if len(box) >= 7 else 1.0
            scale_y = box[6] if len(box) >= 7 else 1.0
            records.append(
                (
                    int(start_x),
                    int(start_y),
                    int(end_x - start_x),
                    int(end_y - start_y),
                    float(scale_x),
                    float(scale_y),
                    float(score),
                )
            )
        return records

    def _get_pick_boxes(self, matches):
        return ImageFinder.non_max_suppression_fast(self._match_records_to_boxes(matches), 0.3)

    def find_image_coordinates(self, target_image_path, screenshot, win, x_offset, y_offset, max_matches, search_region=None):
        best_scale, best_loc, num_matches, best_max_val, target_image, screenshot_cv = self._match_image(
            target_image_path,
            screenshot,
            search_region=search_region,
        )

        if best_max_val >= self.threshold:
            pick = self._get_pick_boxes(best_loc)

            for box in pick:
                start_x, start_y, end_x, end_y = box[:4]
                scale_x = box[5] if len(box) > 6 else best_scale[0]
                scale_y = box[6] if len(box) > 6 else best_scale[1]
                center_x = int(start_x + (end_x - start_x) // 2 + win.left)
                center_y = int(start_y + (end_y - start_y) // 2 + win.top)

                x_offset_scaled = int(x_offset * scale_x)
                y_offset_scaled = int(y_offset * scale_y)

                final_x = center_x + x_offset_scaled
                final_y = center_y + y_offset_scaled

                return True, final_x, final_y, len(pick)

            return True, None, None, len(pick)
        return False, None, None, 0

    def find_and_click_image(self, target_image_path, screenshot, win, x_offset, y_offset, max_matches, search_region=None):
        best_scale, best_loc, num_matches, best_max_val, target_image, screenshot_cv = self._match_image(
            target_image_path,
            screenshot,
            search_region=search_region,
        )

        if best_max_val >= self.threshold:
            pick = self._get_pick_boxes(best_loc)
            if len(pick) >= max_matches and max_matches != 0:
                return False
            if len(pick) < max_matches and max_matches != 0:
                return True

            if len(pick) == 0:
                return False

            start_x, start_y, end_x, end_y = pick[0][:4]
            scale_x = pick[0][5] if len(pick[0]) > 6 else best_scale[0]
            scale_y = pick[0][6] if len(pick[0]) > 6 else best_scale[1]
            center_x = int(start_x + (end_x - start_x) // 2 + win.left)
            center_y = int(start_y + (end_y - start_y) // 2 + win.top)
            x_offset_scaled = int(x_offset * scale_x)
            y_offset_scaled = int(y_offset * scale_y)

            return InputController().click(
                center_x + x_offset_scaled,
                center_y + y_offset_scaled,
                window_rect=win,
            )

        if target_image_path != "Media/captchachest.png":
            print(colored(f"No matches for {target_image_path} found in screenshot.", "red"))
        if max_matches != 0:
            return True
        return False

    def find_world_object(self, target_path, screenshot, min_matches=10, ratio_threshold=0.75):
        screenshot_gray, _ = self._to_gray_screenshot(screenshot)
        template, _ = self._load_template(target_path)
        if template is None:
            return False, None

        if self._sift is None:
            try:
                self._sift = cv2.SIFT_create()
            except AttributeError:
                print(colored("SIFT is not available in this OpenCV build.", "red"))
                return False, None

        keypoints_template, descriptors_template = self._sift.detectAndCompute(template, None)
        keypoints_screen, descriptors_screen = self._sift.detectAndCompute(screenshot_gray, None)

        if descriptors_template is None or descriptors_screen is None:
            print(colored(f"SIFT: no descriptors for {target_path}", "red"))
            return False, None

        matcher = cv2.BFMatcher(cv2.NORM_L2)
        raw_matches = matcher.knnMatch(descriptors_template, descriptors_screen, k=2)
        good_matches = []
        for match_pair in raw_matches:
            if len(match_pair) < 2:
                continue
            first, second = match_pair
            if first.distance < ratio_threshold * second.distance:
                good_matches.append(first)

        confidence = len(good_matches) / max(len(keypoints_template), 1)
        print(
            colored(
                f"SIFT world match: {target_path} good_matches={len(good_matches)} "
                f"confidence={confidence:.4f}",
                "green" if len(good_matches) >= min_matches else "red",
            )
        )

        if len(good_matches) < min_matches:
            return False, None

        points = np.float32([keypoints_screen[match.trainIdx].pt for match in good_matches])
        center_x, center_y = np.mean(points, axis=0).astype(int)
        center = (int(center_x), int(center_y))
        return True, center

    @staticmethod
    def non_max_suppression_fast(boxes, overlapThresh):
        if len(boxes) == 0:
            return np.empty((0, 5), dtype=float)

        boxes = boxes.astype(float)
        has_scores = boxes.shape[1] >= 5

        x1 = boxes[:, 0]
        y1 = boxes[:, 1]
        x2 = boxes[:, 2]
        y2 = boxes[:, 3]
        scores = boxes[:, 4] if has_scores else y2

        area = (x2 - x1 + 1) * (y2 - y1 + 1)
        idxs = np.argsort(scores)
        pick = []

        while len(idxs) > 0:
            last = len(idxs) - 1
            i = idxs[last]
            pick.append(i)

            xx1 = np.maximum(x1[i], x1[idxs[:last]])
            yy1 = np.maximum(y1[i], y1[idxs[:last]])
            xx2 = np.minimum(x2[i], x2[idxs[:last]])
            yy2 = np.minimum(y2[i], y2[idxs[:last]])

            w = np.maximum(0, xx2 - xx1 + 1)
            h = np.maximum(0, yy2 - yy1 + 1)

            overlap = (w * h) / area[idxs[:last]]
            idxs = np.delete(
                idxs,
                np.concatenate(([last], np.where(overlap > overlapThresh)[0])),
            )

        return boxes[pick]

    def find_image(self, target_image_path, screenshot, search_region=None):
        best_scale, best_loc, num_matches, best_max_val, target_image, screenshot_cv = self._match_image(
            target_image_path,
            screenshot,
            search_region=search_region,
        )

        if best_max_val >= self.threshold:
            print(colored(f"found {target_image_path} {num_matches}x at {best_max_val:.4f}", "green"))
            return True
        print(colored(f"No matches for {target_image_path} found in screenshot.", "red"))
        return False
