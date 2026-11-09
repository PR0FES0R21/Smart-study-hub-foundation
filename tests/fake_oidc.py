"""Local protocol fixture only: not included in the application image."""

import base64
import hashlib
import json
import os
import secrets
import time
from urllib.parse import parse_qs, urlencode

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from jwt.algorithms import RSAAlgorithm

app = FastAPI()
key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
codes = {}
issuer = os.environ["TEST_OIDC_ISSUER"]


@app.get("/.well-known/openid-configuration")
def discovery():
    return {
        "issuer": issuer,
        "authorization_endpoint": issuer + "/authorize",
        "token_endpoint": issuer + "/token",
        "jwks_uri": issuer + "/jwks",
        "code_challenge_methods_supported": ["S256"],
    }


@app.get("/jwks")
def jwks():
    return {
        "keys": [
            {
                **json.loads(RSAAlgorithm.to_jwk(key.public_key())),
                "kid": "fixture-key",
                "use": "sig",
                "alg": "RS256",
            }
        ]
    }


@app.get("/authorize")
def authorize(request: Request):
    data = dict(request.query_params)
    code = secrets.token_urlsafe(32)
    codes[code] = data
    return RedirectResponse(
        data["redirect_uri"] + "?" + urlencode({"state": data["state"], "code": code}),
        status_code=302,
    )


@app.post("/token")
async def token(request: Request):
    form = {k: v[0] for k, v in parse_qs((await request.body()).decode()).items()}
    data = codes.pop(form.get("code"), None)
    if not data:
        raise HTTPException(400)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    if (
        challenge != data["code_challenge"]
        or form["redirect_uri"] != data["redirect_uri"]
        or form["client_id"] != data["client_id"]
    ):
        raise HTTPException(400)
    claims = {
        "iss": issuer,
        "sub": "admin-subject",
        "aud": data["client_id"],
        "iat": int(time.time()),
        "exp": int(time.time()) + 120,
        "nonce": data["nonce"],
        "email": "admin@example.org",
        "role": "admin",
        "school_id": "untrusted-provider-school",
    }
    mode = data.get("mode")
    if mode == "nonce":
        claims["nonce"] = "invalid"
    if mode == "audience":
        claims["aud"] = "another-application"
    if mode == "issuer":
        claims["iss"] = "https://evil.example.org"
    if mode == "unlinked":
        claims["sub"] = "someone-else-with-the-same-email"
    if mode == "expired":
        claims["exp"] = int(time.time()) - 60
    token = jwt.encode(claims, key, algorithm="RS256", headers={"kid": "fixture-key"})
    if mode == "unsigned":
        token = jwt.encode(claims, "", algorithm="none", headers={"kid": "fixture-key"})
    return {
        "id_token": token,
        "access_token": "provider-token-never-forwarded",
        "token_type": "Bearer",
    }
