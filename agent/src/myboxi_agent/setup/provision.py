"""The setup file ``myboxi-setup.json`` on the boot partition (SPEC v0.14 §9.7).

``myboxi-agent provision`` runs as root at boot (myboxi-provision.service), after
NetworkManager and before the agent's user session:

1. Wi-Fi country, Wi-Fi connection (NetworkManager connects on its own), optionally the SSH
   account ``admin`` (keys only).
2. Server address, own CA and the one-time claim token go to the agent as a handover file in
   its data directory (owner ``myboxi``, 0600); the agent applies it when it starts.
3. The setup file is deleted: it holds the Wi-Fi password. A file the box cannot use is
   renamed to ``myboxi-setup.failed.json`` instead, so the next boot does not try again.

Nothing secret is logged: no password, token or key, and no values from validation errors.
"""

from __future__ import annotations

import json
import logging
import os
import pwd
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ValidationError

from myboxi_agent.setup.nm import Result
from myboxi_agent.sync.tls import normalize_ca, valid_ca
from myboxi_protocol.pairing import ClaimToken
from myboxi_protocol.setup_file import MAX_BYTES, SetupFile

log = logging.getLogger(__name__)

FAILED_NAME = "myboxi-setup.failed.json"
ADMIN = "admin"
SSHD_DROP_IN = """# Myboxi (setup file, SPEC v0.14 §9.7): keys only, no root login.
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
"""

Runner = Callable[[Sequence[str], float], Result]


def run_command(args: Sequence[str], timeout: float = 30) -> Result:
    try:
        proc = subprocess.run(  # noqa: S603 - fixed programs, argument lists
            list(args), capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Result(1, str(exc))
    return Result(proc.returncode, proc.stdout)


class Handover(BaseModel):
    """What only the agent can apply (its own database)."""

    server_url: str
    server_ca: str | None = None
    claim_token: ClaimToken | None = None

    def __repr__(self) -> str:  # never the token
        return f"Handover(server_url={self.server_url!r}, ca={self.server_ca is not None})"


class WifiAdder(Protocol):
    def add_wifi(self, ssid: str, password: str | None) -> bool: ...


def _chown(path: Path, user: str) -> None:
    entry = pwd.getpwnam(user)
    os.chown(path, entry.pw_uid, entry.pw_gid)


@dataclass
class Provisioner:
    setup_file: Path
    handover: Path
    wifi: WifiAdder
    run: Runner = run_command
    agent_user: str = "myboxi"
    sudoers_dir: Path = Path("/etc/sudoers.d")
    sshd_dir: Path = Path("/etc/ssh/sshd_config.d")
    home_root: Path = Path("/home")
    chown: Callable[[Path, str], None] = _chown

    def apply(self) -> bool:
        """True if there was nothing to do or the file was applied."""
        if not self.setup_file.exists():
            return True
        setup = self._read()
        if setup is None:
            self._give_up()
            return False
        if setup.wifi_country:
            self.run(["raspi-config", "nonint", "do_wifi_country", setup.wifi_country], 60)
        if setup.wifi is not None and not self.wifi.add_wifi(setup.wifi.ssid, setup.wifi.password):
            self._give_up()
            return False
        if setup.ssh_authorized_keys and not self._ssh(setup.ssh_authorized_keys):
            self._give_up()
            return False
        self._write_handover(setup)
        self.setup_file.unlink()
        os.sync()
        log.info(
            "setup file applied",
            extra={"wifi": setup.wifi is not None, "ssh": bool(setup.ssh_authorized_keys),
                   "claim": setup.claim_token is not None, "own_ca": setup.server_ca is not None},
        )  # fmt: skip
        return True

    def _read(self) -> SetupFile | None:
        try:
            raw = self.setup_file.read_bytes()
        except OSError as exc:
            log.error("setup file unreadable", extra={"error": exc.strerror})
            return None
        if len(raw) > MAX_BYTES:
            log.error("setup file too large")
            return None
        try:
            setup = SetupFile.model_validate_json(raw.decode("utf-8-sig"))
        except (ValidationError, UnicodeDecodeError) as exc:
            where = (
                [".".join(str(p) for p in e["loc"]) for e in exc.errors()]
                if isinstance(exc, ValidationError)
                else ["encoding"]
            )
            log.error("setup file invalid", extra={"fields": where[:10]})  # never the values
            return None
        if setup.server_ca is not None and not valid_ca(setup.server_ca):
            log.error("setup file invalid", extra={"fields": ["server_ca"]})
            return None
        return setup

    def _give_up(self) -> None:
        """Keep the file for the household to look at, but never try it again."""
        try:
            self.setup_file.rename(self.setup_file.with_name(FAILED_NAME))
        except OSError:
            log.exception("could not rename the setup file")

    def _ssh(self, keys: list[str]) -> bool:
        """The account ``admin``: login with these keys only, sudo without password."""
        if self.run(["id", "-u", ADMIN], 10).code != 0:
            add = self.run(["useradd", "--create-home", "--shell", "/bin/bash",
                            "--groups", "sudo", ADMIN], 30)  # fmt: skip
            if add.code != 0:
                log.error("could not create the admin account")
                return False
        ssh = self.home_root / ADMIN / ".ssh"  # useradd's default home
        ssh.mkdir(mode=0o700, parents=True, exist_ok=True)
        keyfile = ssh / "authorized_keys"
        _write(keyfile, "".join(f"{k}\n" for k in keys), 0o600)
        for path in (ssh, keyfile):
            self.chown(path, ADMIN)
        self.sudoers_dir.mkdir(parents=True, exist_ok=True)
        _write(self.sudoers_dir / "010_myboxi-admin", f"{ADMIN} ALL=(ALL) NOPASSWD: ALL\n", 0o440)
        self.sshd_dir.mkdir(parents=True, exist_ok=True)
        _write(self.sshd_dir / "10-myboxi.conf", SSHD_DROP_IN, 0o644)
        # never wait for ssh here: it is ordered after this unit at boot
        self.run(["systemctl", "enable", "ssh.service"], 30)
        self.run(["systemctl", "start", "--no-block", "ssh.service"], 30)
        return True

    def _write_handover(self, setup: SetupFile) -> None:
        handover = Handover(
            server_url=setup.server_url,
            server_ca=normalize_ca(setup.server_ca) if setup.server_ca else None,
            claim_token=setup.claim_token,
        )
        self.handover.parent.mkdir(parents=True, exist_ok=True)
        _write(self.handover, handover.model_dump_json(exclude_none=True), 0o600)
        self.chown(self.handover, self.agent_user)


def _write(path: Path, text: str, mode: int) -> None:
    """Atomically, with ``mode`` from the start (never readable by others in between)."""
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    tmp.chmod(mode)
    tmp.replace(path)


def read_handover(path: Path) -> Handover | None:
    """For the agent: the handover once; the file is removed in any case."""
    if not path.exists():
        return None
    try:
        return Handover.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, ValidationError):
        log.error("provision handover invalid")
        return None
    finally:
        path.unlink(missing_ok=True)
