from datetime import datetime
from pathlib import Path
from shutil import copyfile

from artifact_retention import ArtifactRetentionManager, policy_from_environment
from logging_config import get_logger

LOGGER = get_logger(__name__)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATASET_DIR = PROJECT_ROOT / "datasets" / "recovery"
DEFAULT_DATASET_RETENTION = policy_from_environment(
    max_groups_env="ROK_RECOVERY_DATASET_MAX_SAMPLES",
    max_age_days_env="ROK_RECOVERY_DATASET_MAX_AGE_DAYS",
    default_max_groups=300,
    default_max_age_days=30.0,
)


class DetectionDataset:
    """Export bounded recovery-training stubs from runtime screenshots."""

    def __init__(
        self,
        output_dir=DEFAULT_DATASET_DIR,
        retention_manager: ArtifactRetentionManager | None = None,
    ):
        self.output_dir = Path(output_dir)
        self.retention_manager = retention_manager or ArtifactRetentionManager()

    @staticmethod
    def _safe_label(value):
        return "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in str(value))

    def export_stub(self, screenshot_path, state_name, action_image=None, detections=None):
        """Export one dataset stub image plus metadata for offline labeling."""

        if not screenshot_path:
            return None

        source = Path(screenshot_path)
        if not source.is_file():
            return None

        self.output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        stem = f"{self._safe_label(state_name)}_{timestamp}"
        image_path = self.output_dir / f"{stem}{source.suffix.lower() or '.png'}"
        label_path = self.output_dir / f"{stem}.txt"
        meta_path = self.output_dir / f"{stem}.meta"

        try:
            copyfile(source, image_path)
            label_path.write_text(
                "# YOLO labels: class_id x_center y_center width height\n",
                encoding="utf-8",
            )
            meta_lines = [
                f"state_name={state_name}",
                f"action_image={action_image or ''}",
            ]
            for detection in detections or []:
                if hasattr(detection, "to_dict"):
                    detection = detection.to_dict()
                meta_lines.append(f"detection={detection}")
            meta_path.write_text("\n".join(meta_lines) + "\n", encoding="utf-8")
        except Exception as exc:
            LOGGER.error(f"Unable to export detection dataset stub: {exc}")
            return None

        self.retention_manager.prune_directory(self.output_dir, DEFAULT_DATASET_RETENTION)
        LOGGER.warning(f"Detection dataset stub exported: {image_path}")
        return image_path

    def export_correction(self, screenshot_path, decision, corrected_point, detections=None):
        """Export one planner-correction sample alongside the corrected point."""

        image_path = self.export_stub(
            screenshot_path,
            "planner_correction",
            action_image=getattr(decision, "label", None) or "dynamic_planner",
            detections=detections,
        )
        if not image_path:
            return None
        point_path = image_path.with_suffix(".point")
        if hasattr(decision, "to_dict"):
            decision = decision.to_dict()
        point_path.write_text(
            "\n".join(
                [
                    f"label={decision.get('label', '')}",
                    f"original_x={decision.get('x', '')}",
                    f"original_y={decision.get('y', '')}",
                    f"corrected_x={corrected_point.get('x', '')}",
                    f"corrected_y={corrected_point.get('y', '')}",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        return image_path
