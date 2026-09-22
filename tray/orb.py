"""Naka's face, drawn with Pillow: a lit sphere in the colour of a state.

The same orb the panel draws on a canvas, rendered small — the tray icon and
the taskbar are where most people see Naka most of the time, and a flat disc
there would say nothing about what it is doing. packaging/assets.py draws the
executable's icon and the installer's artwork from here too, so all three
faces are the one face.

Colours are the panel's: grey while resting, violet when ready, and the same
red, amber and blue it uses for listening, thinking and speaking.
"""

from PIL import Image, ImageDraw, ImageFilter

# The orb at rest. A state's colour replaces the lit half; the shadow stays
# close to the background, which is what makes it read as a sphere.
VIOLET = (138, 108, 255)
SHADOW = (40, 30, 110)
HIGHLIGHT = (222, 216, 255)

STATES = {
    "starting": (74, 81, 98),
    "restarting": (74, 81, 98),
    "ready": VIOLET,
    "attached": VIOLET,
    "listening": (255, 107, 107),
    "thinking": (240, 136, 62),
    "speaking": (77, 159, 255),
    "failed": (150, 60, 60),
    "stopped": (60, 62, 72),
}


def _shade(colour: tuple[int, int, int], towards: tuple[int, int, int],
           amount: float) -> tuple[int, int, int]:
    return tuple(round(colour[i] + (towards[i] - colour[i]) * amount) for i in range(3))


def draw(size: int, colour: tuple[int, int, int] = VIOLET,
         background: tuple[int, int, int] | None = None) -> Image.Image:
    """A lit sphere, drawn four times over and scaled down for a smooth edge."""
    scale = 4
    side = size * scale
    image = Image.new("RGBA", (side, side),
                      (*background, 255) if background else (0, 0, 0, 0))
    sphere = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    pen = ImageDraw.Draw(sphere)

    radius = side * 0.46
    centre = side / 2
    dark = _shade(colour, SHADOW, 0.75)
    # Concentric circles inwards: a radial gradient without a shader.
    steps = int(radius)
    for step in range(steps, 0, -1):
        fraction = step / steps
        pen.ellipse(
            (centre - radius * fraction, centre - radius * fraction,
             centre + radius * fraction, centre + radius * fraction),
            fill=(*_shade(dark, colour, (1 - fraction) ** 0.65), 255))

    # The light, from the upper left, blurred and then clipped to the sphere:
    # unclipped it would fog whatever is behind the icon.
    light = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    spot = radius * 0.55
    at = (centre - radius * 0.3, centre - radius * 0.32)
    ImageDraw.Draw(light).ellipse(
        (at[0] - spot, at[1] - spot, at[0] + spot, at[1] + spot),
        fill=(*_shade(colour, HIGHLIGHT, 0.8), 225))
    light = light.filter(ImageFilter.GaussianBlur(spot * 0.55))
    sphere = Image.alpha_composite(sphere, light)

    mask = Image.new("L", (side, side), 0)
    ImageDraw.Draw(mask).ellipse(
        (centre - radius, centre - radius, centre + radius, centre + radius), fill=255)
    sphere.putalpha(Image.composite(sphere.getchannel("A"), mask, mask))

    return Image.alpha_composite(image, sphere).resize((size, size), Image.LANCZOS)


def for_state(state: str, size: int = 64) -> Image.Image:
    return draw(size, STATES.get(state, STATES["starting"]))
