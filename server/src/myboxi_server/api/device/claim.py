"""``POST /api/v1/tenants/{tid}/devices/claim`` (SPEC §7.1 step 2).

A user endpoint: session cookie plus CSRF header, role >= admin.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from myboxi_protocol.errors import ErrorCode
from myboxi_protocol.pairing import ClaimRequest, ClaimResponse
from myboxi_server.api.device.errors import ApiError, rate_limited
from myboxi_server.api.web.deps import DbSession, csrf_protect, require
from myboxi_server.auth import ratelimit
from myboxi_server.domain import pairing
from myboxi_server.domain.authz import Perm, TenantContext

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", dependencies=[Depends(csrf_protect)])

ClaimCtx = Annotated[TenantContext, Depends(require(Perm.DEVICE_CLAIM))]


async def claim_with_limits(
    request: Request, db: DbSession, ctx: TenantContext, code: str, name: str
) -> ClaimResponse:
    """Shared by the JSON API and the web form. Every attempt counts (SPEC §7.1)."""
    engine = request.app.state.engine
    try:
        await ratelimit.hit(engine, f"claim:user:{ctx.user_id}", ratelimit.CLAIM_PER_USER)
        await ratelimit.hit(engine, f"claim:tenant:{ctx.tenant_id}", ratelimit.CLAIM_PER_TENANT)
        await ratelimit.check(engine, "claim:failed", ratelimit.CLAIM_FAILURES_GLOBAL)
    except ratelimit.RateLimitedError as exc:
        raise rate_limited(exc.retry_after) from None
    try:
        device = await pairing.claim(db, ctx, code=code, name=name)
    except pairing.CodeInvalidError as exc:
        await db.rollback()
        await ratelimit.hit(engine, "claim:failed", ratelimit.CLAIM_FAILURES_GLOBAL)
        log.warning("pairing code rejected", extra={"tenant_id": str(ctx.tenant_id)})
        raise ApiError(400, ErrorCode.CODE_INVALID, exc.message) from None
    except pairing.PairedElsewhereError as exc:
        await db.rollback()
        raise ApiError(409, ErrorCode.DEVICE_PAIRED_ELSEWHERE, exc.message) from None
    await db.commit()
    return ClaimResponse(device_id=device.id, name=device.name)


@router.post("/tenants/{tid}/devices/claim", response_model=ClaimResponse)
async def claim_device(
    request: Request, body: ClaimRequest, db: DbSession, ctx: ClaimCtx
) -> ClaimResponse:
    return await claim_with_limits(request, db, ctx, body.code, body.name)
