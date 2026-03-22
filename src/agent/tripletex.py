import logging

import httpx

logger = logging.getLogger("agent.tripletex")


class TripletexClient:
    """Thin async HTTP client for the Tripletex API proxy."""

    def __init__(self, base_url: str, session_token: str):
        self.base_url = base_url.rstrip("/")
        self.session_token = session_token
        self.call_count = 0
        self.write_count = 0
        self.error_count = 0
        self._auth = ("0", session_token)
        self.token_dead = False
        self._http = httpx.AsyncClient(auth=self._auth, timeout=30.0)

    async def close(self) -> None:
        await self._http.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()

    async def get(self, endpoint: str, params: dict | None = None) -> dict:
        return await self._request("GET", endpoint, params=params)

    async def post(self, endpoint: str, payload: dict | None = None, params: dict | None = None) -> dict:
        return await self._request("POST", endpoint, json=payload, params=params)

    async def put(self, endpoint: str, payload: dict | None = None, params: dict | None = None) -> dict:
        return await self._request("PUT", endpoint, json=payload, params=params)

    async def delete(self, endpoint: str, params: dict | None = None) -> dict:
        return await self._request("DELETE", endpoint, params=params)

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: dict | None = None,
        json: dict | None = None,
    ) -> dict:
        if self.token_dead:
            logger.warning("[API SKIP] %s %s — token already expired, skipping", method, endpoint)
            return {"error": "Token expired (circuit breaker). Stop making API calls.", "_token_dead": True}

        url = f"{self.base_url}{endpoint}"
        self.call_count += 1
        if method != "GET":
            self.write_count += 1

        logger.info("[API %s] %s", method, endpoint)
        if params:
            logger.info("  params: %s", params)
        if json:
            logger.info("  payload: %s", json)

        try:
            response = await self._http.request(method, url, params=params, json=json)
        except httpx.TimeoutException:
            self.error_count += 1
            logger.error("[API TIMEOUT] %s %s", method, endpoint)
            return {"error": f"Request timed out: {method} {endpoint}", "status": 0}
        except httpx.HTTPError as exc:
            self.error_count += 1
            logger.error("[API ERROR] %s %s → %s", method, endpoint, exc)
            return {"error": f"HTTP error: {exc}", "status": 0}

        if response.status_code >= 400:
            self.error_count += 1
            logger.error(
                "[API %d] %s %s → %s",
                response.status_code,
                method,
                endpoint,
                response.text[:500],
            )
            if response.status_code == 403:
                try:
                    body = response.json()
                    msg = str(body.get("error", "")).lower()
                    if "expired" in msg or "invalid" in msg:
                        self.token_dead = True
                        logger.error("[CIRCUIT BREAKER] Token expired/invalid — all future API calls will be skipped")
                except Exception:
                    pass
        else:
            logger.info("[API %d] %s %s OK", response.status_code, method, endpoint)

        try:
            return response.json()
        except Exception:
            return {"error": f"Non-JSON response ({response.status_code})", "status": response.status_code}
