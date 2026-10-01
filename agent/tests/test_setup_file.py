"""The setup file on the boot partition (SPEC v0.14 §9.7): applied at boot by root, handed
over to the agent, and the box pairs itself with the household's one-time token."""

from __future__ import annotations

import json
import logging
import stat
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from myboxi_agent.adapters.bundle import sim_adapters
from myboxi_agent.adapters.sim import SimAnnouncer
from myboxi_agent.app import App
from myboxi_agent.config import Settings
from myboxi_agent.core.model import Prompt
from myboxi_agent.setup.nm import Result
from myboxi_agent.setup.provision import FAILED_NAME, Provisioner, read_handover
from myboxi_agent.sync import engine as engine_module
from myboxi_agent.sync.client import ApiError
from myboxi_protocol.errors import ErrorCode

from . import owncerts
from .test_sync_engine import TENANT, FakeApi, claimed, run_until

TOKEN = "q3V0bWJ0ZXN0LXRva2VuLTAxMjM0NTY3ODlhYmNkZWZ"
WIFI_PASSWORD = "geheimes-wlan-passwort"
KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGb5bN0p1r1o0yQq4yE0bQ2bS0x8x6H4m8v1u2w3x4y5 ich@pc"


@dataclass
class FakeWifi:
    ok: bool = True
    added: list[tuple[str, str | None]] = field(default_factory=list[tuple[str, str | None]])

    def add_wifi(self, ssid: str, password: str | None) -> bool:
        self.added.append((ssid, password))
        return self.ok


@dataclass
class Rig:
    tmp: Path
    wifi: FakeWifi = field(default_factory=FakeWifi)
    commands: list[list[str]] = field(default_factory=list[list[str]])
    owners: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])

    @property
    def setup_file(self) -> Path:
        return self.tmp / "boot" / "myboxi-setup.json"

    @property
    def handover(self) -> Path:
        return self.tmp / "data" / "provision.json"

    def run(self, args: Sequence[str], timeout: float) -> Result:
        del timeout
        self.commands.append(list(args))
        return Result(1 if args[:2] == ["id", "-u"] else 0, "")

    def provisioner(self) -> Provisioner:
        return Provisioner(
            setup_file=self.setup_file, handover=self.handover, wifi=self.wifi, run=self.run,
            sudoers_dir=self.tmp / "sudoers.d", sshd_dir=self.tmp / "sshd_config.d",
            home_root=self.tmp / "home", chown=lambda p, u: self.owners.append((p.name, u)),
        )  # fmt: skip

    def write(self, data: dict[str, Any]) -> None:
        self.setup_file.parent.mkdir(parents=True, exist_ok=True)
        self.setup_file.write_text(json.dumps(data))


def setup_json(**extra: Any) -> dict[str, Any]:
    return {"myboxi_setup": 1, "server_url": "https://app.myboxi.eu", "claim_token": TOKEN,
            "wifi": {"ssid": "Heimnetz", "password": WIFI_PASSWORD}, "wifi_country": "DE",
            **extra}  # fmt: skip


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


def test_the_file_is_applied_handed_over_and_deleted(
    rig: Rig, caplog: pytest.LogCaptureFixture
) -> None:
    rig.write(setup_json(ssh_authorized_keys=[KEY]))
    with caplog.at_level(logging.DEBUG):
        assert rig.provisioner().apply()
    assert not rig.setup_file.exists()  # it held the Wi-Fi password
    assert rig.wifi.added == [("Heimnetz", WIFI_PASSWORD)]
    assert ["raspi-config", "nonint", "do_wifi_country", "DE"] in rig.commands
    handover = json.loads(rig.handover.read_text())
    assert handover == {"server_url": "https://app.myboxi.eu", "claim_token": TOKEN}
    assert stat.S_IMODE(rig.handover.stat().st_mode) == 0o600
    assert ("provision.json", "myboxi") in rig.owners
    # SSH: the account "admin", keys only, sudo
    assert ["useradd", "--create-home", "--shell", "/bin/bash", "--groups", "sudo",
            "admin"] in rig.commands  # fmt: skip
    keys = rig.tmp / "home" / "admin" / ".ssh" / "authorized_keys"
    assert keys.read_text() == KEY + "\n"
    assert stat.S_IMODE(keys.stat().st_mode) == 0o600
    assert ("authorized_keys", "admin") in rig.owners
    assert "PasswordAuthentication no" in (rig.tmp / "sshd_config.d" / "10-myboxi.conf").read_text()
    assert stat.S_IMODE((rig.tmp / "sudoers.d" / "010_myboxi-admin").stat().st_mode) == 0o440
    assert ["systemctl", "start", "--no-block", "ssh.service"] in rig.commands
    assert WIFI_PASSWORD not in caplog.text
    assert TOKEN not in caplog.text


def test_without_a_file_nothing_happens(rig: Rig) -> None:
    assert rig.provisioner().apply()
    assert rig.commands == []
    assert not rig.handover.exists()


