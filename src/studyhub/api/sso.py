from uuid import UUID

import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from studyhub.api.dependencies import Principal, database, platform_administrator
from studyhub.core.errors import AuthenticationError
from studyhub.infrastructure.redis import rate_limit
from studyhub.models import ExternalIdentity, User
from studyhub.schemas import Input, Tokens

router = APIRouter(prefix="/api/v1/auth/sso", tags=["sso"])
COOKIE = "studyhub_oidc_browser"
COOKIE_PATH = "/api/v1/auth/sso"


class IdentityLink(Input):
    user_id: UUID
    subject: str = Field(min_length=1, max_length=255)


@router.post("/identities", status_code=201)
async def link_identity(
    payload: IdentityLink,
    request: Request,
    _: Principal = Depends(platform_administrator),
    db: AsyncSession = Depends(database),
):
    issuer = request.app.state.settings.oidc_issuer
    if not issuer:
        raise HTTPException(503, "SSO is not configured")
    if not await db.get(User, payload.user_id):
        raise HTTPException(404, "User not found")
    db.add(ExternalIdentity(issuer=issuer, subject=payload.subject, user_id=payload.user_id))
    await db.commit()
    return {"user_id": payload.user_id, "issuer": issuer, "subject": payload.subject}


@router.get("/start")
async def start(request: Request):
    state = request.app.state
    if not state.settings.oidc_issuer:
        raise HTTPException(503, "SSO is not configured")
    await rate_limit(state.control, state.settings.namespace, f"oidc:{request.client.host}", 20, 60)
    try:
        url, browser = await state.oidc.start()
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise HTTPException(503, "Identity provider unavailable") from exc
    response = RedirectResponse(url, status_code=302)
    response.set_cookie(
        COOKIE,
        browser,
        max_age=300,
        httponly=True,
        secure=state.settings.environment != "test",
        samesite="lax",
        path=COOKIE_PATH,
    )
    return response


@router.get("/callback", response_model=Tokens)
async def callback(
    request: Request,
    response: Response,
    code: str = Query(min_length=1, max_length=2048),
    state: str = Query(min_length=32, max_length=200),
    db: AsyncSession = Depends(database),
):
    if not request.app.state.settings.oidc_issuer:
        raise HTTPException(503, "SSO is not configured")
    try:
        tokens = await request.app.state.oidc.callback(
            db, code, state, request.cookies.get(COOKIE, "")
        )
    except (jwt.InvalidTokenError, ValueError, KeyError, TypeError) as exc:
        raise AuthenticationError("Invalid SSO response") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(503, "Identity provider unavailable") from exc
    response.delete_cookie(COOKIE, path=COOKIE_PATH)
    return tokens
