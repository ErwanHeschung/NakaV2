"""Naka's logo, drawn for the tray and the installer from ui/public/naka.svg.

One source for every place the face appears: the panel serves the SVG as it
is, and this renders it to pixels for what cannot read SVG — the tray icon,
the executable's icon, the installer's artwork. resvg rather than a hand-made
drawing, so the gradients come out as they were designed.

The tray icon also says what Naka is doing. In colour she is there; in grey
she is not yet, or not any more (starting, stopped, failed); and a small dot
in the corner, in the state's colour, shows her listening, thinking or
speaking — what the orb's colour used to say on its own.
"""

from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

from server import paths

from .orb import STATES

SOURCE = paths.UI / "naka.svg"

# Not there, or not yet: the logo loses its colour.
GREY = {"starting", "restarting", "stopped", "failed"}
# Doing something: a dot in the state's colour.
DOTTED = {"listening", "thinking", "speaking", "failed"}


@lru_cache(maxsize=16)
def render(size: int) -> Image.Image:
    """The logo, square, with a transparent background."""
    import io

    import resvg_py

    png = resvg_py.svg_to_bytes(svg_path=str(SOURCE), width=size, height=size)
    return Image.open(io.BytesIO(bytes(png))).convert("RGBA")


def grey(image: Image.Image) -> Image.Image:
    """The same shape without its colour, alpha kept.

    Lifted rather than dimmed: the logo is mostly deep navy, and its plain
    greyscale all but vanished on a dark taskbar.
    """
    alpha = image.getchannel("A")
    shade = ImageOps.grayscale(image).point(lambda v: min(255, int(v * 1.6 + 30)))
    return Image.merge("RGBA", (shade, shade, shade, alpha))


def rim(image: Image.Image, width: float) -> Image.Image:
    """A faint light edge around the silhouette.

    For the tray only: a dark face on a dark taskbar needs something to
    stand out by, and a rim does it without recolouring the logo itself.
    """
    alpha = image.getchannel("A")
    grown = alpha.filter(ImageFilter.MaxFilter(max(3, int(width) * 2 + 1)))
    edge = ImageChops.subtract(grown, alpha).point(lambda v: v * 90 // 255)
    halo = Image.new("RGBA", image.size, (220, 214, 255, 0))
    halo.putalpha(edge)
    return Image.alpha_composite(halo, image)


def for_state(state: str, size: int = 64) -> Image.Image:
    # Drawn at four times the size and scaled down, so the dot's edge is as
    # smooth as the logo's.
    big = size * 4
    # The SVG is cropped to the face, so the rim needs a little room.
    inner = round(big * 0.94)
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    image.alpha_composite(render(inner), ((big - inner) // 2, (big - inner) // 2))
    if state in GREY:
        image = grey(image)
    image = rim(image, big / 32)
    if state in DOTTED:
        colour = STATES.get(state, STATES["starting"])
        # A dark ring around the dot keeps it apart from the logo and from
        # whatever colour the taskbar is.
        radius = big * 0.14
        ring = big * 0.045
        cx = cy = big - radius - ring
        pen = ImageDraw.Draw(image)
        pen.ellipse((cx - radius - ring, cy - radius - ring,
                     cx + radius + ring, cy + radius + ring), fill=(7, 8, 11, 255))
        pen.ellipse((cx - radius, cy - radius, cx + radius, cy + radius),
                    fill=(*colour, 255))
    return image.resize((size, size), Image.LANCZOS)
