"""Normalize captured screenshots before they are sent to the model."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

from .capture import ScreenCapture

logger = logging.getLogger(__name__)

_DEFAULT_MAX_DIMENSION = 1920
_DEFAULT_JPEG_QUALITY = 85


def get_resize_settings() -> Tuple[int, int]:
    """Return (max_dimension, jpeg_quality) from config."""
    max_dim = _DEFAULT_MAX_DIMENSION
    quality = _DEFAULT_JPEG_QUALITY
    try:
        from hermes_cli.config import load_config

        block = load_config().get("screen_awareness", {})
        if isinstance(block, dict):
            if block.get("max_dimension") is not None:
                max_dim = int(block["max_dimension"])
            if block.get("jpeg_quality") is not None:
                quality = int(block["jpeg_quality"])
    except Exception:
        pass
    max_dim = max(320, min(max_dim, 8192))
    quality = max(40, min(quality, 95))
    return max_dim, quality


def _image_dimensions(path: Path) -> Optional[Tuple[int, int]]:
    try:
        from PIL import Image

        with Image.open(path) as img:
            return img.size
    except ImportError:
        pass
    except Exception as exc:
        logger.debug("screen-awareness: PIL dimension read failed for %s: %s", path, exc)

    magick = shutil.which("magick") or shutil.which("convert")
    if not magick:
        return None
    try:
        proc = subprocess.run(
            [magick, "identify", "-format", "%w %h", str(path)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if proc.returncode != 0:
            return None
        parts = proc.stdout.strip().split()
        if len(parts) != 2:
            return None
        return int(parts[0]), int(parts[1])
    except Exception as exc:
        logger.debug("screen-awareness: identify failed for %s: %s", path, exc)
        return None


def _convert_to_jpeg_pillow(src: Path, dest: Path, *, jpeg_quality: int) -> bool:
    try:
        from PIL import Image
    except ImportError:
        return False

    try:
        with Image.open(src) as img:
            img.load()
            if img.mode in {"RGBA", "P"}:
                img = img.convert("RGB")
            elif img.mode != "RGB":
                img = img.convert("RGB")
            dest.parent.mkdir(parents=True, exist_ok=True)
            img.save(dest, format="JPEG", quality=jpeg_quality, optimize=True)
        return dest.is_file() and dest.stat().st_size > 0
    except Exception as exc:
        logger.debug("screen-awareness: Pillow JPEG convert failed for %s: %s", src, exc)
        return False


def _convert_to_jpeg_magick(src: Path, dest: Path, *, jpeg_quality: int) -> bool:
    magick = shutil.which("magick") or shutil.which("convert")
    if not magick:
        return False

    cmd = [
        magick,
        str(src),
        "-auto-orient",
        "-quality",
        str(jpeg_quality),
        str(dest),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
        if proc.returncode != 0:
            return False
        return dest.is_file() and dest.stat().st_size > 0
    except Exception as exc:
        logger.debug("screen-awareness: magick JPEG convert error for %s: %s", src, exc)
        return False


def _resize_with_pillow(
    src: Path,
    dest: Path,
    *,
    max_dimension: int,
    jpeg_quality: int,
) -> bool:
    try:
        from PIL import Image
    except ImportError:
        return False

    try:
        with Image.open(src) as img:
            img.load()
            if img.mode in {"RGBA", "P"}:
                img = img.convert("RGB")
            elif img.mode != "RGB":
                img = img.convert("RGB")

            width, height = img.size
            long_edge = max(width, height)
            if long_edge > max_dimension:
                scale = max_dimension / long_edge
                new_size = (
                    max(1, int(width * scale)),
                    max(1, int(height * scale)),
                )
                resample = getattr(Image, "Resampling", Image).LANCZOS
                img = img.resize(new_size, resample)

            dest.parent.mkdir(parents=True, exist_ok=True)
            img.save(dest, format="JPEG", quality=jpeg_quality, optimize=True)
        return dest.is_file() and dest.stat().st_size > 0
    except Exception as exc:
        logger.debug("screen-awareness: Pillow resize failed for %s: %s", src, exc)
        return False


def _resize_with_magick(
    src: Path,
    dest: Path,
    *,
    max_dimension: int,
    jpeg_quality: int,
) -> bool:
    magick = shutil.which("magick") or shutil.which("convert")
    if not magick:
        return False

    resize_arg = f"{max_dimension}x{max_dimension}>"
    cmd = [
        magick,
        str(src),
        "-auto-orient",
        "-resize",
        resize_arg,
        "-quality",
        str(jpeg_quality),
        str(dest),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
        if proc.returncode != 0:
            logger.debug(
                "screen-awareness: magick resize failed for %s: %s",
                src,
                proc.stderr.strip() or proc.stdout.strip(),
            )
            return False
        return dest.is_file() and dest.stat().st_size > 0
    except Exception as exc:
        logger.debug("screen-awareness: magick resize error for %s: %s", src, exc)
        return False


def _resize_capture(
    shot: ScreenCapture,
    *,
    max_dimension: int,
    jpeg_quality: int,
) -> ScreenCapture:
    src = shot.path
    if not src.is_file():
        return shot

    dims = _image_dimensions(src)
    if dims is None:
        logger.warning("screen-awareness: could not read dimensions for %s", src)
        return shot

    width, height = dims
    long_edge = max(width, height)
    needs_resize = long_edge > max_dimension
    is_jpeg = src.suffix.lower() in {".jpg", ".jpeg"}

    if not needs_resize and is_jpeg:
        return shot

    dest = src.with_name(f"{src.stem}_ai.jpg")
    if dest == src:
        dest = src.with_suffix(".jpg")

    if needs_resize:
        resized = _resize_with_pillow(
            src,
            dest,
            max_dimension=max_dimension,
            jpeg_quality=jpeg_quality,
        )
        if not resized:
            resized = _resize_with_magick(
                src,
                dest,
                max_dimension=max_dimension,
                jpeg_quality=jpeg_quality,
            )
    else:
        resized = _convert_to_jpeg_pillow(src, dest, jpeg_quality=jpeg_quality)
        if not resized:
            resized = _convert_to_jpeg_magick(src, dest, jpeg_quality=jpeg_quality)

    if not resized:
        logger.warning("screen-awareness: resize failed for %s — using original", src)
        return shot

    if dest != src and src.exists():
        try:
            src.unlink()
        except OSError:
            pass

    new_dims = _image_dimensions(dest)
    if new_dims:
        logger.debug(
            "screen-awareness: resized %s (%dx%d) -> %s (%dx%d)",
            shot.name,
            width,
            height,
            dest.name,
            new_dims[0],
            new_dims[1],
        )

    return ScreenCapture(
        name=shot.name,
        path=dest,
        geometry=shot.geometry,
        monitor_index=shot.monitor_index,
    )


def normalize_captures(shots: List[ScreenCapture]) -> List[ScreenCapture]:
    """Resize oversized captures so every monitor uses a suitable max dimension."""
    if not shots:
        return shots

    max_dimension, jpeg_quality = get_resize_settings()
    return [
        _resize_capture(
            shot,
            max_dimension=max_dimension,
            jpeg_quality=jpeg_quality,
        )
        for shot in shots
    ]
