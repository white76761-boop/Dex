from __future__ import annotations
from utils.ton_rpc import TonClient


async def ton_usd(client: TonClient) -> float:
    try:
        data = await client.ton_rates()
        ton = (data.get("rates") or {}).get("TON") or (data.get("rates") or {}).get("ton") or {}
        prices = ton.get("prices") or {}
        return float(prices.get("USD") or prices.get("usd") or 0)
    except Exception:
        return 0.0

# Backwards-compatible alias used by older code paths.
async def sol_usd(*_args, **_kwargs) -> float:
    return 0.0
