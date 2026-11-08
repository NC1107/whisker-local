from __future__ import annotations

import io
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_EDGE_PX = 1600

# Decoding holds the whole bitmap in memory, and the container has 512 MB. 36 megapixels covers
# any phone camera, and a JPEG shrinks on load so its pixel count is what matters.
MAX_PIXELS = 36_000_000

# Other formats decode at full size and the pipeline briefly holds a few copies of the bitmap, so
# the budget counts bytes (pixels times bands) and leaves room for those copies.
MAX_DECODED_BYTES = 64 * 1024 * 1024

# Decoding happens on this one thread. A lock around a thread pool would not bound memory: every
# pool thread gets its own malloc arena, and freed bitmaps stay in the arena that made them.
decoder = ThreadPoolExecutor(max_workers=1, thread_name_prefix="photo-decode")

JPEG_QUALITY = 85
_ACCEPTED = ("JPEG", "PNG", "WEBP", "GIF")


class NotAnImage(Exception):
    """The bytes are not a picture this server accepts."""


class ImageTooLarge(Exception):
    """The picture is small as a file but would take too much memory to open."""


def normalise(data: bytes) -> bytes:
    """A metadata-free JPEG no larger than ``MAX_EDGE_PX``, made from any accepted image.

    Metadata is dropped on purpose: phone photos carry the place they were taken.
    """
    try:
        image = Image.open(io.BytesIO(data), formats=_ACCEPTED)
    except Image.DecompressionBombError as err:
        raise ImageTooLarge from err
    except (UnidentifiedImageError, OSError, ValueError) as err:
        raise NotAnImage from err
    try:
        width, height = image.size
        if getattr(image, "n_frames", 1) > 1:
            raise NotAnImage
        if image.format == "JPEG":
            if width * height > MAX_PIXELS:
                raise ImageTooLarge
        elif width * height * len(image.getbands()) > MAX_DECODED_BYTES:
            raise ImageTooLarge
        image.draft("RGB", (MAX_EDGE_PX, MAX_EDGE_PX))
        # Shrinking first keeps the orientation fix from copying a full-size bitmap.
        image.thumbnail((MAX_EDGE_PX, MAX_EDGE_PX))
        # Applying the orientation is what makes it safe to drop the tag that carried it.
        ImageOps.exif_transpose(image, in_place=True)
        flat = _on_white(image)
        # JPEG and GIF comments are free text that a camera or editor may have left behind.
        flat.info.clear()
        out = io.BytesIO()
        flat.save(out, "JPEG", quality=JPEG_QUALITY, optimize=True)
    except Image.DecompressionBombError as err:
        raise ImageTooLarge from err
    except (OSError, SyntaxError, ValueError) as err:
        # A truncated or corrupt file fails here, after the header looked fine.
        raise NotAnImage from err
    return out.getvalue()


def _on_white(image: Image.Image) -> Image.Image:
    if image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info:
        rgba = image.convert("RGBA")
        canvas = Image.new("RGB", rgba.size, "white")
        canvas.paste(rgba, mask=rgba.getchannel("A"))
        return canvas
    return image.convert("RGB")


class CatPhotos:
    """One JPEG per cat, in a folder next to the database and named only from the cat's id."""

    def __init__(self, folder: Path) -> None:
        self._folder = folder

    def _path(self, cat_id: int) -> Path:
        return self._folder / f"{int(cat_id)}.jpg"

    def path(self, cat_id: int) -> Path | None:
        path = self._path(cat_id)
        return path if path.is_file() else None

    def version(self, cat_id: int) -> int | None:
        """Changes whenever the picture does, so a client can tell when to fetch it again."""
        path = self.path(cat_id)
        return None if path is None else path.stat().st_mtime_ns // 1_000_000

    def save(self, cat_id: int, data: bytes) -> None:
        self._folder.mkdir(parents=True, exist_ok=True)
        # Written beside the target and renamed, so a reader never sees half a picture.
        descriptor, temporary = tempfile.mkstemp(dir=self._folder, suffix=".part")
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
            Path(temporary).replace(self._path(cat_id))
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise

    def delete(self, cat_id: int) -> None:
        self._path(cat_id).unlink(missing_ok=True)
