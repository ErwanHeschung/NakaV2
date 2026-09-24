"""Draw Naka's icon and the installer's artwork, from her logo.

Generated from ui/public/naka.svg rather than checked in as images: the SVG
is the one source for the face, and a binary nobody can diff is a poor place
to keep a copy of it.

    python packaging\\assets.py build\\wizard

Writes naka.ico (the executable, the taskbar and the installer), side.bmp and
small.bmp (Inno Setup's own window — BMP, because that is all it reads).
"""

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tray import logo, orb  # noqa: E402  — after the path is set up

BACKGROUND = (7, 8, 11)
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def glow(image: Image.Image, at: tuple[int, int], radius: int,
         colour: tuple[int, int, int], strength: int) -> None:
    """The ambient wash behind the panel, in the installer's window."""
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).ellipse(
        (at[0] - radius, at[1] - radius, at[0] + radius, at[1] + radius),
        fill=(*colour, strength))
    layer = layer.filter(ImageFilter.GaussianBlur(radius * 0.6))
    image.alpha_composite(layer)


def banner(width: int, height: int, logo_size: int) -> Image.Image:
    image = Image.new("RGBA", (width, height), (*BACKGROUND, 255))
    glow(image, (int(width * 0.2), int(height * 0.18)), int(height * 0.45), (86, 62, 190), 150)
    glow(image, (int(width * 0.9), int(height * 0.85)), int(height * 0.4), (24, 60, 120), 110)
    at = (round((width - logo_size) / 2), round(height * 0.34 - logo_size / 2))
    # A little light behind the face, as the orb throws in the panel.
    glow(image, (at[0] + logo_size // 2, at[1] + logo_size // 2),
         int(logo_size * 0.8), orb.VIOLET, 70)
    image.alpha_composite(logo.render(logo_size), at)
    return image


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    logo.render(256).save(out / "naka.ico", sizes=[(s, s) for s in ICON_SIZES])
    # Inno Setup's sizes at 100%; it scales them itself on a larger display.
    banner(164, 314, 104).convert("RGB").save(out / "side.bmp")
    small = Image.new("RGBA", (55, 55), (*BACKGROUND, 255))
    small.alpha_composite(logo.render(47), (4, 4))
    small.convert("RGB").save(out / "small.bmp")
    print(f"wrote naka.ico, side.bmp and small.bmp to {out}")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "build/wizard"))
