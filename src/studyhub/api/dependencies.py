from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import jwt
from fastapi import Depends, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from studyhub.core.context import actor_id_context, school_id_context
from studyhub.core.security import decode_access
from studyhub.models import AuthSession, Membership, School, User

bearer = HTTPBearer(auto_error=False)


async def database(request: Request):
    async with request.app.state.sessions() as session:
        # Services own commit boundaries. Uncommitted work always rolls back on close.
        yield session


@dataclass
class Principal:
    user: User
    session_id: UUID
    school: School | None = None
    membership: Membership | None = None

    @property
    def school_id(self) -> UUID:
        if self.school is None:
            raise RuntimeError("School context is required")
        return self.school.id


async def bind_school(db: AsyncSession, principal: Principal, school_id: UUID) -> Principal:
    row = (
        await db.execute(
            select(School, Membership)
            .join(Membership)
            .where(
                School.id == school_id,
                School.is_active.is_(True),
                Membership.user_id == principal.user.id,
                Membership.is_active.is_(True),
            )
        )
    ).first()
    if not row:
        raise HTTPException(403, "No active membership in this school")
    principal.school, principal.membership = row
    school_id_context.set(str(school_id))
    db.info["school_id"] = str(school_id)
    await db.execute(
        text("SELECT set_config('app.school_id', :school, true)"), {"school": str(school_id)}
    )
    return principal


async def load_principal(db: AsyncSession, user_id: UUID, session_id: UUID) -> Principal | None:
    user = await db.scalar(
        select(User)
        .join(AuthSession, AuthSession.user_id == User.id)
        .where(
            User.id == user_id,
            User.is_active.is_(True),
            AuthSession.id == session_id,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > datetime.now(UTC),
        )
    )
    return Principal(user, session_id) if user else None


async def current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: AsyncSession = Depends(database),
) -> Principal:
    error = HTTPException(
        401, "Invalid or expired credentials", headers={"WWW-Authenticate": "Bearer"}
    )
    if not credentials:
        raise error
    try:
        claims = decode_access(request.app.state.settings, credentials.credentials)
    except (jwt.InvalidTokenError, ValueError, TypeError) as exc:
        raise error from exc
    principal = await load_principal(db, UUID(claims["sub"]), UUID(claims["sid"]))
    if not principal:
        raise error
    actor_id_context.set(str(principal.user.id))
    return principal


async def school_context(
    school_id: UUID = Header(alias="X-School-ID"),
    principal: Principal = Depends(current_user),
    db: AsyncSession = Depends(database),
) -> Principal:
    return await bind_school(db, principal, school_id)


async def administrator(principal: Principal = Depends(school_context)) -> Principal:
    if principal.membership.role != "admin":
        raise HTTPException(403, "Administrator role required")
    return principal


async def educator(principal: Principal = Depends(school_context)) -> Principal:
    if principal.membership.role not in {"admin", "teacher"}:
        raise HTTPException(403, "Teacher or administrator role required")
    return principal


async def platform_administrator(principal: Principal = Depends(current_user)) -> Principal:
    if not principal.user.is_platform_admin:
        raise HTTPException(403, "Platform administrator required")
    return principal
