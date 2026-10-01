"""Setup files: one-time claim tokens (SPEC v0.14 §7.1, §9.7).

An admin prepares a box: the server creates a token for the household and shows it once; the
browser writes it into ``myboxi-setup.json`` together with the Wi-Fi settings, which never
reach the server. The box sends the token with its pairing start and is claimed at once.
Only the token's SHA-256 is stored.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from myboxi_server.auth.tokens import hash_token, new_token
from myboxi_server.domain.authz import Perm, TenantContext
from myboxi_server.domain.errors import InvalidInputError, NotFoundError
from myboxi_server.models import ClaimToken

TOKEN_TTL = dt.timedelta(days=7)
MAX_OPEN = 10
State = Literal["waiting", "used", "expired"]


@dataclass(frozen=True)
class NewToken:
    token: str  # shown once, never stored
    row: ClaimToken


async def create(db: AsyncSession, ctx: TenantContext, *, name: str) -> NewToken:
    ctx.require(Perm.DEVICE_CLAIM)
    name = name.strip()
    if not 1 <= len(name) <= 64:
        raise InvalidInputError("Bitte einen Namen mit höchstens 64 Zeichen angeben.")
    open_tokens = await db.scalar(
        select(func.count())
        .select_from(ClaimToken)
        .where(
            ClaimToken.tenant_id == ctx.tenant_id,
            ClaimToken.used_at.is_(None),
            ClaimToken.expires_at > func.now(),
        )
    )
    if (open_tokens or 0) >= MAX_OPEN:
        raise InvalidInputError(
            f"Es gibt schon {MAX_OPEN} offene Einrichtungsdateien. Bitte eine davon verwenden "
            "oder ein paar Tage warten, bis sie ablaufen."
        )
    token = new_token()
    row = ClaimToken(
        tenant_id=ctx.tenant_id,
        token_hash=hash_token(token),
        device_name=name,
        created_by=ctx.user_id,
        expires_at=dt.datetime.now(dt.UTC) + TOKEN_TTL,
    )
    db.add(row)
    await db.flush()
    return NewToken(token, row)


async def get(db: AsyncSession, ctx: TenantContext, token_id: uuid.UUID) -> ClaimToken:
    ctx.require(Perm.DEVICE_CLAIM)
    row = await db.scalar(
        select(ClaimToken).where(ClaimToken.id == token_id, ClaimToken.tenant_id == ctx.tenant_id)
    )
    if row is None:
        raise NotFoundError()
    return row


def state(row: ClaimToken, now: dt.datetime) -> State:
    if row.used_at is not None:
        return "used"
    return "expired" if row.expires_at <= now else "waiting"
