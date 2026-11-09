import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studyhub.core.config import Settings
from studyhub.core.errors import AuthenticationError
from studyhub.core.security import (
    DUMMY_HASH,
    access_token,
    opaque_token,
    token_hash,
    verify_password,
)
from studyhub.models import AuthSession, RefreshToken, User
from studyhub.schemas import Tokens


def issue_tokens(settings: Settings, user: User, session: AuthSession, refresh: str) -> Tokens:
    return Tokens(
        access_token=access_token(settings, user.id, session.id),
        refresh_token=refresh,
        expires_in=settings.access_token_seconds,
    )


async def login(db: AsyncSession, settings: Settings, email: str, password: str) -> Tokens:
    user = await db.scalar(select(User).where(User.email == email.lower()))
    valid = await asyncio.to_thread(
        verify_password, user.password_hash if user and user.password_hash else DUMMY_HASH, password
    )
    if not user or not valid or not user.is_active:
        raise AuthenticationError("Invalid email or password")
    return await create_session(db, settings, user)


async def create_session(db: AsyncSession, settings: Settings, user: User) -> Tokens:
    session = AuthSession(
        user_id=user.id, expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_days)
    )
    db.add(session)
    await db.flush()
    refresh = opaque_token()
    db.add(RefreshToken(digest=token_hash(refresh), session_id=session.id))
    await db.commit()
    return issue_tokens(settings, user, session, refresh)


async def rotate_refresh(db: AsyncSession, settings: Settings, raw: str) -> Tokens:
    token = await db.get(RefreshToken, token_hash(raw))
    error = AuthenticationError("Invalid or expired refresh token")
    if not token:
        raise error
    # Serialize rotations for the session, including replay of an older token.
    session = await db.scalar(
        select(AuthSession).where(AuthSession.id == token.session_id).with_for_update()
    )
    await db.refresh(token)
    now = datetime.now(UTC)
    if not session or session.revoked_at or session.expires_at <= now:
        raise error
    if token.used_at:
        session.revoked_at = now
        await db.commit()  # Must persist revocation even though the response is 401.
        raise error
    user = await db.get(User, session.user_id)
    if not user or not user.is_active:
        raise error
    token.used_at = now
    refresh = opaque_token()
    db.add(RefreshToken(digest=token_hash(refresh), session_id=session.id))
    await db.commit()
    return issue_tokens(settings, user, session, refresh)
