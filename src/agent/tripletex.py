import logging

import httpx

logger = logging.getLogger("agent.tripletex")


class TripletexClient:
    """Thin async HTTP client for the Tripletex API proxy."""

    def __init__(self, base_url: str, session_token: str):
        self.base_url = base_url.rstrip("/")
        self.session_token = session_token
        self.call_count = 0
        self.error_count = 0
        self._auth = ("0", session_token)

    async def get(self, endpoint: str, params: dict | None = None) -> dict:
        return await self._request("GET", endpoint, params=params)

    async def post(self, endpoint: str, payload: dict | None = None, params: dict | None = None) -> dict:
        return await self._request("POST", endpoint, json=payload, params=params)

    async def put(self, endpoint: str, payload: dict | None = None, params: dict | None = None) -> dict:
        return await self._request("PUT", endpoint, json=payload, params=params)

    async def delete(self, endpoint: str) -> dict:
        return await self._request("DELETE", endpoint)

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: dict | None = None,
        json: dict | None = None,
    ) -> dict:
        url = f"{self.base_url}{endpoint}"
        self.call_count += 1

        logger.info("[API %s] %s", method, endpoint)
        if params:
            logger.info("  params: %s", params)
        if json:
            logger.info("  payload: %s", json)

        async with httpx.AsyncClient(auth=self._auth, timeout=30.0) as http:
            response = await http.request(method, url, params=params, json=json)

        if response.status_code >= 400:
            self.error_count += 1
            logger.error(
                "[API %d] %s %s → %s",
                response.status_code,
                method,
                endpoint,
                response.text[:500],
            )
        else:
            logger.info("[API %d] %s %s OK", response.status_code, method, endpoint)

        return response.json()
