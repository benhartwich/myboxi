"""ffprobe/ffmpeg wrappers (SPEC §3.8). Subprocesses without a shell; arguments are lists.

Opus output is bit-exact (``+bitexact``: fixed Ogg serial number, no encoder tag) so that the
same input and profile always yield the same SHA-256, which deduplication relies on.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from myboxi_server.models.enums import UploadProfile

log = logging.getLogger(__name__)

TARGET_I = -16.0  # LUFS, EBU R128 (SPEC §3.8)
TARGET_TP = -1.5
TARGET_LRA = 11.0
MAX_DURATION_MS = 6 * 3600 * 1000
# Below this measured loudness the input is effectively silent; loudnorm would amplify noise.
SILENCE_LUFS = -70.0

# Input options for uploaded files: ffmpeg picks the demuxer from the content, so without a
# whitelist an "audio" upload could be a playlist or manifest (DASH, concat) that reads other
# local files into the user's track. Only the formats we accept, only plain files.
AUDIO_INPUT = [
    "-protocol_whitelist", "file", "-format_whitelist", "mp3,mov,wav,ogg,flac,aac,matroska",
]  # fmt: skip
IMAGE_INPUT = [
    "-protocol_whitelist", "file", "-format_whitelist", "image2,png_pipe,jpeg_pipe,webp_pipe",
]  # fmt: skip

PROFILES: dict[UploadProfile, list[str]] = {
    # SPEC §3.8: mono 48 kbit/s for speech, stereo 96 kbit/s for music.
    UploadProfile.SPEECH: ["-ac", "1", "-b:a", "48k", "-application", "voip"],
    UploadProfile.MUSIC: ["-ac", "2", "-b:a", "96k", "-application", "audio"],
}


class MediaError(Exception):
    """Input is not usable; ``message`` is shown to the user (German)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class Probe:
    duration_ms: int
    has_audio: bool
    title: str | None


async def _run(args: list[str], limit_s: float) -> tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), limit_s)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise MediaError("Die Verarbeitung hat zu lange gedauert.") from None
    return proc.returncode or 0, out, err


async def probe(ffprobe: str, path: Path) -> Probe:
    code, out, _ = await _run(
        [
            ffprobe, "-v", "error", *AUDIO_INPUT, "-print_format", "json",
            "-show_entries", "format=duration:format_tags=title:stream=codec_type",
            str(path),
        ],
        limit_s=60,
    )  # fmt: skip
    if code != 0:
        raise MediaError("Die Datei konnte nicht gelesen werden.")
    data: dict[str, Any] = json.loads(out or b"{}")
    streams: list[dict[str, Any]] = data.get("streams", [])
    fmt: dict[str, Any] = data.get("format", {})
    try:
        duration_ms = round(float(fmt.get("duration", "nan")) * 1000)
    except (TypeError, ValueError, OverflowError):
        duration_ms = 0
    tags: dict[str, Any] = fmt.get("tags", {})
    title = next((str(v) for k, v in tags.items() if k.lower() == "title" and v), None)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    if duration_ms <= 0 and has_audio:
        duration_ms = await _packet_duration_ms(ffprobe, path)
    return Probe(
        duration_ms=duration_ms,
        has_audio=has_audio,
        title=title,
    )


async def _packet_duration_ms(ffprobe: str, path: Path) -> int:
    """Duration from the last audio packet: recordings from the browser (MediaRecorder WebM)
    carry no duration in their header."""
    code, out, _ = await _run(
        [
            ffprobe, "-v", "error", *AUDIO_INPUT, "-select_streams", "a:0",
            "-show_entries", "packet=pts_time,duration_time", "-of", "csv=p=0", str(path),
        ],
        limit_s=120,
    )  # fmt: skip
    if code != 0:
        return 0
    end = 0.0
    for line in out.decode(errors="replace").splitlines():
        pts, _, dur = line.partition(",")
        try:
            end = max(end, float(pts) + (float(dur) if dur else 0.0))
        except ValueError:
            continue
    return round(end * 1000) if math.isfinite(end) else 0


async def check_audio(ffprobe: str, path: Path) -> Probe:
    info = await probe(ffprobe, path)
    if not info.has_audio:
        raise MediaError("Die Datei enthält keine Tonspur.")
    if info.duration_ms <= 0:
        raise MediaError("Die Datei ist leer oder beschädigt.")
    if info.duration_ms > MAX_DURATION_MS:
        raise MediaError("Die Datei ist länger als 6 Stunden.")
    return info


def _loudnorm(extra: str = "") -> str:
    return f"loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={TARGET_LRA}{extra}"


async def measure_loudness(ffmpeg: str, src: Path) -> dict[str, str] | None:
    """First loudnorm pass. Returns the measurements, or None for (near) silence."""
    code, _, err = await _run(
        [
            ffmpeg, "-hide_banner", "-nostdin", *AUDIO_INPUT, "-i", str(src),
            "-vn", "-map", "0:a:0",
            "-af", _loudnorm(":print_format=json"), "-f", "null", "-",
        ],
        limit_s=3600,
    )  # fmt: skip
    if code != 0:
        raise MediaError("Die Tonspur konnte nicht analysiert werden.")
    text = err.decode(errors="replace")
    start, end = text.rfind("{"), text.rfind("}")
    if start < 0 or end < start:
        raise MediaError("Die Lautheit konnte nicht gemessen werden.")
    measured: dict[str, str] = json.loads(text[start : end + 1])
    try:
        input_i = float(measured["input_i"])
    except (KeyError, ValueError):
        return None
    if math.isinf(input_i) or input_i < SILENCE_LUFS:
        return None
    return measured


async def transcode(
    ffmpeg: str, src: Path, dest: Path, profile: UploadProfile, measured: dict[str, str] | None
) -> None:
    """Second pass: normalize to -16 LUFS and encode Opus (SPEC §3.8)."""
    filters: list[str] = []
    if measured is not None:
        filters = [
            "-af",
            _loudnorm(
                f":measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
                f":measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}"
                f":offset={measured['target_offset']}:linear=true:print_format=none"
            ),
        ]
    code, _, err = await _run(
        [
            ffmpeg, "-hide_banner", "-nostdin", "-y", *AUDIO_INPUT, "-i", str(src),
            "-vn", "-sn", "-dn", "-map", "0:a:0", *filters,
            "-ar", "48000", "-c:a", "libopus", *PROFILES[profile],
            "-map_metadata", "-1", "-fflags", "+bitexact", "-flags:a", "+bitexact",
            "-f", "ogg", str(dest),
        ],
        limit_s=3600,
    )  # fmt: skip
    if code != 0:
        log.warning(
            "ffmpeg transcode failed", extra={"stderr": err.decode(errors="replace")[-2000:]}
        )
        raise MediaError("Die Datei konnte nicht umgewandelt werden.")


async def make_cover(ffmpeg: str, src: Path, dest: Path) -> None:
    """Square 512x512 JPEG for the app (SPEC v0.3 §3.8)."""
    code, _, _ = await _run(
        [
            ffmpeg, "-hide_banner", "-nostdin", "-y", *IMAGE_INPUT, "-i", str(src),
            "-frames:v", "1",
            "-vf", "scale=512:512:force_original_aspect_ratio=increase,crop=512:512",
            "-map_metadata", "-1", "-fflags", "+bitexact", "-q:v", "3", "-f", "image2",
            "-c:v", "mjpeg", str(dest),
        ],
        limit_s=60,
    )  # fmt: skip
    if code != 0 or not await asyncio.to_thread(dest.exists):
        raise MediaError("Das Bild konnte nicht gelesen werden.")
