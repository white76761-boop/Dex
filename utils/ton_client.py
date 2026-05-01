from __future__ import annotations

import time
from typing import Any, Optional
import httpx

TON_NATIVE = "EQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAM9c"
TON_NATIVE_ALT = "ton"
TONAPI_BASE = "https://tonapi.io/v2"


def _as_list(payload: Any, keys: tuple[str, ...] = ("items", "events", "pools", "data", "result")) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in keys:
            v = payload.get(k)
            if isinstance(v, list):
                return v
        result = payload.get("result")
        if isinstance(result, dict):
            return _as_list(result, keys)
    return []


def _first(*vals):
    for v in vals:
        if v not in (None, "", [], {}):
            return v
    return None


def _num(v, decimals: int | None = None) -> float:
    try:
        if isinstance(v, str) and v.strip() == "":
            return 0.0
        f = float(v or 0)
        if decimals is not None and abs(f) > 10 ** max(6, decimals):
            f = f / (10 ** decimals)
        return f
    except Exception:
        return 0.0


class TonClient:
    """Small async client used by the BazaTon bot.

    It prefers TonAPI account events for live swap detection and also uses public
    STON.fi/DeDust/DexScreener endpoints to discover pools and metadata.
    """

    def __init__(self, api_key: str = "", timeout: float = 20.0):
        self.api_key = api_key or ""
        self.client = httpx.AsyncClient(timeout=timeout, headers=self._headers())
        self._pool_cache: dict[str, tuple[float, list[dict]]] = {}

    def _headers(self) -> dict[str, str]:
        h = {"Accept": "application/json", "User-Agent": "BazaTonBuyBot/1.0"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    async def get_json(self, url: str, **kwargs) -> Any:
        r = await self.client.get(url, **kwargs)
        r.raise_for_status()
        return r.json()

    async def post_json(self, url: str, payload: dict, **kwargs) -> Any:
        r = await self.client.post(url, json=payload, **kwargs)
        r.raise_for_status()
        return r.json()

    async def get_account_events(self, address: str, limit: int = 30) -> list[dict]:
        url = f"{TONAPI_BASE}/accounts/{address}/events"
        try:
            data = await self.get_json(url, params={"limit": limit})
            return _as_list(data, ("events", "items", "data", "result"))
        except Exception:
            return []

    async def get_account_transactions(self, address: str, limit: int = 20) -> list[dict]:
        url = f"{TONAPI_BASE}/blockchain/accounts/{address}/transactions"
        try:
            data = await self.get_json(url, params={"limit": limit})
            return _as_list(data, ("transactions", "items", "data", "result"))
        except Exception:
            return []

    async def discover_pools(self, token_address: str) -> list[dict]:
        cached = self._pool_cache.get(token_address)
        now = time.time()
        if cached and now - cached[0] < 10 * 60:
            return cached[1]

        pools: dict[str, dict] = {}

        # DexScreener is useful for finding pair/pool addresses across TON DEXes.
        try:
            data = await self.get_json(f"https://api.dexscreener.com/latest/dex/tokens/{token_address}")
            for p in data.get("pairs") or []:
                chain = (p.get("chainId") or "").lower()
                dex_id = (p.get("dexId") or "").lower()
                pair_addr = p.get("pairAddress") or p.get("pair_address")
                if chain == "ton" and pair_addr and ("ston" in dex_id or "dedust" in dex_id):
                    pools[pair_addr] = {"address": pair_addr, "dex": "dedust" if "dedust" in dex_id else "stonfi", "url": p.get("url")}
        except Exception:
            pass

        # DeDust exposes available pools and latest trades publicly.
        try:
            data = await self.get_json("https://api.dedust.io/v2/pools")
            for p in _as_list(data):
                blob = str(p)
                addr = _first(p.get("address"), p.get("pool"), p.get("poolAddress"), p.get("contractAddress")) if isinstance(p, dict) else None
                if addr and token_address in blob:
                    pools[addr] = {"address": addr, "dex": "dedust", "raw": p}
        except Exception:
            pass

        # STON.fi REST API supports pool queries; use both query and list fallbacks.
        try:
            data = await self.post_json("https://api.ston.fi/v1/pools/query", {"search_term": token_address, "limit": 20})
            for p in _as_list(data):
                addr = _first(p.get("address"), p.get("pool_address"), p.get("poolAddress"), p.get("lp_account_address")) if isinstance(p, dict) else None
                if addr:
                    pools[addr] = {"address": addr, "dex": "stonfi", "raw": p}
        except Exception:
            pass

        found = list(pools.values())[:8]
        self._pool_cache[token_address] = (now, found)
        return found

    async def latest_dedust_trades(self, pool: str, page_size: int = 20) -> list[dict]:
        try:
            data = await self.get_json(f"https://api.dedust.io/v2/pools/{pool}/trades", params={"page_size": page_size})
            return _as_list(data)
        except Exception:
            return []

    async def close(self):
        await self.client.aclose()
