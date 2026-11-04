"""System clock with NTP state (SPEC §5.6)."""

from __future__ import annotations

import asyncio
import datetime as dt
import subprocess
import time
from collections.abc import Callable

Runner = Callable[[list[str]], str]
CHECK_EVERY_S = 60.0


def _run(cmd: list[str]) -> str:
    # Fixed argument lists from this module only, no shell.
    return subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=False).stdout  # noqa: S603


class SystemClock:
    """``time_trusted`` only reads the last answer: ``timedatectl`` runs in ``run``, in a
    thread, so the controller's tick never waits for it (CLAUDE.md rule 1)."""

    def __init__(self, *, assume_trusted: bool = False, runner: Runner = _run) -> None:
        self._runner = runner
        self._trusted = assume_trusted

    def monotonic(self) -> float:
        return time.monotonic()

    def now(self) -> dt.datetime:
        return dt.datetime.now(dt.UTC)

    def time_trusted(self) -> bool:
        return self._trusted

    def check(self) -> bool:
        """``timedatectl``: NTPSynchronized=yes once synced since boot. Sticky once true."""
        if not self._trusted:
            try:
                out = self._runner(["timedatectl", "show", "--property=NTPSynchronized"])
            except (OSError, subprocess.SubprocessError):
                out = ""
            self._trusted = out.strip() == "NTPSynchronized=yes"
        return self._trusted

    async def run(self) -> None:
        """Background task: ask until the time is trusted, then stop."""
        while True:
            if await asyncio.to_thread(self.check):
                return
            await asyncio.sleep(CHECK_EVERY_S)
