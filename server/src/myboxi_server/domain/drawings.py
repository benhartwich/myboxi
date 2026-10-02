"""Drawings for "Figur gestalten" (docs/gehaeuse.md): a photo of a child's drawing becomes a
figure.

The photo is decoded and traced in memory and never stored. What is stored are the traced
strokes (polygons), under the hash of their content, for 7 days: long enough for the preview,
the download and a later visit to the shared link.

Decoding is the risky part (untrusted images): only JPEG, PNG and WebP, recognised by their
first bytes and opened with the matching ffmpeg demuxer, at most 40 megapixels, 20 s.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import subprocess
import time
from pathlib import Path

import numpy as np
import numpy.typing as npt

from myboxi_case import trace

MAX_BYTES = 16 * 1024 * 1024
RETENTION = dt.timedelta(days=7)
DECODE_TIMEOUT_S = 20
MAX_PIXELS = 40_000_000
UNSUPPORTED = "Bitte ein Foto als JPEG, PNG oder WebP hochladen."


class DrawingError(ValueError):
    """German, shown to the user."""


def _demuxer(data: bytes) -> str | None:
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg_pipe"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png_pipe"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp_pipe"
    return None


def decode(data: bytes, ffmpeg: str = "ffmpeg") -> npt.NDArray[np.uint8]:
    """The image as ``trace.SIZE`` x ``trace.SIZE`` grey pixels, padded with white."""
    demuxer = _demuxer(data)
    if demuxer is None or len(data) > MAX_BYTES:
        raise DrawingError(UNSUPPORTED)
    size = trace.SIZE
    scale = (
        f"scale={size}:{size}:force_original_aspect_ratio=decrease:flags=area,"
        f"pad={size}:{size}:(ow-iw)/2:(oh-ih)/2:color=white,format=gray"
    )
    try:
        proc = subprocess.run(  # noqa: S603 - fixed program, argument list, data on stdin
            [ffmpeg, "-v", "error", "-nostats", "-threads", "1", "-max_pixels", str(MAX_PIXELS),
             "-f", demuxer, "-i", "pipe:0", "-frames:v", "1", "-vf", scale,
             "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"],
            input=data, capture_output=True, timeout=DECODE_TIMEOUT_S, check=False,
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DrawingError("Das Foto ließ sich nicht öffnen. Bitte ein anderes versuchen.") from exc
    if proc.returncode != 0 or len(proc.stdout) != size * size:
        raise DrawingError("Das Foto ließ sich nicht öffnen. Bitte ein anderes versuchen.")
    return np.frombuffer(proc.stdout, dtype=np.uint8).reshape(size, size).copy()


def _dir(data_dir: Path) -> Path:
    return data_dir / "drawings"


def store(data_dir: Path, rings: trace.Rings) -> str:
    """Saves the strokes; the id is the start of their hash (equal drawings, equal ids)."""
    text = trace.dumps(rings)
    drawing_id = hashlib.sha256(text.encode()).hexdigest()[:16]
    folder = _dir(data_dir)
    folder.mkdir(mode=0o750, parents=True, exist_ok=True)
    path = folder / f"{drawing_id}.json"
    tmp = folder / f".{drawing_id}.{os.getpid()}.tmp"
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)  # a newer upload of the same drawing keeps it another 7 days
    return drawing_id


def load(data_dir: Path, drawing_id: str | None) -> trace.Rings | None:
    if drawing_id is None or len(drawing_id) != 16 or not drawing_id.isalnum():
        return None
    try:
        return trace.loads((_dir(data_dir) / f"{drawing_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def purge(data_dir: Path, now: float | None = None) -> int:
    """Deletes strokes older than 7 days; returns how many."""
    folder = _dir(data_dir)
    if not folder.is_dir():
        return 0
    cutoff = (now if now is not None else time.time()) - RETENTION.total_seconds()
    removed = 0
    for path in folder.iterdir():
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed
