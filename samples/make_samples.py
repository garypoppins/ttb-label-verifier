"""Generate the sample label images used for demos and tests.

Run from the repository root:

    python samples/make_samples.py

The images are drawn with Pillow using fonts that ship with the operating
system, so the exact pixels differ slightly between Windows, macOS and Linux.
The generated files are committed so tests do not depend on local fonts.
The warning text is written out here on purpose, separately from the app, so
the samples do not share code with the thing they test.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, replace
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT_DIR = Path(__file__).resolve().parent

WARNING_HEADER = "GOVERNMENT WARNING:"
WARNING_BODY = (
    "(1) According to the Surgeon General, women should not drink alcoholic "
    "beverages during pregnancy because of the risk of birth defects. "
    "(2) Consumption of alcoholic beverages impairs your ability to drive a "
    "car or operate machinery, and may cause health problems."
)
REWORDED_BODY = (
    "(1) According to the Surgeon General, women should not drink alcoholic "
    "beverages during pregnancy because of the risk of birth defects. "
    "(2) Consumption of alcoholic beverages impairs your ability to drive a "
    "car or operate machinery, and can cause health issues."
)

FONT_FILES = {
    "serif_bold": ["georgiab.ttf", "Georgia Bold.ttf", "DejaVuSerif-Bold.ttf", "LiberationSerif-Bold.ttf"],
    "serif_italic": ["georgiai.ttf", "Georgia Italic.ttf", "DejaVuSerif-Italic.ttf", "LiberationSerif-Italic.ttf"],
    "serif": ["georgia.ttf", "Georgia.ttf", "DejaVuSerif.ttf", "LiberationSerif-Regular.ttf"],
    "sans": ["arial.ttf", "Arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"],
    "sans_bold": ["arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"],
}
FONT_DIRS = [
    Path("C:/Windows/Fonts"),
    Path("/System/Library/Fonts/Supplemental"),
    Path("/Library/Fonts"),
    Path("/usr/share/fonts/truetype/dejavu"),
    Path("/usr/share/fonts/truetype/liberation"),
]


def font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    """Load the first available font file for a style."""
    for name in FONT_FILES[kind]:
        for folder in FONT_DIRS:
            path = folder / name
            if path.exists():
                return ImageFont.truetype(str(path), size)
    raise FileNotFoundError(f"No font found for style '{kind}'. Looked for {FONT_FILES[kind]}.")


@dataclass(frozen=True)
class Label:
    """Everything printed on one sample label."""

    brand_lines: tuple[str, ...]
    class_type: str
    alcohol: str
    net_contents: str
    bottler: str
    tagline: str = "EST. 1892"
    warning_header: str | None = WARNING_HEADER
    warning_body: str = WARNING_BODY
    header_bold: bool = True
    paper: tuple[int, int, int] = (246, 239, 222)
    ink: tuple[int, int, int] = (40, 26, 14)
    accent: tuple[int, int, int] = (128, 32, 24)
    brand_size: int = 96


WIDTH, HEIGHT, MARGIN = 1000, 1400, 80


def draw_centered(draw: ImageDraw.ImageDraw, y: int, text: str, face: ImageFont.FreeTypeFont, fill: tuple) -> int:
    """Draw one centered line and return the y position below it."""
    width = draw.textlength(text, font=face)
    draw.text(((WIDTH - width) / 2, y), text, font=face, fill=fill)
    return y + int(face.size * 1.25)


def draw_warning(draw: ImageDraw.ImageDraw, label: Label, y: int) -> None:
    """Draw the warning paragraph with word wrap and an optional bold header."""
    size = 25
    regular, bold = font("sans", size), font("sans_bold", size)
    header_face = bold if label.header_bold else regular
    words = [(word, header_face) for word in (label.warning_header or "").split()]
    words += [(word, regular) for word in label.warning_body.split()]
    space = draw.textlength(" ", font=regular)
    x = MARGIN
    for word, face in words:
        width = draw.textlength(word, font=face)
        if x + width > WIDTH - MARGIN:
            x, y = MARGIN, y + int(size * 1.35)
        draw.text((x, y), word, font=face, fill=label.ink)
        x += width + space


def render(label: Label) -> Image.Image:
    """Draw a complete label."""
    image = Image.new("RGB", (WIDTH, HEIGHT), label.paper)
    draw = ImageDraw.Draw(image)
    draw.rectangle([30, 30, WIDTH - 30, HEIGHT - 30], outline=label.accent, width=6)
    draw.rectangle([46, 46, WIDTH - 46, HEIGHT - 46], outline=label.ink, width=2)

    y = 120
    y = draw_centered(draw, y, label.tagline, font("serif", 30), label.accent) + 40
    for line in label.brand_lines:
        y = draw_centered(draw, y, line, font("serif_bold", label.brand_size), label.ink)
    y += 30
    draw.line([(WIDTH // 2 - 200, y), (WIDTH // 2 + 200, y)], fill=label.accent, width=4)
    y += 50
    y = draw_centered(draw, y, label.class_type, font("serif_italic", 46), label.ink) + 60
    y = draw_centered(draw, y, label.alcohol, font("serif", 44), label.ink) + 10
    y = draw_centered(draw, y, label.net_contents, font("serif", 44), label.ink) + 70
    draw_centered(draw, y, label.bottler, font("serif", 26), label.ink)

    if label.warning_header is not None:
        draw_warning(draw, label, HEIGHT - 300)
    return image


def render_back(label: Label) -> Image.Image:
    """Draw a back label: bottler, origin and the warning, with no brand, class, alcohol or net contents."""
    image = Image.new("RGB", (WIDTH, 700), label.paper)
    draw = ImageDraw.Draw(image)
    draw.rectangle([30, 30, WIDTH - 30, 700 - 30], outline=label.accent, width=6)
    y = draw_centered(draw, 110, label.bottler, font("serif", 26), label.ink) + 10
    draw_centered(draw, y, "PRODUCT OF USA", font("serif", 26), label.ink)
    draw_warning(draw, label, 330)
    return image


def tilt(image: Image.Image, degrees: float) -> Image.Image:
    """Rotate slightly on a dark table-like background, as a hand-held photo would be."""
    return image.rotate(degrees, expand=True, resample=Image.Resampling.BICUBIC, fillcolor=(58, 52, 48))


def wash_out(image: Image.Image) -> Image.Image:
    """Simulate a dim, flat, slightly soft photo with sensor noise."""
    grey = Image.new("RGB", image.size, (150, 146, 140))
    flat = Image.blend(image, grey, 0.72).filter(ImageFilter.GaussianBlur(0.8))
    rng = random.Random(7)
    noise = Image.frombytes("L", flat.size, bytes(rng.randrange(0, 256) for _ in range(flat.width * flat.height)))
    noise_rgb = Image.merge("RGB", (noise, noise, noise))
    return Image.blend(flat, noise_rgb, 0.06)


OLD_TOM = Label(
    brand_lines=("OLD TOM", "DISTILLERY"),
    class_type="Kentucky Straight Bourbon Whiskey",
    alcohol="45% Alc./Vol. (90 Proof)",
    net_contents="750 mL",
    bottler="Distilled and Bottled by Old Tom Distillery, Bardstown, Kentucky",
)
STONES_THROW = replace(
    OLD_TOM,
    brand_lines=("STONE'S THROW",),
    class_type="Straight Rye Whiskey",
    alcohol="50% Alc./Vol. (100 Proof)",
    bottler="Distilled and Bottled by Stone's Throw Spirits, Portland, Oregon",
    tagline="SMALL BATCH",
)
WINE = Label(
    brand_lines=("CHATEAU", "MERIDIAN"),
    class_type="Napa Valley Cabernet Sauvignon",
    alcohol="13.5% Alc. by Vol.",
    net_contents="750 mL",
    bottler="Produced and Bottled by Meridian Cellars, Napa, California",
    tagline="VINTAGE 2021",
    paper=(250, 248, 242),
    ink=(30, 24, 28),
    accent=(110, 20, 48),
)
BEER = Label(
    brand_lines=("HOP HARBOR",),
    class_type="India Pale Ale",
    alcohol="6.2% Alc./Vol.",
    net_contents="12 FL OZ",
    bottler="Brewed and Canned by Hop Harbor Brewing Co., San Diego, California",
    tagline="CRAFT BREWED",
    paper=(22, 58, 44),
    ink=(244, 236, 210),
    accent=(214, 170, 60),
)


def build_all() -> dict[str, Image.Image]:
    """Return every sample image keyed by file name."""
    correct = render(OLD_TOM)
    return {
        "label_correct.png": correct,
        "label_wrong_abv.png": render(replace(OLD_TOM, alcohol="40% Alc./Vol. (80 Proof)")),
        "label_titlecase_warning.png": render(replace(OLD_TOM, warning_header="Government Warning:")),
        "label_reworded_warning.png": render(replace(OLD_TOM, warning_body=REWORDED_BODY)),
        "label_missing_warning.png": render(replace(OLD_TOM, warning_header=None)),
        "label_warning_not_bold.png": render(replace(OLD_TOM, header_bold=False)),
        "label_brand_case.png": render(STONES_THROW),
        "label_rotated.jpg": correct.transpose(Image.Transpose.ROTATE_270),
        "label_tilted.jpg": tilt(correct, 7),
        "label_low_contrast.jpg": wash_out(correct),
        "label_wine.png": render(WINE),
        "label_beer.png": render(BEER),
        # A front and back pair for one product, to be checked together.
        "label_front.png": render(replace(OLD_TOM, warning_header=None)),
        "label_back.png": render_back(OLD_TOM),
    }


def main() -> None:
    for name, image in build_all().items():
        options = {"quality": 88} if name.endswith(".jpg") else {"optimize": True}
        image.save(OUT_DIR / name, **options)
        print(f"wrote {name} ({image.width}x{image.height})")


if __name__ == "__main__":
    main()
