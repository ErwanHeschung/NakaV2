"""Draw Naka's icon and the installer's artwork, from the orb in the panel.

Generated rather than checked in: it is one sphere and a background, the
colours belong with the rest of the theme, and a binary nobody can diff is a
poor place to keep either.

    python packaging\\assets.py build\\wizard

Writes naka.ico (the executable, the taskbar and the installer), side.bmp and
small.bmp (Inno Setup's own window — BMP, because that is all it reads).
"""

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

BACKGROUND = (7, 8, 11)
# The orb's own colours, from ui/src/orb.ts: lit from the upper left, violet
# through to a deep blue in the shadow.
LIGHT = (222, 216, 255)
MID = (138, 108, 255)
DARK = (40, 30, 110)
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def orb(size: int, background: tuple[int, int, int] | None = None) -> Image.Image:
    """A lit sphere, drawn large and scaled down so its edge stays smooth."""
    scale = 4
    side = size * scale
    image = Image.new("RGBA", (side, side), (*background, 255) if background else (0, 0, 0, 0))
    sphere = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    draw = ImageDraw.Draw(sphere)

    radius = side * 0.46
    centre = side / 2
    # Concentric circles from the shadow inwards to the highlight: a cheap
    # radial gradient, offset so the light comes from one side.
    steps = int(radius)
    for step in range(steps, 0, -1):
        fraction = step / steps
        colour = tuple(
            round(DARK[i] + (MID[i] - DARK[i]) * (1 - fraction) ** 0.65)
            for i in range(3)
        )
        draw.ellipse(
            (centre - radius * fraction, centre - radius * fraction,
             centre + radius * fraction, centre + radius * fraction),
            fill=(*colour, 255))

    highlight = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    spot = radius * 0.55
    at = (centre - radius * 0.3, centre - radius * 0.32)
    ImageDraw.Draw(highlight).ellipse(
        (at[0] - spot, at[1] - spot, at[0] + spot, at[1] + spot),
        fill=(*LIGHT, 225))
    highlight = highlight.filter(ImageFilter.GaussianBlur(spot * 0.55))
    sphere = Image.alpha_composite(sphere, highlight)

    # Only inside the sphere: the blurred highlight would otherwise leak past
    # its edge and fog the background.
    mask = Image.new("L", (side, side), 0)
    ImageDraw.Draw(mask).ellipse(
        (centre - radius, centre - radius, centre + radius, centre + radius), fill=255)
    sphere.putalpha(Image.composite(sphere.getchannel("A"), mask, mask))

    image = Image.alpha_composite(image, sphere)
    return image.resize((size, size), Image.LANCZOS)


def glow(image: Image.Image, at: tuple[int, int], radius: int,
         colour: tuple[int, int, int], strength: int) -> None:
    """The ambient wash behind the panel, in the installer's window."""
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).ellipse(
        (at[0] - radius, at[1] - radius, at[0] + radius, at[1] + radius),
        fill=(*colour, strength))
    layer = layer.filter(ImageFilter.GaussianBlur(radius * 0.6))
    image.alpha_composite(layer)


def banner(width: int, height: int, orb_size: int) -> Image.Image:
    image = Image.new("RGBA", (width, height), (*BACKGROUND, 255))
    glow(image, (int(width * 0.2), int(height * 0.18)), int(height * 0.45), (86, 62, 190), 150)
    glow(image, (int(width * 0.9), int(height * 0.85)), int(height * 0.4), (24, 60, 120), 110)
    at = (round((width - orb_size) / 2), round(height * 0.34 - orb_size / 2))
    # The orb throws a little light of its own, as it does in the panel.
    glow(image, (at[0] + orb_size // 2, at[1] + orb_size // 2),
         int(orb_size * 0.8), MID, 70)
    image.alpha_composite(orb(orb_size), at)
    return image


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    orb(256).save(out / "naka.ico", sizes=[(s, s) for s in ICON_SIZES])
    # Inno Setup's sizes at 100%; it scales them itself on a larger display.
    banner(164, 314, 96).convert("RGB").save(out / "side.bmp")
    small = Image.new("RGBA", (55, 55), (*BACKGROUND, 255))
    small.alpha_composite(orb(44), (6, 6))
    small.convert("RGB").save(out / "small.bmp")
    print(f"wrote naka.ico, side.bmp and small.bmp to {out}")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "build/wizard"))
