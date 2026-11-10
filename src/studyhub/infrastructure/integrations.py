from typing import Any

import httpx

from studyhub.infrastructure.http_json import fetch_json


class IntegrationClient:
    """Server-configured outbound adapter. No user-supplied URLs or redirects."""

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def get_capabilities(self) -> dict[str, Any]:
        return await fetch_json(self.client, "GET", "capabilities")
