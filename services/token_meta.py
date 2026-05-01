from __future__ import annotations
import httpx

DEX_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens/{mint}"
TONAPI_JETTON = "https://tonapi.io/v2/jettons/{mint}"


def _f(v):
    try:
        return float(v)
    except Exception:
        return 0.0

async def fetch_token_meta(mint: str) -> dict:
    # Prefer DexScreener because it gives price, chart URL, liquidity and MCAP.
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get(DEX_TOKEN_URL.format(mint=mint))
            r.raise_for_status()
            data = r.json()
        pairs = [p for p in (data.get("pairs") or []) if (p.get("chainId") or "").lower() == "ton"]
        if pairs:
            pairs.sort(key=lambda p: (_f((p.get("liquidity") or {}).get("usd")), _f(p.get("fdv") or p.get("marketCap"))), reverse=True)
            p = pairs[0]
            base = p.get("baseToken") or {}
            quote = p.get("quoteToken") or {}
            token = base if (base.get("address") == mint or mint in str(base)) else base or quote
            name = token.get("name") or token.get("symbol") or mint[:6]
            symbol = token.get("symbol") or name
            mcap_val = p.get("marketCap") if p.get("marketCap") not in (None, "", 0, "0") else p.get("fdv")
            return {
                "name": name,
                "symbol": symbol,
                "priceUsd": _f(p.get("priceUsd")) if p.get("priceUsd") is not None else None,
                "liquidityUsd": _f((p.get("liquidity") or {}).get("usd")),
                "mcapUsd": _f(mcap_val) if mcap_val not in (None, "") else None,
                "dexUrl": p.get("url"),
                "gtUrl": None,
            }
    except Exception:
        pass

    # Fallback to TonAPI jetton metadata.
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get(TONAPI_JETTON.format(mint=mint))
            r.raise_for_status()
            data = r.json()
        meta = data.get("metadata") or data
        name = meta.get("name") or meta.get("symbol") or mint[:6]
        symbol = meta.get("symbol") or name
        return {"name": name, "symbol": symbol, "priceUsd": None, "liquidityUsd": None, "mcapUsd": None, "dexUrl": f"https://tonviewer.com/{mint}", "gtUrl": None}
    except Exception:
        return {"name": mint[:6], "symbol": mint[:6], "priceUsd": None, "liquidityUsd": None, "mcapUsd": None, "dexUrl": f"https://tonviewer.com/{mint}", "gtUrl": None}
