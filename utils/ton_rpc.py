from __future__ import annotations
import httpx
from typing import Any, Optional


class TonClient:
    def __init__(self, tonapi_base: str, tonapi_key: str = "", toncenter_base: str = "https://toncenter.com/api/v3", toncenter_key: str = "", timeout: float = 20.0):
        self.tonapi_base = tonapi_base.rstrip("/")
        self.tonapi_key = tonapi_key
        self.toncenter_base = toncenter_base.rstrip("/")
        self.toncenter_key = toncenter_key
        self.client = httpx.AsyncClient(timeout=timeout)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.tonapi_key}"} if self.tonapi_key else {}

    async def get_account_events(self, address: str, limit: int = 30, before_lt: int | None = None) -> list[dict]:
        params: dict[str, Any] = {"limit": limit}
        if before_lt:
            params["before_lt"] = before_lt
        r = await self.client.get(f"{self.tonapi_base}/accounts/{address}/events", params=params, headers=self._headers())
        r.raise_for_status()
        data = r.json()
        return data.get("events") or []

    async def get_event(self, event_id: str) -> Optional[dict]:
        r = await self.client.get(f"{self.tonapi_base}/events/{event_id}", headers=self._headers())
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    async def get_jetton(self, address: str) -> Optional[dict]:
        r = await self.client.get(f"{self.tonapi_base}/jettons/{address}", headers=self._headers())
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    async def ton_rates(self) -> dict:
        r = await self.client.get(f"{self.tonapi_base}/rates", params={"tokens": "ton", "currencies": "usd"}, headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def run_get_method(self, address: str, method: str, stack: list | None = None) -> Optional[dict]:
        headers = {"Content-Type": "application/json"}
        if self.toncenter_key:
            headers["X-API-Key"] = self.toncenter_key
        r = await self.client.post(
            f"{self.toncenter_base}/runGetMethod",
            json={"address": address, "method": method, "stack": stack or []},
            headers=headers,
        )
        if r.status_code >= 400:
            return None
        return r.json()

    async def close(self):
        await self.client.aclose()
