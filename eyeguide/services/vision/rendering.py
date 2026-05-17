from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


class UnicodeFrameRenderer:
    def draw_box_label(
        self,
        frame: np.ndarray,
        box: tuple[int, int, int, int],
        text: str,
        color: tuple[int, int, int],
    ) -> None:
        x1, y1, x2, y2 = box
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)

        image = self._to_pil(frame)
        draw = ImageDraw.Draw(image)
        font = self._get_font(20)
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font, stroke_width=1)
        text_width = right - left
        text_height = bottom - top
        label_left = x1
        label_top = max(4, y1 - text_height - 14)
        label_right = x1 + text_width + 18
        label_bottom = label_top + text_height + 10
        draw.rounded_rectangle(
            (label_left, label_top, label_right, label_bottom),
            radius=8,
            fill=(color[2], color[1], color[0]),
        )
        draw.text(
            (label_left + 9, label_top + 4),
            text,
            font=font,
            fill=(255, 255, 255),
            stroke_width=1,
            stroke_fill=(0, 0, 0),
        )
        frame[:] = self._to_bgr(image)

    def draw_panel(
        self,
        frame: np.ndarray,
        lines: list[str],
        origin: tuple[int, int] = (20, 16),
        font_size: int = 23,
        text_color: tuple[int, int, int] = (255, 255, 255),
    ) -> None:
        if not lines:
            return

        image = self._to_pil(frame)
        draw = ImageDraw.Draw(image)
        font = self._get_font(font_size)
        padding_x = 14
        padding_y = 12
        line_gap = 8

        text_boxes = [draw.textbbox((0, 0), line, font=font, stroke_width=1) for line in lines]
        text_width = max((box[2] - box[0]) for box in text_boxes)
        line_heights = [(box[3] - box[1]) for box in text_boxes]
        panel_width = text_width + padding_x * 2
        panel_height = sum(line_heights) + line_gap * (len(lines) - 1) + padding_y * 2
        left, top = origin
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        overlay_draw.rounded_rectangle(
            (left, top, left + panel_width, top + panel_height),
            radius=12,
            fill=(0, 0, 0, 130),
        )
        image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
        draw = ImageDraw.Draw(image)

        current_y = top + padding_y
        for line, line_height in zip(lines, line_heights):
            draw.text(
                (left + padding_x, current_y),
                line,
                font=font,
                fill=text_color,
                stroke_width=1,
                stroke_fill=(0, 0, 0),
            )
            current_y += line_height + line_gap

        frame[:] = self._to_bgr(image)

    def draw_bottom_banner(self, frame: np.ndarray, text: str) -> None:
        height, width = frame.shape[:2]
        banner_height = 78
        overlay = frame.copy()
        cv2.rectangle(overlay, (0, height - banner_height), (width, height), (0, 0, 0), -1)
        frame[:] = cv2.addWeighted(overlay, 0.45, frame, 0.55, 0)

        image = self._to_pil(frame)
        draw = ImageDraw.Draw(image)
        font = self._get_font(22)
        draw.text(
            (20, height - banner_height + 20),
            text,
            font=font,
            fill=(255, 255, 255),
            stroke_width=1,
            stroke_fill=(0, 0, 0),
        )
        frame[:] = self._to_bgr(image)

    def _to_pil(self, frame: np.ndarray) -> Image.Image:
        return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))

    def _to_bgr(self, image: Image.Image) -> np.ndarray:
        return cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)

    @lru_cache(maxsize=8)
    def _get_font(self, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        font_path = self._find_font_path()
        if font_path is not None:
            return ImageFont.truetype(str(font_path), size=size)
        return ImageFont.load_default()

    def _find_font_path(self) -> Path | None:
        candidates = [
            Path("C:/Windows/Fonts/msyh.ttc"),
            Path("C:/Windows/Fonts/simhei.ttf"),
            Path("C:/Windows/Fonts/simsun.ttc"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None
