from typing import Any

import httpx


class IntegrationClient:
    """Server-configured outbound adapter. No user-supplied URLs or redirects."""

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def get_capabilities(self) -> dict[str, Any]:
        response = await self.client.get("capabilities")
        response.raise_for_status()
        return response.json()
