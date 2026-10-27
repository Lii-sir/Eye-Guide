from __future__ import annotations

from pathlib import Path

from transformers import AutoImageProcessor, AutoModelForDepthEstimation


MODEL_NAME = "depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf"
TARGET_DIR = Path(__file__).resolve().parent.parent / "models" / "depth-anything-v2"


def main() -> None:
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Downloading processor to: {TARGET_DIR}")
    processor = AutoImageProcessor.from_pretrained(MODEL_NAME)
    processor.save_pretrained(TARGET_DIR)

    print(f"Downloading model to: {TARGET_DIR}")
    model = AutoModelForDepthEstimation.from_pretrained(MODEL_NAME)
    model.save_pretrained(TARGET_DIR)

    print("Depth model cached successfully.")


if __name__ == "__main__":
    main()
