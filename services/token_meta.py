from __future__ import annotations
import httpx
from bot.config import settings

DEX_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens/{mint}"


def _f(v):
    try:
        return float(v)
    except Exception:
        return 0.0


async def _fetch_groypad_meta(address: str) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get(settings.GROYPAD_API_URL)
            r.raise_for_status()
            data = r.json()
        for t in data if isinstance(data, list) else []:
            if (t.get("meme_address") or "").lower() == address.lower():
                return {
                    "name": t.get("name") or t.get("ticker") or address[:6],
                    "symbol": t.get("ticker") or t.get("name") or address[:6],
                    "priceUsd": None,
                    "liquidityUsd": None,
                    "mcapUsd": _f(t.get("market_cap")) if t.get("market_cap") is not None else None,
                    "dexUrl": f"https://groypfi.io/launchpad/{address}",
                    "image": t.get("image_url"),
                    "progress": _f(t.get("progress")),
                    "platform": "groypad",
                    "telegram": t.get("telegram"),
                }
    except Exception:
        return None
    return None


async def fetch_token_meta(mint: str, ton_client=None) -> dict:
    base = {"name": mint[:6], "symbol": mint[:6], "priceUsd": None, "liquidityUsd": None, "mcapUsd": None, "dexUrl": f"https://tonviewer.com/{mint}", "progress": None, "platform": "ton"}

    groy = await _fetch_groypad_meta(mint)
    if groy:
        base.update(groy)

    if ton_client is not None:
        try:
            j = await ton_client.get_jetton(mint)
            if j:
                meta = j.get("metadata") or {}
                base.update({
                    "name": meta.get("name") or base["name"],
                    "symbol": meta.get("symbol") or base["symbol"],
                    "image": meta.get("image") or base.get("image"),
                })
        except Exception:
            pass

    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get(DEX_TOKEN_URL.format(mint=mint))
            r.raise_for_status()
            data = r.json()
        pairs = [p for p in (data.get("pairs") or []) if (p.get("chainId") or "").lower() in {"ton", "the-open-network"} or True]
        if pairs:
            pairs.sort(key=lambda p: (1 if (p.get("marketCap") is not None or p.get("fdv") is not None) else 0, _f((p.get("liquidity") or {}).get("usd"))), reverse=True)
            p = pairs[0]
            token = p.get("baseToken") or {}
            mcap_val = p.get("marketCap") if p.get("marketCap") not in (None, "", 0, "0") else p.get("fdv")
            base.update({
                "name": token.get("name") or base["name"],
                "symbol": token.get("symbol") or base["symbol"],
                "priceUsd": _f(p.get("priceUsd")) if p.get("priceUsd") is not None else base.get("priceUsd"),
                "liquidityUsd": _f((p.get("liquidity") or {}).get("usd")) if (p.get("liquidity") or {}).get("usd") is not None else base.get("liquidityUsd"),
                "mcapUsd": _f(mcap_val) if mcap_val not in (None, "") else base.get("mcapUsd"),
                "dexUrl": p.get("url") or base.get("dexUrl"),
            })
    except Exception:
        pass
    return base
