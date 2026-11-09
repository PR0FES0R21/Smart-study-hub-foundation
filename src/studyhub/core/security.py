import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError

from studyhub.core.config import Settings

password_hasher = PasswordHasher()
# Used on unknown accounts too, reducing username timing differences.
DUMMY_HASH = password_hasher.hash("not-a-real-user-password")


def verify_password(encoded: str, password: str) -> bool:
    try:
        return password_hasher.verify(encoded, password)
    except VerificationError:
        return False


def opaque_token() -> str:
    return secrets.token_urlsafe(48)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def access_token(settings: Settings, user_id: UUID, session_id: UUID) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user_id),
            "sid": str(session_id),
            "type": "access",
            "iat": now,
            "nbf": now,
            "exp": now + timedelta(seconds=settings.access_token_seconds),
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
        },
        settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )


def decode_access(settings: Settings, token: str) -> dict:
    claims = jwt.decode(
        token,
        settings.jwt_secret.get_secret_value(),
        algorithms=["HS256"],
        audience=settings.jwt_audience,
        issuer=settings.jwt_issuer,
        options={"require": ["exp", "iat", "nbf", "sub", "sid", "type"]},
    )
    if claims["type"] != "access":
        raise jwt.InvalidTokenError("Wrong token type")
    UUID(claims["sub"])
    UUID(claims["sid"])
    return claims
