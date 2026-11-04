"""mpv over its JSON IPC (CLAUDE.md stack): one instance for content, one for prompts.

The Player/Announcer methods are commands: they queue IPC messages and return at once.
mpv reports back through events, which become controller callbacks.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import itertools
import json
import logging
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from myboxi_agent.core.model import PlanItem, Prompt, ResumePoint
from myboxi_protocol.state import RepeatMode

log = logging.getLogger(__name__)

TONE_FALLBACK = "av://lavfi:sine=frequency=660:duration=0.25"
ERROR_FALLBACK = "av://lavfi:sine=frequency=330:duration=0.6"
GAIN_MIN_DB = 0.1  # smaller corrections are inaudible: play unchanged
LIMIT = 0.891  # -1 dBFS (SPEC v0.8 §8.2)


def gain_filter(gain_db: float) -> str:
    """Per-file audio filter for the loudness correction (SPEC v0.8 §8.2). A boost runs
    through a limiter without its auto level, so peaks cannot clip."""
    graph = f"volume={gain_db:.1f}dB"
    if gain_db > 0:
        graph += f",alimiter=limit={LIMIT}:level=0"
    return f"lavfi=[{graph}]"


def loadfile_command(item: PlanItem) -> dict[str, Any] | list[Any]:
    if item.gain_db is None or abs(item.gain_db) < GAIN_MIN_DB:
        return ["loadfile", item.source, "append"]
    value = gain_filter(item.gain_db)
    # Per-file options (kept with the playlist entry). Named arguments work on every mpv
    # since 0.33; the %n% quoting keeps the commas of the filter graph in one value.
    return {
        "name": "loadfile",
        "url": item.source,
        "flags": "append",
        "options": f"af=%{len(value)}%{value}",
    }


def mpv_args(mpv: str, ipc: Path, audio_output: str, client_name: str) -> list[str]:
    return [
        mpv,
        "--idle=yes",
        "--no-video",
        "--no-terminal",
        "--really-quiet",
        f"--input-ipc-server={ipc}",
        f"--ao={audio_output}",
        f"--audio-client-name={client_name}",
        "--gapless-audio=weak",
        "--volume=0",
        "--volume-max=100",
    ]


class MpvClient:
    """Minimal JSON IPC client: ordered commands, events to a callback, auto-reconnect."""

    def __init__(self, ipc: Path, on_event: Callable[[dict[str, Any]], None]) -> None:
        self.ipc = ipc
        self.on_event = on_event
        # Commands that go first on every new connection (observers are per connection).
        self.hello: Callable[[], list[list[Any]]] | None = None
        self._queue: asyncio.Queue[tuple[dict[str, Any] | list[Any], Callable[[], None] | None]]
        self._queue = asyncio.Queue()
        self._ids = itertools.count(1)
        self._replies: dict[int, Callable[[], None]] = {}
        self.connected = asyncio.Event()

    def send(self, *command: Any) -> None:
        self._queue.put_nowait((list(command), None))

    def send_command(
        self, command: dict[str, Any] | list[Any], on_reply: Callable[[], None] | None = None
    ) -> None:
        """A prepared command, e.g. with named arguments. ``on_reply`` runs once mpv
        answered it: every event mpv sent before handling it has arrived by then."""
        self._queue.put_nowait((command, on_reply))

    async def run(self) -> None:
        while True:
            try:
                reader, writer = await asyncio.open_unix_connection(str(self.ipc))
            except OSError:  # not there yet, refused, or a stale socket file
                await asyncio.sleep(0.2)
                continue
            self._replies.clear()
            self.connected.set()
            try:
                for command in self.hello() if self.hello is not None else []:
                    self._write_one(writer, command, None)
                async with asyncio.TaskGroup() as tg:
                    tg.create_task(self._write(writer))
                    tg.create_task(self._read(reader))
            except* (OSError, asyncio.IncompleteReadError, EOFError):
                log.warning("mpv IPC connection lost", extra={"ipc": str(self.ipc)})
            finally:
                self.connected.clear()
                writer.close()
            await asyncio.sleep(0.5)

    def _write_one(
        self,
        writer: asyncio.StreamWriter,
        command: dict[str, Any] | list[Any],
        on_reply: Callable[[], None] | None,
    ) -> None:
        request_id = next(self._ids)
        if on_reply is not None:
            self._replies[request_id] = on_reply
        payload = {"command": command, "request_id": request_id}
        writer.write(json.dumps(payload).encode() + b"\n")

    async def _write(self, writer: asyncio.StreamWriter) -> None:
        while True:
            command, on_reply = await self._queue.get()
            self._write_one(writer, command, on_reply)
            await writer.drain()

    async def _read(self, reader: asyncio.StreamReader) -> None:
        while True:
            line = await reader.readline()
            if not line:
                raise EOFError
            try:
                message: dict[str, Any] = json.loads(line)
            except ValueError:
                continue
            if "event" in message:
                self._call(functools.partial(self.on_event, message), message)
                continue
            if message.get("error") not in (None, "success"):
                log.warning("mpv command failed", extra={"error": message.get("error")})
            request_id = message.get("request_id")
            on_reply = self._replies.pop(request_id, None) if isinstance(request_id, int) else None
            if on_reply is not None:
                self._call(on_reply)

    def _call(self, callback: Callable[[], None], event: dict[str, Any] | None = None) -> None:
        try:
            callback()
        except Exception:
            # a failing callback must not cut the connection to mpv
            log.exception("mpv callback failed", extra={"event": (event or {}).get("event")})


class MpvProcess:
    """Keeps an mpv process running; restarts it when it dies."""

    def __init__(self, args: Sequence[str], ipc: Path, on_restart: Callable[[], None]) -> None:
        self.args = list(args)
        self.ipc = ipc
        self.on_restart = on_restart

    async def run(self) -> None:
        first = True
        while True:
            with contextlib.suppress(FileNotFoundError):
                self.ipc.unlink()
            proc = await asyncio.create_subprocess_exec(
                *self.args, stdin=asyncio.subprocess.DEVNULL
            )
            if not first:
                self.on_restart()
            first = False
            try:
                code = await proc.wait()
            except asyncio.CancelledError:
                proc.terminate()
                with contextlib.suppress(ProcessLookupError):
                    await asyncio.wait_for(proc.wait(), 3)
                raise
            log.error("mpv exited", extra={"code": code})
            await asyncio.sleep(1)


class MpvPlayer:
    """``core.ports.Player`` on mpv."""

    def __init__(
        self, client_factory: Callable[[Callable[[dict[str, Any]], None]], MpvClient]
    ) -> None:
        self.client = client_factory(self._on_event)
        self.client.hello = self._hello
        self.on_playlist_finished: Callable[[], None] | None = None
        self.on_error: Callable[[str], None] | None = None
        self._pos_index = 0
        self._pos_s = 0.0
        self._volume: int | None = None
        self._play_id = 0  # one per ``play``
        self._ready_id = 0  # mpv handled the ``stop`` of this ``play``: its events are current
        self._pending_start = False
        self._active = False  # expecting an end-of-playlist idle event

    def _hello(self) -> list[list[Any]]:
        """On every (re)connect: observers are per connection, and a restarted mpv starts
        muted (``--volume=0``), so the last volume goes again (CLAUDE.md rule 6). Events
        of the old connection are gone, so what follows is current."""
        self._ready_id = self._play_id
        hello: list[list[Any]] = [
            ["observe_property", 1, "playlist-pos"],
            ["observe_property", 2, "time-pos"],
        ]
        if self._volume is not None:
            hello.append(["set_property", "volume", self._volume])
        return hello

    def play(
        self, items: Sequence[PlanItem], index: int, position_ms: int, repeat: RepeatMode
    ) -> None:
        self._active = False
        self._play_id += 1
        play_id = self._play_id
        self.client.send_command(["stop"], on_reply=lambda: self._ready(play_id))
        self.client.send("set_property", "loop-file", "inf" if repeat == "one" else "no")
        self.client.send("set_property", "loop-playlist", "inf" if repeat == "all" else "no")
        # Always set: a start left from an earlier figure must not apply to this one.
        start = f"{position_ms / 1000:.3f}" if position_ms > 0 else "none"
        self._pending_start = position_ms > 0
        self.client.send("set_property", "start", start)
        for item in items:
            self.client.send_command(loadfile_command(item))
        self.client.send("set_property", "pause", False)
        self.client.send("playlist-play-index", index)
        self._pos_index, self._pos_s = index, position_ms / 1000
        self._active = True

    def _ready(self, play_id: int) -> None:
        self._ready_id = play_id

    def pause(self) -> None:
        self.client.send("set_property", "pause", True)

    def resume(self) -> None:
        self.client.send("set_property", "pause", False)

    def stop(self) -> None:
        self._active = False
        self.client.send("stop")

    def set_volume(self, volume: int) -> None:
        self._volume = max(0, min(volume, 100))
        self.client.send("set_property", "volume", self._volume)

    def position(self) -> ResumePoint | None:
        return ResumePoint(self._pos_index, int(self._pos_s * 1000))

    def restarted(self) -> None:
        """mpv crashed and came back: the playlist is gone."""
        if self._active and self.on_error is not None:
            self._active = False
            self.on_error("player_restart")

    def _on_event(self, event: dict[str, Any]) -> None:
        name = event.get("event")
        if self._ready_id != self._play_id:
            return  # a late event of the playlist before this ``play``
        if name == "property-change":
            data = event.get("data")
            if event.get("name") == "playlist-pos" and isinstance(data, int) and data >= 0:
                self._pos_index = data
            elif event.get("name") == "time-pos" and isinstance(data, int | float):
                self._pos_s = float(data)
        elif name == "file-loaded" and self._pending_start:
            # "start" applies to every file loaded afterwards; only the first one resumes.
            self._pending_start = False
            self.client.send("set_property", "start", "none")
        elif name == "end-file" and event.get("reason") == "error":
            if self.on_error is not None:
                self.on_error("decode_error")
        elif name == "idle" and self._active:
            self._active = False
            if self.on_playlist_finished is not None:
                self.on_playlist_finished()


class MpvAnnouncer:
    """``core.ports.Announcer``: prompt files, own recordings first (SPEC v0.5 §4).

    A missing file becomes a generated tone, so a situation is never silent (SPEC §1.7).
    Volume follows the single volume policy through ``volume``.
    """

    def __init__(
        self,
        client_factory: Callable[[Callable[[dict[str, Any]], None]], MpvClient],
        prompt_dirs: Sequence[Path],
        volume: Callable[[], int],
    ) -> None:
        self.client = client_factory(lambda _event: None)
        self.prompt_dirs = list(prompt_dirs)
        self.volume = volume  # core.volume.prompt_volume via the controller (CLAUDE.md rule 4)

    def source(self, prompt: str) -> str:
        for directory in self.prompt_dirs:
            path = directory / f"{prompt}.opus"
            if path.is_file():
                return str(path)
        log.warning("prompt missing", extra={"prompt": prompt})
        return TONE_FALLBACK if prompt.startswith("tone_") else ERROR_FALLBACK

    def announce(self, *prompts: str) -> None:
        self.client.send("set_property", "volume", self.volume())
        for prompt in prompts:
            self.client.send("loadfile", self.source(str(prompt)), "append-play")


KNOWN_PROMPTS = [p.value for p in Prompt] + [f"digit_{d}" for d in "0123456789"]


def missing_prompts(prompt_dirs: Sequence[Path]) -> list[str]:
    """Prompts without a file in any directory (own recordings first, SPEC v0.5 §4)."""
    return [p for p in KNOWN_PROMPTS if not any((d / f"{p}.opus").is_file() for d in prompt_dirs)]
