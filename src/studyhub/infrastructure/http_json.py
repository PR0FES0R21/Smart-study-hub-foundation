import asyncio
import json

import httpx


async def fetch_json(
    client: httpx.AsyncClient, method: str, url: str, *, max_bytes: int = 262144, **kwargs
) -> dict:
    headers = dict(kwargs.pop("headers", {}) or {})
    headers["Accept-Encoding"] = "identity"
    try:
        async with asyncio.timeout(5):
            async with client.stream(method, url, headers=headers, **kwargs) as response:
                response.raise_for_status()
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise httpx.HTTPError("Compressed upstream JSON is unsupported")
                body = bytearray()
                async for chunk in response.aiter_raw():
                    if len(body) + len(chunk) > max_bytes:
                        raise httpx.HTTPError("Upstream JSON exceeds size limit")
                    body.extend(chunk)
    except TimeoutError as exc:
        raise httpx.TimeoutException("Upstream response deadline exceeded") from exc
    try:
        payload = json.loads(body)
    except (ValueError, RecursionError) as exc:
        raise httpx.HTTPError("Invalid upstream JSON") from exc
    if not isinstance(payload, dict):
        raise httpx.HTTPError("Upstream JSON must be an object")
    return payload
