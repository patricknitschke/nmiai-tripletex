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
        self.token_dead = False  # Circuit breaker: set on expired token 403
        self._http = httpx.AsyncClient(auth=self._auth, timeout=30.0)

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
        # Circuit breaker: don't waste time on a dead token
        if self.token_dead:
            logger.warning("[API SKIP] %s %s — token already expired, skipping", method, endpoint)
            return {"error": "Token expired (circuit breaker). Stop making API calls.", "_token_dead": True}

        url = f"{self.base_url}{endpoint}"
        self.call_count += 1

        logger.info("[API %s] %s", method, endpoint)
        if params:
            logger.info("  params: %s", params)
        if json:
            logger.info("  payload: %s", json)

        response = await self._http.request(method, url, params=params, json=json)

        if response.status_code >= 400:
            self.error_count += 1
            logger.error(
                "[API %d] %s %s → %s",
                response.status_code,
                method,
                endpoint,
                response.text[:500],
            )
            # Trip circuit breaker on expired proxy token
            if response.status_code == 403:
                try:
                    body = response.json()
                    if "expired" in str(body.get("error", "")).lower() or "invalid" in str(body.get("error", "")).lower():
                        self.token_dead = True
                        logger.error("[CIRCUIT BREAKER] Token expired/invalid — all future API calls will be skipped")
                except Exception:
                    pass
        else:
            logger.info("[API %d] %s %s OK", response.status_code, method, endpoint)

        try:
            return response.json()
        except Exception:
            # Non-JSON response (e.g. HTML 404 page)
            return {"error": f"Non-JSON response ({response.status_code})", "status": response.status_code}
