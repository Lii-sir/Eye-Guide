from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "assets" / "icons"
PNG_PATH = OUTPUT_DIR / "eye-guide.png"
ICO_PATH = OUTPUT_DIR / "eye-guide.ico"
WINDOW_PNG_PATH = OUTPUT_DIR / "eye-guide-window.png"
WINDOW_ICO_PATH = OUTPUT_DIR / "eye-guide-window.ico"


def _hex_color(value: str, alpha: int = 255) -> tuple[int, int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4)) + (alpha,)


def _lerp(a: int, b: int, t: float) -> int:
    return round(a + (b - a) * t)


def _interpolate(c1: tuple[int, int, int], c2: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(_lerp(a, b, t) for a, b in zip(c1, c2))


def _rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    return mask


def _diagonal_gradient(size: int, start: str, end: str, radius: int) -> Image.Image:
    start_rgb = _hex_color(start)[:3]
    end_rgb = _hex_color(end)[:3]
    image = Image.new("RGBA", (size, size))
    pixels = image.load()
    denom = max((size - 1) * 2, 1)
    for y in range(size):
        for x in range(size):
            t = (x + y) / denom
            pixels[x, y] = _interpolate(start_rgb, end_rgb, t) + (255,)
    image.putalpha(_rounded_mask(size, radius))
    return image


def _vertical_gradient_polygon(
    base: Image.Image,
    points: list[tuple[float, float]],
    top: str,
    bottom: str,
    opacity_top: int,
    opacity_bottom: int,
) -> None:
    size = base.width
    overlay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).polygon(points, fill=255)

    top_rgb = _hex_color(top)[:3]
    bottom_rgb = _hex_color(bottom)[:3]
    pixels = overlay.load()
    denom = max(size - 1, 1)
    for y in range(size):
        t = y / denom
        rgb = _interpolate(bottom_rgb, top_rgb, 1 - t)
        alpha = _lerp(opacity_top, opacity_bottom, t)
        for x in range(size):
            pixels[x, y] = rgb + (alpha,)

    overlay.putalpha(ImageChops.multiply(overlay.getchannel("A"), mask))
    base.alpha_composite(overlay)


def _draw_background_rings(draw: ImageDraw.ImageDraw, size: int) -> None:
    cx = cy = size / 2
    cy -= size * 0.05
    for radius, dash_deg in ((size * 0.35, 12), (size * 0.255, 9)):
        bbox = (cx - radius, cy - radius, cx + radius, cy + radius)
        for start in range(0, 360, dash_deg * 2):
            draw.arc(
                bbox,
                start=start,
                end=start + dash_deg,
                fill=_hex_color("#3B82F6", 64),
                width=max(4, round(size * 0.008)),
            )


def _draw_shadow(base: Image.Image, mask: Image.Image, blur: float, offset_y: float, opacity: int) -> None:
    shadow = Image.new("RGBA", base.size, (0, 0, 0, 0))
    shadow_alpha = mask.filter(ImageFilter.GaussianBlur(radius=blur))
    shadow_layer = Image.new("RGBA", base.size, (0, 0, 0, opacity))
    shadow_layer.putalpha(shadow_alpha)
    shadow.alpha_composite(shadow_layer, (0, round(offset_y)))
    base.alpha_composite(shadow)


def _line_points(p1: tuple[float, float], p2: tuple[float, float], width: float) -> list[tuple[float, float]]:
    x1, y1 = p1
    x2, y2 = p2
    dx = x2 - x1
    dy = y2 - y1
    length = (dx * dx + dy * dy) ** 0.5
    if length == 0:
        return [p1, p1, p2, p2]
    ux = dx / length
    uy = dy / length
    px = -uy * width / 2
    py = ux * width / 2
    return [
        (x1 + px, y1 + py),
        (x2 + px, y2 + py),
        (x2 - px, y2 - py),
        (x1 - px, y1 - py),
    ]


