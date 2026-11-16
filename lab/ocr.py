"""Small, transient images processed by the installed Tesseract executable."""

import io
import secrets
import shutil
import subprocess

from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

MAX_BYTES = 2 * 1024 * 1024
MAX_PIXELS = 2_000_000
FORMATS = ("PNG", "JPEG", "WEBP")
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class InvalidImage(ValueError):
    pass


class OcrUnavailable(RuntimeError):
    pass


def sample_image():
    label = "".join(secrets.choice(ALPHABET) for _ in range(6))
    image = Image.new("RGB", (380, 116), "#f7f9ef")
    draw = ImageDraw.Draw(image)
    draw.text((190, 58), label, font=ImageFont.load_default(size=57), fill="#15251e", anchor="mm")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def normalize(raw: bytes):
    try:
        with Image.open(io.BytesIO(raw), formats=FORMATS) as image:
            if image.width * image.height > MAX_PIXELS or getattr(image, "n_frames", 1) != 1:
                raise InvalidImage("Use a still image with at most 2 million pixels.")
            image = ImageOps.exif_transpose(image).convert("RGBA")
            background = Image.new("RGBA", image.size, "white")
            background.alpha_composite(image)
            image = ImageOps.autocontrast(background.convert("L"))
            image = ImageOps.expand(image, border=12, fill=255)
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            return buffer.getvalue()
    except (OSError, SyntaxError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise InvalidImage("Use a valid PNG, JPEG or WebP image.") from exc


def recognize(raw: bytes):
    image = normalize(raw)
    executable = shutil.which("tesseract")
    if executable is None:
        raise OcrUnavailable("Tesseract is missing. Install it or run the Docker image.")
    try:
        result = subprocess.run(
            [executable, "stdin", "stdout", "-l", "eng", "--psm", "7"],
            input=image, capture_output=True, check=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise OcrUnavailable("Tesseract failed or exceeded its five-second limit.") from exc
    return result.stdout.decode("utf-8", errors="replace").strip()
