"""The setup file ``myboxi-setup.json`` on the box's boot partition (SPEC v0.14 §9.7).

The web app builds it in the browser (the Wi-Fi password never reaches the server); the box
reads it once at boot, applies it and deletes it.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator

from myboxi_protocol.common import ProtocolModel
from myboxi_protocol.pairing import ClaimToken

MAX_BYTES = 65536
# OpenSSH public keys the box accepts for the "admin" account; never private keys.
SshKey = Annotated[
    str,
    StringConstraints(
        pattern=r"^(ssh-ed25519|ecdsa-sha2-nistp(256|384|521)|sk-ssh-ed25519@openssh\.com|"
        r"sk-ecdsa-sha2-nistp256@openssh\.com|ssh-rsa) [A-Za-z0-9+/=]{40,4096}( [^\n\r]{0,200})?$"
    ),
]


class SetupWifi(ProtocolModel):
    ssid: Annotated[str, StringConstraints(min_length=1, max_length=32)]
    # Empty or missing: an open network. WPA needs 8 to 63 characters.
    password: str | None = Field(default=None, repr=False)

    @field_validator("ssid")
    @classmethod
    def _ssid_bytes(cls, v: str) -> str:
        if not 1 <= len(v.encode()) <= 32:
            raise ValueError("SSID: 1 to 32 bytes")
        return v

    @field_validator("password")
    @classmethod
    def _psk(cls, v: str | None) -> str | None:
        if v and not (8 <= len(v) <= 63 and v.isprintable()):
            raise ValueError("Wi-Fi password: 8 to 63 printable characters")
        return v or None


class SetupFile(ProtocolModel):
    myboxi_setup: Literal[1]
    server_url: Annotated[
        str, StringConstraints(pattern=r"^https://[^\s/]+(/[^\s]*)?$", max_length=200)
    ]
    # SPEC v0.13 §9.3: own CA of a self-hosted server (PEM).
    server_ca: Annotated[str, StringConstraints(max_length=8192)] | None = None
    claim_token: ClaimToken | None = Field(default=None, repr=False)
    wifi: SetupWifi | None = None
    wifi_country: Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")] | None = None
    ssh_authorized_keys: Annotated[list[SshKey], Field(max_length=5)] = Field(
        default_factory=list[SshKey]
    )