def build_icon(size: int = 1024) -> Image.Image:
    radius = round(size * 112 / 512)
    image = _diagonal_gradient(size, "#1E3A8A", "#0F172A", radius)
    draw = ImageDraw.Draw(image)

    _draw_background_rings(draw, size)

    path_points = [
        (size * 120 / 512, size * 440 / 512),
        (size * 216 / 512, size * 260 / 512),
        (size * 296 / 512, size * 260 / 512),
        (size * 392 / 512, size * 440 / 512),
    ]
    _vertical_gradient_polygon(image, path_points, "#10B981", "#3B82F6", 204, 25)
    draw.line(
        ((size * 256 / 512, size * 260 / 512), (size * 256 / 512, size * 440 / 512)),
        fill=_hex_color("#10B981", 153),
        width=max(8, round(size * 6 / 512)),
    )

    eye_mask = Image.new("L", (size, size), 0)
    eye_draw = ImageDraw.Draw(eye_mask)
    eye_box = (
        size * 112 / 512,
        size * 140 / 512,
        size * 400 / 512,
        size * 320 / 512,
    )
    eye_draw.ellipse(eye_box, fill=255)
    eye_box_inner = (
        size * 136 / 512,
        size * 164 / 512,
        size * 376 / 512,
        size * 296 / 512,
    )
    eye_draw.ellipse(eye_box_inner, fill=0)
    _draw_shadow(image, eye_mask, blur=size * 0.02, offset_y=size * 0.015, opacity=80)
    ImageDraw.Draw(image).ellipse(eye_box, outline="white", width=max(12, round(size * 16 / 512)))
    ImageDraw.Draw(image).ellipse(eye_box_inner, outline=None)

    iris_bbox = (
        size * 200 / 512,
        size * 174 / 512,
        size * 312 / 512,
        size * 286 / 512,
    )
    pupil_bbox = (
        size * 228 / 512,
        size * 202 / 512,
        size * 284 / 512,
        size * 258 / 512,
    )
    highlight_bbox = (
        size * 258 / 512,
        size * 212 / 512,
        size * 274 / 512,
        size * 228 / 512,
    )
    draw.ellipse(iris_bbox, outline=_hex_color("#3B82F6"), width=max(8, round(size * 10 / 512)))
    draw.ellipse(pupil_bbox, fill=_hex_color("#10B981"))
    draw.ellipse(highlight_bbox, fill=_hex_color("#FFFFFF", 204))

    guide_line = _line_points((size * 420 / 512, size * 110 / 512), (size * 310 / 512, size * 220 / 512), size * 14 / 512)
    guide_mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(guide_mask).polygon(guide_line, fill=255)
    _draw_shadow(image, guide_mask, blur=size * 0.02, offset_y=size * 0.015, opacity=70)
    draw.line(
        ((size * 420 / 512, size * 110 / 512), (size * 310 / 512, size * 220 / 512)),
        fill=_hex_color("#FFFFFF"),
        width=max(10, round(size * 14 / 512)),
    )
    draw.line(
        ((size * 420 / 512, size * 110 / 512), (size * 370 / 512, size * 160 / 512)),
        fill=_hex_color("#EF4444"),
        width=max(10, round(size * 14 / 512)),
    )

    return image


def build_window_icon(size: int = 512) -> Image.Image:
    radius = round(size * 96 / 512)
    image = _diagonal_gradient(size, "#23408E", "#15254E", radius)
    draw = ImageDraw.Draw(image)

    eye_mask = Image.new("L", (size, size), 0)
    eye_draw = ImageDraw.Draw(eye_mask)
    eye_box = (
        size * 72 / 512,
        size * 144 / 512,
        size * 440 / 512,
        size * 368 / 512,
    )
    eye_box_inner = (
        size * 102 / 512,
        size * 174 / 512,
        size * 410 / 512,
        size * 338 / 512,
    )
    eye_draw.ellipse(eye_box, fill=255)
    eye_draw.ellipse(eye_box_inner, fill=0)
    _draw_shadow(image, eye_mask, blur=size * 0.03, offset_y=size * 0.02, opacity=85)

    stroke = max(12, round(size * 18 / 512))
    draw.ellipse(eye_box, outline=_hex_color("#FFFFFF"), width=stroke)

    iris_box = (
        size * 182 / 512,
        size * 166 / 512,
        size * 330 / 512,
        size * 314 / 512,
    )
    pupil_box = (
        size * 220 / 512,
        size * 204 / 512,
        size * 292 / 512,
        size * 276 / 512,
    )
    highlight_box = (
        size * 260 / 512,
        size * 200 / 512,
        size * 286 / 512,
        size * 226 / 512,
    )
    draw.ellipse(iris_box, outline=_hex_color("#60A5FA"), width=max(10, round(size * 14 / 512)))
    draw.ellipse(pupil_box, fill=_hex_color("#10B981"))
    draw.ellipse(highlight_box, fill=_hex_color("#FFFFFF", 220))

    guide_start = (size * 408 / 512, size * 120 / 512)
    guide_end = (size * 314 / 512, size * 216 / 512)
    guide_width = max(12, round(size * 18 / 512))
    guide_mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(guide_mask).polygon(_line_points(guide_start, guide_end, guide_width), fill=255)
    _draw_shadow(image, guide_mask, blur=size * 0.018, offset_y=size * 0.014, opacity=76)
    draw.line((guide_start, guide_end), fill=_hex_color("#FFFFFF"), width=guide_width)
    draw.line((guide_start, (size * 364 / 512, size * 164 / 512)), fill=_hex_color("#EF4444"), width=guide_width)

    return image


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    icon = build_icon()
    icon.save(PNG_PATH)
    icon.save(
        ICO_PATH,
        sizes=[(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    window_icon = build_window_icon()
    window_icon.save(WINDOW_PNG_PATH)
    window_icon.save(
        WINDOW_ICO_PATH,
        sizes=[(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(f"Generated icon assets:\n- {PNG_PATH}\n- {ICO_PATH}")
    print(f"- {WINDOW_PNG_PATH}\n- {WINDOW_ICO_PATH}")


if __name__ == "__main__":
    main()
