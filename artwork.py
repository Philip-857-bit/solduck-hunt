"""Deterministic personalized artwork rendering for Telegram winners."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
import unicodedata

from PIL import Image, ImageDraw, ImageFont

ASSET_DIRECTORY = Path(__file__).resolve().parent / "assets"
WINNER_TEMPLATE = ASSET_DIRECTORY / "winner-template.png"
WINNER_FONT = ASSET_DIRECTORY / "DejaVuSans-Bold.ttf"

# Safe interior of the empty purple name plaque in the 1254x1254 template.
NAME_BOX = (62, 298, 650, 383)
MAX_FONT_SIZE = 58
MIN_FONT_SIZE = 24
MAX_NAME_CHARACTERS = 64
TEXT_FILL = "#FFD83D"
TEXT_STROKE = "#19062F"


def winner_label(display_name: str) -> str:
    """Normalize Telegram-provided names to one safe line for the artwork."""
    normalized = unicodedata.normalize("NFKC", display_name or "")
    normalized = "".join(
        " "
        if character.isspace()
        else character
        if not unicodedata.category(character).startswith("C")
        else ""
        for character in normalized
    )
    normalized = " ".join(normalized.split())[:MAX_NAME_CHARACTERS].strip()
    return f"WINNER: {normalized or 'SolDuck Player'}"


def _fit_text(draw: ImageDraw.ImageDraw, text: str) -> tuple[str, ImageFont.FreeTypeFont]:
    max_width = NAME_BOX[2] - NAME_BOX[0]
    max_height = NAME_BOX[3] - NAME_BOX[1]
    for size in range(MAX_FONT_SIZE, MIN_FONT_SIZE - 1, -2):
        font = ImageFont.truetype(WINNER_FONT, size=size)
        bounds = draw.textbbox((0, 0), text, font=font, stroke_width=3)
        if bounds[2] - bounds[0] <= max_width and bounds[3] - bounds[1] <= max_height:
            return text, font

    font = ImageFont.truetype(WINNER_FONT, size=MIN_FONT_SIZE)
    shortened = text
    while len(shortened) > 1:
        candidate = shortened.rstrip() + "…"
        bounds = draw.textbbox((0, 0), candidate, font=font, stroke_width=3)
        if bounds[2] - bounds[0] <= max_width:
            return candidate, font
        shortened = shortened[:-1]
    return "WINNER", font


def render_winner_image(display_name: str) -> BytesIO:
    """Render a personalized JPEG suitable for Telegram's InputMediaPhoto."""
    with Image.open(WINNER_TEMPLATE) as template:
        image = template.convert("RGB")
    draw = ImageDraw.Draw(image)
    text, font = _fit_text(draw, winner_label(display_name))
    bounds = draw.textbbox((0, 0), text, font=font, stroke_width=3)
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    x = NAME_BOX[0] + ((NAME_BOX[2] - NAME_BOX[0] - width) / 2) - bounds[0]
    y = NAME_BOX[1] + ((NAME_BOX[3] - NAME_BOX[1] - height) / 2) - bounds[1]
    draw.text(
        (x, y),
        text,
        font=font,
        fill=TEXT_FILL,
        stroke_width=3,
        stroke_fill=TEXT_STROKE,
    )

    output = BytesIO()
    image.save(output, format="JPEG", quality=94, optimize=True)
    output.seek(0)
    return output