@pytest.mark.parametrize(
    "data",
    [
        setup_json(server_url="http://app.myboxi.eu"),  # never plain HTTP
        setup_json(wifi={"ssid": "Heimnetz", "password": "kurz"}),
        setup_json(server_ca="-----BEGIN CERTIFICATE-----\nnope\n-----END CERTIFICATE-----"),
        {"hello": "world"},
    ],
)
def test_a_file_the_box_cannot_use_is_set_aside(
    rig: Rig, data: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    rig.write(data)
    with caplog.at_level(logging.DEBUG):
        assert not rig.provisioner().apply()
    assert not rig.setup_file.exists()
    assert (rig.setup_file.parent / FAILED_NAME).exists()  # not tried again at the next boot
    assert not rig.handover.exists()
    assert rig.wifi.added == []
    assert "kurz" not in caplog.text  # the field, never the value


def test_wifi_that_cannot_be_added_stops_everything(rig: Rig) -> None:
    rig.wifi.ok = False
    rig.write(setup_json())
    assert not rig.provisioner().apply()
    assert not rig.handover.exists()
    assert (rig.setup_file.parent / FAILED_NAME).exists()


def test_broken_json_is_set_aside(rig: Rig) -> None:
    rig.setup_file.parent.mkdir(parents=True)
    rig.setup_file.write_bytes(b"\xff\xfe{not json")
    assert not rig.provisioner().apply()
    assert (rig.setup_file.parent / FAILED_NAME).exists()


# --- the agent takes over ------------------------------------------------------------------


@pytest.fixture
def api() -> FakeApi:
    return FakeApi()


@pytest.fixture
def app(tmp_path: Path, api: FakeApi, monkeypatch: pytest.MonkeyPatch) -> App:
    monkeypatch.setattr(engine_module, "POLL_S", 0.01)
    settings = Settings(data_dir=tmp_path / "box", sim=True, default_server_url="https://box.test")
    return App(settings, sim_adapters(), api_factory=lambda _url: api)  # pyright: ignore[reportArgumentType]


def hand_over(app: App, **data: Any) -> None:
    app.settings.provision_handover.parent.mkdir(parents=True, exist_ok=True)
    app.settings.provision_handover.write_text(json.dumps(data))


async def test_the_box_pairs_itself_without_a_spoken_code(app: App, api: FakeApi) -> None:
    hand_over(app, server_url="https://box.test", claim_token=TOKEN)
    app.apply_handover()
    assert not app.settings.provision_handover.exists()
    assert app.state.claim_token() == TOKEN
    api.polls = [claimed()]
    await run_until(app, lambda: app.state.get().tenant_id == TENANT)
    assert api.start_bodies[0].claim_token == TOKEN
    assert isinstance(app.announcer, SimAnnouncer)
    said = [p for line in app.announcer.history for p in line]
    assert Prompt.PAIRING_INTRO not in said  # nobody needs the code
    assert Prompt.PAIRING_DONE in said
    assert app.state.claim_token() is None  # used up


async def test_an_invalid_token_falls_back_to_the_code(app: App, api: FakeApi) -> None:
    app.state.set_claim_token(TOKEN)
    api.start_errors = [ApiError(403, ErrorCode.CLAIM_INVALID)]
    api.polls = [claimed()]
    await run_until(app, lambda: app.state.get().tenant_id == TENANT)
    assert [b.claim_token for b in api.start_bodies] == [None]  # only the retry got through
    assert app.state.claim_token() is None
    assert isinstance(app.announcer, SimAnnouncer)
    assert any(Prompt.PAIRING_INTRO in line for line in app.announcer.history)


def test_a_paired_box_moves_to_the_household_of_the_file(app: App) -> None:
    app.state.set_paired(TENANT, "s" * 43)
    hand_over(app, server_url="https://box.test", claim_token=TOKEN)
    app.apply_handover()
    assert app.sync._repair  # pyright: ignore[reportPrivateUsage]  # unpair, then pair with the token


@pytest.mark.skipif(not owncerts.available(), reason="openssl not installed")
def test_a_self_hosted_server_comes_with_its_ca(app: App, tmp_path: Path) -> None:
    pem = owncerts.make(tmp_path / "c").ca.read_text()
    hand_over(app, server_url="https://myboxi.home.arpa", server_ca=pem)
    app.apply_handover()
    assert app.sync.server_url() == "https://myboxi.home.arpa"
    assert app.state.server_ca() == pem


def test_a_plain_http_server_from_a_handover_is_refused(app: App) -> None:
    app.settings = app.settings.model_copy(update={"sim": False})
    hand_over(app, server_url="http://evil.example", claim_token=TOKEN)
    app.apply_handover()
    assert app.state.get().server_url is None
    assert app.state.claim_token() is None
    assert read_handover(app.settings.provision_handover) is None  # gone
