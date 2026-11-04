"""One failing loop never takes playback down (CLAUDE.md rule 1, SPEC §1.1, §1.7)."""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest

import myboxi_agent.app as app_module
from myboxi_agent.adapters import hardware
from myboxi_agent.adapters.base import Placed, ReaderEvent
from myboxi_agent.adapters.bundle import sim_adapters
from myboxi_agent.adapters.hardware import SystemdSystem
from myboxi_agent.adapters.mpv import MpvClient
from myboxi_agent.adapters.sim import SimAnnouncer
from myboxi_agent.app import App, supervised
from myboxi_agent.config import Settings
from myboxi_agent.core.model import Playable, Prompt
from myboxi_agent.sync.client import ApiError, DeviceApi, Unreachable
from myboxi_protocol.state import StateResponse

from .test_sync_engine import TENANT, FakeApi, run_until

UID = "04A2B3C4D5E680"


def api_answering(handler: Any) -> DeviceApi:
    return DeviceApi("https://box.test", client=httpx.AsyncClient(transport=handler))


async def test_html_from_a_proxy_is_an_api_error() -> None:
    """SPEC §7: a 200 that is not the protocol model is retried, not a crash."""
    api = api_answering(httpx.MockTransport(lambda _r: httpx.Response(200, text="<html>")))
    with pytest.raises(ApiError):
        await api.state("jwt", 0, 0)
    with pytest.raises(ApiError):
        await api.token(TENANT, "s" * 43)


async def test_undecodable_body_is_unreachable() -> None:
    def broken(_request: httpx.Request) -> httpx.Response:
        raise httpx.DecodingError("bad gzip")

    api = api_answering(httpx.MockTransport(broken))
    with pytest.raises(Unreachable):
        await api.state("jwt", 0, 0)


@dataclass
class CrashingApi(FakeApi):
    """A full disk on the first sync, fine afterwards."""

    crashes: int = 1

    async def state(self, token: str, config_rev: int, device_rev: int) -> StateResponse:
        if self.crashes:
            self.crashes -= 1
            raise OSError(28, "No space left on device")
        return await super().state(token, config_rev, device_rev)


async def test_unexpected_sync_error_is_retried(tmp_path: Path) -> None:
    """CLAUDE.md rule 1: the sync loop logs, backs off and tries again."""
    api = CrashingApi()
    settings = Settings(data_dir=tmp_path, sim=True, default_server_url="https://box.test")
    app = App(settings, sim_adapters(), api_factory=lambda _url: api)  # pyright: ignore[reportArgumentType]
    app.state.set_paired(TENANT, "s" * 43)
    app.sync._wait = lambda _s: asyncio.sleep(0.01)  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
    await run_until(app, lambda: isinstance(app.library.resolve(UID), Playable))
    assert api.crashes == 0
    assert app.sync.status.last_error is None


async def test_a_crashing_loop_starts_again(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_module, "RESTART_S", 0.01)
    runs: list[int] = []

    async def flaky() -> None:
        runs.append(1)
        if len(runs) < 3:
            raise RuntimeError("boom")

    await asyncio.wait_for(supervised("flaky", flaky), 2)
    assert len(runs) == 3


class OneFigure:
    def __init__(self) -> None:
        self.after: asyncio.Event = asyncio.Event()

    async def events(self) -> AsyncIterator[ReaderEvent]:
        yield Placed(UID)
        yield Placed(UID)
        self.after.set()
        await asyncio.Event().wait()


async def test_a_failing_figure_event_is_audible_and_the_reader_goes_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SPEC §1.7: an internal error on placing a figure ends with the error tone."""
    app = App(Settings(data_dir=tmp_path, sim=True), sim_adapters())
    reader = OneFigure()
    app.adapters.reader = reader  # pyright: ignore[reportAttributeAccessIssue]
    calls: list[str] = []

    def broken(uid: str) -> None:
        calls.append(uid)
        raise RuntimeError("database is locked")

    monkeypatch.setattr(app.controller, "token_placed", broken)
    task = asyncio.create_task(app._reader_loop())  # pyright: ignore[reportPrivateUsage]
    await asyncio.wait_for(reader.after.wait(), 2)
    await asyncio.sleep(0)
    task.cancel()
    assert calls == [UID, UID]  # the loop survived the first one
    announcer = app.announcer
    assert isinstance(announcer, SimAnnouncer)
    assert announcer.history.count((Prompt.TONE_ERROR,)) == 2


async def test_mpv_event_callback_errors_keep_the_connection(tmp_path: Path) -> None:
    ipc = tmp_path / "mpv.sock"
    seen: list[str] = []

    def on_event(event: dict[str, Any]) -> None:
        seen.append(event["event"])
        if len(seen) == 1:
            raise RuntimeError("boom")

    async def serve(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.write(b'{"event":"file-loaded"}\n{"event":"idle"}\n')
        await writer.drain()
        await asyncio.sleep(5)

    server = await asyncio.start_unix_server(serve, path=str(ipc))
    client = MpvClient(ipc, on_event)
    task = asyncio.create_task(client.run())
    try:
        async with asyncio.timeout(2):
            while len(seen) < 2:
                await asyncio.sleep(0.01)
        assert client.connected.is_set()
    finally:
        task.cancel()
        server.close()
    assert seen == ["file-loaded", "idle"]


def test_hanging_systemctl_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    def hang(*_a: Any, **_kw: Any) -> Any:
        raise subprocess.TimeoutExpired("systemctl", 60)

    monkeypatch.setattr(hardware.subprocess, "run", hang)
    assert hardware.run_command(["systemctl", "start", "x"]) == -1


async def test_systemctl_runs_off_the_event_loop() -> None:
    """CLAUDE.md rule 1: a slow systemd never stalls playback."""
    started = asyncio.Event()
    release = asyncio.Event()
    loop = asyncio.get_running_loop()

    def slow(cmd: list[str]) -> int:
        del cmd
        loop.call_soon_threadsafe(started.set)
        asyncio.run_coroutine_threadsafe(release.wait(), loop).result(2)
        return 0

    system = SystemdSystem(runner=slow)
    system.request_setup_mode()  # returns at once
    await asyncio.wait_for(started.wait(), 2)
    release.set()
    system.request_update()
    await asyncio.sleep(0.05)
