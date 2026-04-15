from datetime import datetime
from pathlib import Path
from shutil import copyfile

from termcolor import colored


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATASET_DIR = PROJECT_ROOT / "datasets" / "recovery"


class DetectionDataset:
    def __init__(self, output_dir=DEFAULT_DATASET_DIR):
        self.output_dir = Path(output_dir)

    @staticmethod
    def _safe_label(value):
        return "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in str(value))

    def export_stub(self, screenshot_path, state_name, action_image=None, detections=None):
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
            print(colored(f"Unable to export detection dataset stub: {exc}", "red"))
            return None

        print(colored(f"Detection dataset stub exported: {image_path}", "yellow"))
        return image_path
