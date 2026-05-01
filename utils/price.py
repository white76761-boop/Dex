from __future__ import annotations
import httpx

async def _fetch_json(url: str, timeout: float = 10.0):
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.get(url)
        r.raise_for_status()
        return r.json()

async def ton_usd(price_url: str | None = None) -> float:
    # CoinGecko id for Toncoin is the-open-network.
    urls = [
        price_url or "https://api.coingecko.com/api/v3/simple/price?ids=the-open-network&vs_currencies=usd",
        "https://tonapi.io/v2/rates?tokens=ton&currencies=usd",
    ]
    for url in urls:
        try:
            data = await _fetch_json(url)
            if "the-open-network" in data:
                return float(data["the-open-network"]["usd"])
            if "rates" in data:
                return float(data["rates"]["TON"]["prices"]["USD"])
        except Exception:
            continue
    return 0.0
