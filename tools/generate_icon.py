from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


GOLD = (232, 181, 50)  # #E8B532 - the StickyOmelet brand yellow
WHITE = "#FFFFFF"
BUBBLE_BORDER = "#D5DBE5"

ASSETS = Path(__file__).resolve().parents[1] / "assets"

# The mark is a single filled gold dot. Everything is drawn supersampled and
# downscaled with LANCZOS so the circle keeps a clean antialiased edge at the
# 16 px taskbar size.
_SUPERSAMPLE = 16
_DOT_SCALE = 0.70
_BUBBLE_DOT_SCALE = 0.42


def make_dot(size: int, *, padding: float = 0.08) -> Image.Image:
    """Rasterize the gold dot into a transparent square image."""
    work_size = size * _SUPERSAMPLE
    image = Image.new("RGBA", (work_size, work_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    diameter = work_size * (1 - padding * 2) * _DOT_SCALE
    offset = (work_size - diameter) / 2
    draw.ellipse((offset, offset, offset + diameter, offset + diameter), fill=GOLD)

    return image.resize((size, size), Image.Resampling.LANCZOS)


def make_bubble(size: int = 64) -> Image.Image:
    """Render the white circular launcher with the gold dot centered inside."""
    supersample = 8
    work_size = size * supersample
    image = Image.new("RGBA", (work_size, work_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    border_width = max(1, round(size * 0.025)) * supersample
    inset = border_width // 2 + supersample
    draw.ellipse(
        (inset, inset, work_size - inset - 1, work_size - inset - 1),
        fill=WHITE,
        outline=BUBBLE_BORDER,
        width=border_width,
    )
    diameter = work_size * _BUBBLE_DOT_SCALE
    offset = (work_size - diameter) / 2
    draw.ellipse((offset, offset, offset + diameter, offset + diameter), fill=GOLD)
    return image.resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    sizes = [(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64), (128, 128), (256, 256)]
    make_dot(256, padding=0.05).save(ASSETS / "stickyomelet.ico", format="ICO", sizes=sizes)
    make_dot(22, padding=0.02).save(ASSETS / "dot-mark.png", format="PNG")
    make_bubble().save(ASSETS / "dot-bubble.png", format="PNG")


if __name__ == "__main__":
    main()
