"""
Generate PWA icons for Aegis AI.
Creates icon-192.png and icon-512.png in ui/static/.
Requires: pip install Pillow
"""

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


PROJECT_ROOT = Path(__file__).parent.parent
STATIC_DIR = PROJECT_ROOT / "ui" / "static"


def generate_icon(size: int) -> Image.Image:
    """Generate a simple shield/A logo icon at the given size."""
    img = Image.new("RGBA", (size, size), (15, 23, 42, 255))  # slate-900 bg
    draw = ImageDraw.Draw(img)

    cx, cy = size // 2, size // 2
    margin = size * 0.12

    # Shield shape
    shield_top = margin
    shield_bottom = size - margin
    shield_left = margin * 1.5
    shield_right = size - margin * 1.5
    shield_mid_y = cy + size * 0.05

    shield_points = [
        (cx, shield_top),                    # top center
        (shield_right, shield_top + size * 0.08),  # top right
        (shield_right, shield_mid_y),        # mid right
        (cx, shield_bottom),                 # bottom center (point)
        (shield_left, shield_mid_y),         # mid left
        (shield_left, shield_top + size * 0.08),   # top left
    ]
    draw.polygon(shield_points, fill=(37, 99, 235, 255))  # blue-600

    # Inner shield (darker)
    inset = size * 0.06
    inner_points = [
        (cx, shield_top + inset),
        (shield_right - inset, shield_top + size * 0.08 + inset * 0.5),
        (shield_right - inset, shield_mid_y - inset * 0.3),
        (cx, shield_bottom - inset * 1.5),
        (shield_left + inset, shield_mid_y - inset * 0.3),
        (shield_left + inset, shield_top + size * 0.08 + inset * 0.5),
    ]
    draw.polygon(inner_points, fill=(30, 64, 175, 255))  # blue-800

    # "A" letter in the center
    font_size = int(size * 0.38)
    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except (OSError, IOError):
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
        except (OSError, IOError):
            font = ImageFont.load_default()

    text = "A"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = cx - tw // 2
    ty = cy - th // 2 - size * 0.02

    draw.text((tx, ty), text, fill=(147, 197, 253, 255), font=font)  # blue-300

    return img


def main():
    STATIC_DIR.mkdir(parents=True, exist_ok=True)

    for size in (192, 512):
        icon = generate_icon(size)
        path = STATIC_DIR / f"icon-{size}.png"
        icon.save(str(path), "PNG")
        print(f"Generated {path}")


if __name__ == "__main__":
    main()
