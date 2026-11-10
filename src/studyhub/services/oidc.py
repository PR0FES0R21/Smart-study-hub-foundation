"""OIDC authorization-code + PKCE adapter; local authorization stays in memberships."""

import base64
import hashlib
import hmac
import json
from urllib.parse import urlencode, urlsplit

import httpx
import jwt
from jwt.algorithms import RSAAlgorithm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studyhub.core.config import Settings
from studyhub.core.errors import AuthenticationError
from studyhub.core.security import opaque_token, token_hash
from studyhub.infrastructure.http_json import fetch_json
from studyhub.models import ExternalIdentity, User
from studyhub.services.auth import create_session


class OIDC:
    def __init__(self, settings: Settings, http: httpx.AsyncClient, control):
        self.settings, self.http, self.control = settings, http, control

    def endpoint(self, url: str) -> str:
        parsed = urlsplit(url)
        issuer = urlsplit(self.settings.oidc_issuer)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        allowed = {f"{issuer.scheme}://{issuer.netloc}", *self.settings.oidc_endpoint_origins}
        if origin not in allowed or parsed.username or parsed.password or parsed.fragment:
            raise AuthenticationError("Untrusted OIDC endpoint")
        return url

    async def discovery(self):
        if not self.settings.oidc_issuer:
            raise AuthenticationError("SSO is not configured")
        metadata = await fetch_json(
            self.http,
            "GET",
            self.settings.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration",
        )
        if metadata["issuer"] != self.settings.oidc_issuer or "S256" not in metadata.get(
            "code_challenge_methods_supported", []
        ):
            raise AuthenticationError("OIDC issuer or PKCE capability mismatch")
        for name in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            self.endpoint(metadata[name])
        return metadata

    async def start(self):
        metadata = await self.discovery()
        state, verifier, nonce, browser = [opaque_token() for _ in range(4)]
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        await self.control.set(
            f"{self.settings.namespace}:oidc:state:{token_hash(state)}",
            json.dumps({"verifier": verifier, "nonce": nonce, "browser": token_hash(browser)}),
            ex=300,
            nx=True,
        )
        url = (
            metadata["authorization_endpoint"]
            + "?"
            + urlencode(
                {
                    "response_type": "code",
                    "client_id": self.settings.oidc_client_id,
                    "redirect_uri": self.settings.oidc_redirect_uri,
                    "scope": "openid profile email",
                    "state": state,
                    "nonce": nonce,
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                }
            )
        )
        return url, browser

    async def callback(self, db: AsyncSession, code: str, state: str, browser: str):
        raw = await self.control.getdel(f"{self.settings.namespace}:oidc:state:{token_hash(state)}")
        if not raw:
            raise AuthenticationError("Invalid or expired SSO state")
        stored = json.loads(raw)
        if not hmac.compare_digest(stored["browser"], token_hash(browser)):
            raise AuthenticationError("SSO browser binding failed")
        metadata = await self.discovery()
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.settings.oidc_redirect_uri,
            "code_verifier": stored["verifier"],
        }
        auth = None
        if self.settings.oidc_client_secret:
            auth = httpx.BasicAuth(
                self.settings.oidc_client_id, self.settings.oidc_client_secret.get_secret_value()
            )
        else:
            form["client_id"] = self.settings.oidc_client_id
        payload = await fetch_json(
            self.http, "POST", metadata["token_endpoint"], data=form, auth=auth
        )
        token = payload["id_token"]
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
            raise AuthenticationError("Unsupported SSO signing key")
        jwks = await fetch_json(self.http, "GET", metadata["jwks_uri"])
        keys = [
            key
            for key in jwks["keys"]
            if key.get("kid") == header["kid"]
            and key.get("kty") == "RSA"
            and key.get("use", "sig") == "sig"
            and key.get("alg", "RS256") == "RS256"
        ]
        if len(keys) != 1:
            raise AuthenticationError("SSO signing key not found")
        claims = jwt.decode(
            token,
            RSAAlgorithm.from_jwk(keys[0]),
            algorithms=["RS256"],
            issuer=self.settings.oidc_issuer,
            audience=self.settings.oidc_client_id,
            options={"require": ["exp", "iat", "sub", "iss", "aud", "nonce"]},
        )
        if not isinstance(claims["sub"], str) or not 1 <= len(claims["sub"]) <= 255:
            raise AuthenticationError("Invalid SSO subject")
        if not isinstance(claims["nonce"], str) or not hmac.compare_digest(
            claims["nonce"], stored["nonce"]
        ):
            raise AuthenticationError("SSO nonce mismatch")
        if (
            isinstance(claims["aud"], list)
            and len(claims["aud"]) > 1
            and claims.get("azp") != self.settings.oidc_client_id
        ):
            raise AuthenticationError("SSO authorized party mismatch")
        if claims.get("azp", self.settings.oidc_client_id) != self.settings.oidc_client_id:
            raise AuthenticationError("SSO authorized party mismatch")
        user = await db.scalar(
            select(User)
            .join(ExternalIdentity)
            .where(
                ExternalIdentity.issuer == claims["iss"],
                ExternalIdentity.subject == claims["sub"],
                User.is_active.is_(True),
            )
        )
        if not user:
            # Never auto-link by matching email or accept role/school claims from a provider.
            raise AuthenticationError("SSO identity has not been provisioned")
        return await create_session(db, self.settings, user)
