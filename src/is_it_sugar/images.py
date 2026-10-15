"""Fit a photo into what TypeSafe accepts: an upright PNG/JPEG, 280-1568 px per side."""

import io

from PIL import Image, ImageOps

MAX_SIDE, MIN_SIDE = 1568, 280


class BadImage(ValueError):
    pass


def prepare(data: bytes) -> bytes:
    try:
        with Image.open(io.BytesIO(data)) as original:
            img = ImageOps.exif_transpose(original).convert("RGB")
    except Exception as e:
        raise BadImage("Couldn't open that image. Please upload a JPEG or PNG photo.") from e

    w, h = img.size
    scale = min(MAX_SIDE / max(w, h), max(1.0, MIN_SIDE / min(w, h)))
    if scale != 1:
        img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.Resampling.LANCZOS)
    # A long, thin crop of an ingredient list can still be too short after scaling; pad it.
    if min(img.size) < MIN_SIDE:
        padded = Image.new("RGB", (max(img.width, MIN_SIDE), max(img.height, MIN_SIDE)), "white")
        padded.paste(img, ((padded.width - img.width) // 2, (padded.height - img.height) // 2))
        img = padded

    out = io.BytesIO()
    img.save(out, "JPEG", quality=90)
    return out.getvalue()
