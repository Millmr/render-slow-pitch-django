"""Validate and shrink uploaded team logos."""

from io import BytesIO

from django.core.exceptions import ValidationError
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_SIDE = 256
ALLOWED_FORMATS = {"PNG", "JPEG", "WEBP", "GIF"}


def process_logo(upload) -> bytes:
    """Return the upload as a PNG no larger than MAX_SIDE on either side.

    Raises ValidationError for anything that isn't a reasonable image.
    """
    if upload.size > MAX_UPLOAD_BYTES:
        raise ValidationError("That image is over 5 MB. Try a smaller file.")
    try:
        image = Image.open(upload)
        image.verify()  # catches truncated or corrupt files
        upload.seek(0)
        image = Image.open(upload)
        if image.format not in ALLOWED_FORMATS:
            raise ValidationError("Upload a PNG, JPEG, WebP or GIF image.")
        if image.width * image.height > 40_000_000:
            raise ValidationError("That image is too large.")
        image.seek(0)  # first frame of an animated GIF
        image = ImageOps.exif_transpose(image)  # respect phone photo rotation
        image = image.convert("RGBA")
        image.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
    except ValidationError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise ValidationError("That file isn't an image we can read. Upload a PNG, JPEG, WebP or GIF.")

    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()
