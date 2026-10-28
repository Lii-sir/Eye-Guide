from __future__ import annotations

import argparse
import ast
import logging
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


LOGGER = logging.getLogger("eyeguide.prepare_paddleseg")


@dataclass(slots=True)
class SplitPaths:
    image_dir: Path
    label_dir: Path
    output_image_dir: Path
    output_mask_dir: Path
    list_path: Path


def parse_data_yaml(path: Path) -> tuple[list[str], dict[str, str]]:
    text = path.read_text(encoding="utf-8")

    names_match = re.search(r"^names:\s*(.+)$", text, flags=re.MULTILINE)
    if not names_match:
        raise ValueError(f"Could not parse class names from {path}")
    names = ast.literal_eval(names_match.group(1).strip())
    if not isinstance(names, list) or not all(isinstance(item, str) for item in names):
        raise ValueError("Expected 'names' to be a list of strings.")

    split_map: dict[str, str] = {}
    for key in ("train", "val", "test"):
        match = re.search(rf"^{key}:\s*(.+)$", text, flags=re.MULTILINE)
        if match:
            split_map[key] = match.group(1).strip()
    return names, split_map


def resolve_split_name(source_name: str) -> str:
    return "valid" if source_name == "val" else source_name


def build_split_paths(input_root: Path, output_root: Path, split_name: str) -> SplitPaths:
    source_split = resolve_split_name(split_name)
    image_dir = input_root / source_split / "images"
    label_dir = input_root / source_split / "labels"
    output_image_dir = output_root / "images" / split_name
    output_mask_dir = output_root / "annotations" / split_name
    list_path = output_root / f"{split_name}.txt"
    return SplitPaths(
        image_dir=image_dir,
        label_dir=label_dir,
        output_image_dir=output_image_dir,
        output_mask_dir=output_mask_dir,
        list_path=list_path,
    )


def yolo_point_to_pixel(value: float, size: int) -> int:
    return max(0, min(size - 1, int(round(value * (size - 1)))))


def parse_label_line(line: str, width: int, height: int) -> tuple[int, np.ndarray] | None:
    parts = line.strip().split()
    if not parts:
        return None

    class_id = int(float(parts[0]))
    coords = [float(part) for part in parts[1:]]

    if len(coords) >= 6 and len(coords) % 2 == 0:
        points = [
            [yolo_point_to_pixel(coords[index], width), yolo_point_to_pixel(coords[index + 1], height)]
            for index in range(0, len(coords), 2)
        ]
        return class_id, np.asarray(points, dtype=np.int32)

    if len(coords) == 4:
        x_center, y_center, box_width, box_height = coords
        x1 = yolo_point_to_pixel(x_center - box_width / 2.0, width)
        y1 = yolo_point_to_pixel(y_center - box_height / 2.0, height)
        x2 = yolo_point_to_pixel(x_center + box_width / 2.0, width)
        y2 = yolo_point_to_pixel(y_center + box_height / 2.0, height)
        points = np.asarray([[x1, y1], [x2, y1], [x2, y2], [x1, y2]], dtype=np.int32)
        return class_id, points

    LOGGER.warning("Skipping malformed label row: %s", line[:120])
    return None


def convert_one(
    image_path: Path,
    label_path: Path,
    destination_image_path: Path,
    destination_mask_path: Path,
) -> bool:
    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        LOGGER.warning("Failed to read image: %s", image_path)
        return False
    height, width = image.shape[:2]
    mask = np.zeros((height, width), dtype=np.uint8)

    if label_path.exists():
        for raw_line in label_path.read_text(encoding="utf-8").splitlines():
            parsed = parse_label_line(raw_line, width, height)
            if parsed is None:
                continue
            class_id, polygon = parsed
            if polygon.shape[0] < 3:
                continue
            cv2.fillPoly(mask, [polygon], color=int(class_id) + 1)

    destination_image_path.parent.mkdir(parents=True, exist_ok=True)
    destination_mask_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(image_path, destination_image_path)
    cv2.imwrite(str(destination_mask_path), mask)
    return True


def convert_split(split: SplitPaths) -> int:
    if not split.image_dir.exists():
        LOGGER.info("Skipping missing split: %s", split.image_dir)
        return 0

    image_paths = sorted(path for path in split.image_dir.iterdir() if path.is_file())
    written = 0
    with split.list_path.open("w", encoding="utf-8") as handle:
        for image_path in image_paths:
            label_path = split.label_dir / f"{image_path.stem}.txt"
            destination_image_path = split.output_image_dir / image_path.name
            destination_mask_path = split.output_mask_dir / f"{image_path.stem}.png"
            if not convert_one(
                image_path=image_path,
                label_path=label_path,
                destination_image_path=destination_image_path,
                destination_mask_path=destination_mask_path,
            ):
                continue
            relative_image = destination_image_path.relative_to(split.list_path.parent).as_posix()
            relative_mask = destination_mask_path.relative_to(split.list_path.parent).as_posix()
            handle.write(f"{relative_image} {relative_mask}\n")
            written += 1
    return written


def write_labels_file(output_root: Path, class_names: list[str]) -> None:
    labels = ["background", *class_names]
    (output_root / "labels.txt").write_text("\n".join(labels) + "\n", encoding="utf-8")


def write_readme(output_root: Path, class_names: list[str], counts: dict[str, int], source_root: Path) -> None:
    content = "\n".join(
        [
            "# PaddleSeg Dataset",
            "",
            f"Source: {source_root}",
            f"Classes: {', '.join(['background', *class_names])}",
            "",
            "Files:",
            "- train.txt / val.txt list image-mask pairs.",
            "- images/<split>/ stores copied source images.",
            "- annotations/<split>/ stores PNG masks.",
            "- labels.txt stores class names.",
            "",
            "Counts:",
            *(f"- {split}: {count}" for split, count in counts.items()),
            "",
            "Mask label ids:",
            "- 0: background",
            *(f"- {index}: {name}" for index, name in enumerate(class_names, start=1)),
        ]
    )
    (output_root / "README.md").write_text(content + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert YOLO segmentation labels into a PaddleSeg dataset layout."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("datasets/blind_road"),
        help="YOLO dataset root containing data.yaml and split folders.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("datasets/blind_road_paddleseg"),
        help="Output dataset root for PaddleSeg.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level), format="%(message)s")

    input_root = args.input.resolve()
    output_root = args.output.resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    data_yaml = input_root / "data.yaml"
    if not data_yaml.exists():
        LOGGER.error("Missing data.yaml under %s", input_root)
        return 1

    try:
        class_names, split_map = parse_data_yaml(data_yaml)
    except ValueError as exc:
        LOGGER.error("%s", exc)
        return 1

    counts: dict[str, int] = {}
    for split_name in ("train", "val", "test"):
        if split_name not in split_map:
            continue
        split = build_split_paths(input_root, output_root, split_name)
        count = convert_split(split)
        counts[split_name] = count
        LOGGER.info("Converted %s samples for %s", count, split_name)

    write_labels_file(output_root, class_names)
    write_readme(output_root, class_names, counts, input_root)
    LOGGER.info("PaddleSeg dataset is ready at %s", output_root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
