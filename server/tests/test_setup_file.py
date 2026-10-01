"""Setup files (SPEC v0.14 §7.1, §9.7): an admin prepares a box in the web app; the box pairs
itself with the household's one-time token, without a spoken code."""

from __future__ import annotations

import datetime as dt
import html
import json
import re
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI
from sqlalchemy import select, update

from myboxi_protocol.errors import ErrorCode, ErrorResponse
from myboxi_protocol.pairing import PairingClaimed
from myboxi_protocol.setup_file import SetupFile
from myboxi_server.domain import claim_tokens, retention
from myboxi_server.models import ClaimToken, Device, Membership
from myboxi_server.models.enums import Role
from myboxi_server.settings import Settings

from .helpers import add_member, login, make_tenant, owner_ctx, sessionmaker_of

POLL = "/api/v1/pairing/poll"
DATA_SETUP = re.compile(r'data-setup="([^"]+)"')


def https(app: FastAPI, settings: Settings, **extra: object) -> None:
    app.state.settings = settings.model_copy(
        update={"base_url": "https://myboxi.example.org", **extra}
    )


async def prepare(client: httpx.AsyncClient, tid: uuid.UUID, csrf: str, name: str) -> SetupFile:
    page = await client.get(f"/t/{tid}/boxes/prepare")
    assert page.status_code == 200
    r = await client.post(f"/t/{tid}/boxes/prepare", data={"csrf_token": csrf, "name": name})
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"  # the token is shown once
    m = DATA_SETUP.search(r.text)
    assert m is not None
    return SetupFile.model_validate(json.loads(html.unescape(m.group(1))))


async def start(client: httpx.AsyncClient, token: str | None) -> httpx.Response:
    body: dict[str, object] = {"device_id": str(uuid.uuid4()), "hw_model": "rpi-zero2w",
                               "agent_version": "0.8.0", "pairing_key": "K" * 43}  # fmt: skip
    if token is not None:
        body["claim_token"] = token
    return await client.post("/api/v1/pairing/start", json=body)


def error_code(r: httpx.Response) -> ErrorCode:
    return ErrorResponse.model_validate_json(r.content).error.code


async def test_a_prepared_box_pairs_itself(
    app: FastAPI, client: httpx.AsyncClient, settings: Settings
) -> None:
    https(app, settings)
    t = await make_tenant(app)
    csrf = await login(client, t.owner_email)
    setup = await prepare(client, t.tenant_id, csrf, "Kinderzimmer")
    assert setup.server_url == "https://myboxi.example.org"
    assert setup.wifi is None  # Wi-Fi is added in the browser, never sent to the server
    assert setup.claim_token is not None
    r = await start(client, setup.claim_token)
    assert r.status_code == 200, r.text
    poll = await client.get(POLL, params={"poll_token": r.json()["poll_token"]})
    assert poll.status_code == 200  # claimed already: no code to type
    claimed = PairingClaimed.model_validate_json(poll.content)
    assert claimed.tenant_id == t.tenant_id
    async with sessionmaker_of(app)() as db:
        token_row = await db.scalar(select(ClaimToken).where(ClaimToken.tenant_id == t.tenant_id))
        assert token_row is not None
        assert token_row.used_at is not None
        assert setup.claim_token.encode() not in token_row.token_hash  # only a hash
        device = await db.get(Device, token_row.device_id)
        assert device is not None
        assert (device.name, device.tenant_id) == ("Kinderzimmer", t.tenant_id)
    status = await client.get(f"/t/{t.tenant_id}/boxes/prepare/{token_row.id}")
    assert "ist verbunden" in status.text
    assert f"/boxes/{device.id}/setup" in status.text
    # once only
    again = await start(client, setup.claim_token)
    assert again.status_code == 403
    assert error_code(again) == ErrorCode.CLAIM_INVALID


async def test_unknown_expired_and_orphaned_tokens_are_refused(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    t = await make_tenant(app)
    ctx = await owner_ctx(app, t.tenant_id)
    async with sessionmaker_of(app)() as db:
        expired = await claim_tokens.create(db, ctx, name="Alt")
        expired.row.expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)
        demoted = await claim_tokens.create(db, ctx, name="Herabgestuft")
        await db.commit()
    for token in ("x" * 43, expired.token):
        r = await start(client, token)
        assert (r.status_code, error_code(r)) == (403, ErrorCode.CLAIM_INVALID)
    # the admin who made the file may no longer pair boxes: the file is worthless
    async with sessionmaker_of(app)() as db:
        await db.execute(
            update(Membership).where(Membership.user_id == ctx.user_id).values(role=Role.VIEWER)
        )
        await db.commit()
    r = await start(client, demoted.token)
    assert (r.status_code, error_code(r)) == (403, ErrorCode.CLAIM_INVALID)


async def test_only_admins_prepare_and_the_number_is_limited(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    t = await make_tenant(app)
    contributor = await add_member(app, t.tenant_id, Role.CONTRIBUTOR)
    csrf = await login(client, contributor)
    r = await client.post(f"/t/{t.tenant_id}/boxes/prepare", data={"csrf_token": csrf, "name": "x"})
    assert r.status_code == 403
    await client.post("/logout", data={"csrf_token": csrf})
    csrf = await login(client, t.owner_email)
    for i in range(claim_tokens.MAX_OPEN):
        r = await client.post(f"/t/{t.tenant_id}/boxes/prepare",
                              data={"csrf_token": csrf, "name": f"Box {i}"})  # fmt: skip
        assert r.status_code == 200
    r = await client.post(f"/t/{t.tenant_id}/boxes/prepare",
                          data={"csrf_token": csrf, "name": "zu viel"})  # fmt: skip
    assert r.status_code == 400
    assert "offene Einrichtungsdateien" in r.text


async def test_a_self_hosted_server_puts_its_ca_into_the_file(
    app: FastAPI, client: httpx.AsyncClient, settings: Settings, tmp_path: Path
) -> None:
    ca = tmp_path / "myboxi-ca.pem"
    ca.write_text("-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n")
    https(app, settings, box_ca_file=ca)
    t = await make_tenant(app)
    csrf = await login(client, t.owner_email)
    r = await client.post(f"/t/{t.tenant_id}/boxes/prepare", data={"csrf_token": csrf, "name": "A"})
    m = DATA_SETUP.search(r.text)
    assert m is not None
    assert json.loads(html.unescape(m.group(1)))["server_ca"] == ca.read_text()


async def test_the_wizard_offers_the_setup_file(app: FastAPI, client: httpx.AsyncClient) -> None:
    t = await make_tenant(app)
    await login(client, t.owner_email)
    page = await client.get(f"/t/{t.tenant_id}/setup")
    assert f'href="/t/{t.tenant_id}/boxes/prepare"' in page.text


async def test_old_tokens_are_purged(app: FastAPI) -> None:
    t = await make_tenant(app)
    ctx = await owner_ctx(app, t.tenant_id)
    async with sessionmaker_of(app)() as db:
        await claim_tokens.create(db, ctx, name="Alt")
        await db.commit()
        later = dt.datetime.now(dt.UTC) + claim_tokens.TOKEN_TTL + dt.timedelta(days=31)
        counts = await retention.purge_expired(db, later)
        assert counts["claim_token"] == 1
