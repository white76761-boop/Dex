from __future__ import annotations
import httpx
from typing import Any, Dict, List, Optional, Tuple
import time

STABLE_SYMBOLS = {"USDC", "USDT"}
WSOL_MINT = "So11111111111111111111111111111111111111112"

class HeliusClient:
    def __init__(self, api_key: str, timeout: float = 20.0):
        self.api_key = api_key
        self.base = "https://api.helius.xyz"
        self.client = httpx.AsyncClient(timeout=timeout)

    async def get_address_txs(self, address: str, limit: int = 20, before: str | None = None) -> list[dict]:
        # Enhanced transactions endpoint
        params = {"api-key": self.api_key}
        if before:
            params["before"] = before
        url = f"{self.base}/v0/addresses/{address}/transactions"
        r = await self.client.get(url, params=params, timeout=20.0)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict) and data.get("error"):
            raise RuntimeError(data["error"])
        # returns list newest->oldest
        return data[:limit]

    async def close(self):
        await self.client.aclose()

def _find_buy_in_tx(tx: dict, mint: str) -> Optional[dict]:
    token_transfers = tx.get("tokenTransfers") or []
    native_transfers = tx.get("nativeTransfers") or []
    fee_payer = tx.get("feePayer") or tx.get("signer")
    account_keys = tx.get("accountData") or []

    events = tx.get("events") or {}
    swap = events.get("swap") or {}

    def _fa(v: Any) -> float:
        try:
            return float(v or 0)
        except Exception:
            return 0.0

    def _norm_sym(sym: str | None, mint_addr: str | None = None) -> str:
        s = (sym or "").upper()
        if mint_addr == WSOL_MINT or s == "WSOL":
            return "SOL"
        return s or "TOKEN"

    def _collect_principals() -> list[str]:
        vals = []
        for v in [fee_payer, tx.get("signer")]:
            if v and v not in vals:
                vals.append(v)
        for item in account_keys:
            acct = item.get("account") or item.get("pubkey")
            if acct and acct not in vals:
                vals.append(acct)
        return vals

    def _owner_of(item: dict) -> str | None:
        for k in ("userAccount", "fromUserAccount", "toUserAccount", "tokenAccount", "fromTokenAccount", "toTokenAccount"):
            v = item.get(k)
            if v:
                return v
        return None

    def _net_native_spend(senders: list[str]) -> float:
        lamports = 0
        for nt in native_transfers:
            frm = nt.get("fromUserAccount")
            to = nt.get("toUserAccount")
            amt = int(nt.get("amount", 0) or 0)
            if amt <= 0:
                continue
            if frm in senders:
                lamports += amt
            if to in senders:
                lamports -= amt
        return max(0.0, lamports / 1_000_000_000)

    # Prefer Helius enhanced swap event first, but only trust the user-facing
    # input/output legs. Aggregators include route-internal hops that can be larger
    # than the real user input.
    try:
        token_inputs = swap.get("tokenInputs") or []
        token_outputs = swap.get("tokenOutputs") or []
        native_input = swap.get("nativeInput") or {}
        principals = _collect_principals()
        if token_outputs:
            output_candidates = []
            for item in token_outputs:
                if item.get("mint") != mint:
                    continue
                amount = _fa(item.get("tokenAmount") or item.get("amount"))
                if amount <= 0:
                    continue
                owner = _owner_of(item)
                score = 0
                if owner in principals:
                    score += 100
                if owner == fee_payer:
                    score += 20
                output_candidates.append((score, amount, owner, item))
            if output_candidates:
                _, amount, owner, out = sorted(output_candidates, key=lambda x: (x[0], x[1]), reverse=True)[0]
                buyer = owner or fee_payer
                input_candidates = []
                generic_candidates = []
                for item in token_inputs:
                    imint = item.get("mint")
                    if imint == mint:
                        continue
                    val = _fa(item.get("tokenAmount") or item.get("amount"))
                    if val <= 0:
                        continue
                    owner = _owner_of(item)
                    sym = _norm_sym(item.get("tokenSymbol") or item.get("symbol"), imint)
                    usd = _fa(item.get("usdValue") or item.get("amountUsd") or item.get("valueUsd"))
                    score = 0
                    if owner in principals:
                        score += 100
                    if owner == buyer:
                        score += 25
                    if owner == fee_payer:
                        score += 10
                    cand = {"mint": imint, "symbol": sym, "value": val, "usd": usd, "owner": owner, "score": score}
                    if sym == "SOL" or sym in STABLE_SYMBOLS:
                        input_candidates.append(cand)
                    else:
                        generic_candidates.append(cand)

                spent_sol = 0.0
                spent_usd = 0.0
                spent_value = 0.0
                spent_symbol = "SOL"

                ranked = sorted(input_candidates, key=lambda c: (c["score"], c["value"]), reverse=True)
                chosen = ranked[0] if ranked and ranked[0]["score"] > 0 else None
                if chosen is None and generic_candidates:
                    generic_ranked = sorted(generic_candidates, key=lambda c: (c["score"], c["usd"], c["value"]), reverse=True)
                    chosen = generic_ranked[0] if generic_ranked[0]["score"] > 0 else generic_ranked[0]
                if chosen:
                    spent_symbol = chosen["symbol"]
                    spent_value = chosen["value"]
                    spent_usd = chosen["usd"]
                    if chosen["symbol"] == "SOL":
                        spent_sol = chosen["value"]
                    elif chosen["symbol"] in STABLE_SYMBOLS:
                        spent_usd = chosen["value"]
                        spent_value = chosen["value"]

                if spent_usd <= 0 and spent_sol <= 0 and spent_value <= 0:
                    net_native = _net_native_spend([buyer, fee_payer] if fee_payer else [buyer])
                    if net_native > 0:
                        spent_sol = net_native
                        spent_value = net_native
                        spent_symbol = "SOL"

                if spent_usd <= 0 and spent_sol <= 0 and spent_value <= 0:
                    lamports = _fa(native_input.get("amount"))
                    if lamports > 0:
                        spent_sol = lamports / 1_000_000_000 if lamports > 1_000_000 else lamports
                        spent_value = spent_sol
                        spent_symbol = "SOL"

                if spent_sol > 0 or spent_usd > 0 or spent_value > 0:
                    return {
                        "buyer": buyer,
                        "got_tokens": amount,
                        "spent_sol": spent_sol,
                        "spent_usd": spent_usd,
                        "spent_value": spent_value if spent_value > 0 else (spent_usd or spent_sol),
                        "spent_symbol": spent_symbol,
                        "signature": tx.get("signature"),
                        "timestamp": tx.get("timestamp") or tx.get("blockTime") or int(time.time()),
                    }
    except Exception:
        pass

    def candidate_senders(buyer: str) -> list[str]:
        vals = []
        for v in [buyer, fee_payer]:
            if v and v not in vals:
                vals.append(v)
        for item in account_keys:
            try:
                acct = item.get("account") or item.get("pubkey")
            except Exception:
                acct = None
            if acct and acct not in vals:
                vals.append(acct)
        return vals

    def scan_spend(senders: list[str]) -> tuple[float, float, float, str]:
        spent_sol = 0.0
        spent_usd = 0.0
        spent_value = 0.0
        spent_symbol = "SOL"
        best_generic = None
        net_native = _net_native_spend(senders)
        if net_native > 0:
            spent_sol = net_native
            spent_value = net_native
            spent_symbol = "SOL"
        for ot in token_transfers:
            if (ot.get("fromUserAccount") or ot.get("fromTokenAccount")) not in senders:
                continue
            omint = ot.get("mint")
            if omint == mint:
                continue
            oval = float(ot.get("tokenAmount", 0) or 0)
            if oval <= 0:
                continue
            sym = _norm_sym(ot.get("tokenSymbol") or ot.get("symbol"), omint)
            usd = _fa(ot.get("usdValue") or ot.get("amountUsd") or ot.get("valueUsd"))
            if sym in STABLE_SYMBOLS:
                if oval > spent_usd:
                    spent_usd = oval
                    spent_value = oval
                    spent_symbol = sym
            elif sym == "SOL":
                if oval > spent_sol:
                    spent_sol = oval
                    spent_value = oval
                    spent_symbol = "SOL"
            elif best_generic is None or oval > best_generic["value"]:
                best_generic = {"value": oval, "symbol": sym, "usd": usd}
        if spent_usd <= 0 and spent_sol <= 0 and best_generic is not None:
            spent_value = best_generic["value"]
            spent_symbol = best_generic["symbol"]
            spent_usd = best_generic["usd"]
        return spent_sol, spent_usd, spent_value, spent_symbol

    for tt in token_transfers:
        if tt.get("mint") != mint:
            continue
        buyer = tt.get("toUserAccount") or tt.get("toTokenAccount")
        amount = float(tt.get("tokenAmount", 0) or 0)
        if not buyer or amount <= 0:
            continue

        senders = candidate_senders(buyer)
        spent_sol, spent_usd, spent_value, spent_symbol = scan_spend(senders)

        # Fallback: largest token outflow in whole tx.
        if spent_sol <= 0 and spent_usd <= 0 and spent_value <= 0:
            best_generic = None
            for ot in token_transfers:
                omint = ot.get("mint")
                if omint == mint:
                    continue
                oval = float(ot.get("tokenAmount", 0) or 0)
                if oval <= 0:
                    continue
                sym = _norm_sym(ot.get("tokenSymbol") or ot.get("symbol"), omint)
                usd = _fa(ot.get("usdValue") or ot.get("amountUsd") or ot.get("valueUsd"))
                if sym in STABLE_SYMBOLS and oval > spent_usd:
                    spent_usd = oval
                    spent_value = oval
                    spent_symbol = sym
                elif sym == "SOL" and oval > spent_sol:
                    spent_sol = oval
                    spent_value = oval
                    spent_symbol = "SOL"
                elif best_generic is None or oval > best_generic["value"]:
                    best_generic = {"value": oval, "symbol": sym, "usd": usd}
            if spent_sol <= 0 and spent_usd <= 0 and spent_value <= 0 and best_generic is not None:
                spent_value = best_generic["value"]
                spent_symbol = best_generic["symbol"]
                spent_usd = best_generic["usd"]

        if spent_sol <= 0 and spent_usd <= 0 and spent_value <= 0:
            return None
        return {
            "buyer": buyer,
            "got_tokens": amount,
            "spent_sol": spent_sol,
            "spent_usd": spent_usd,
            "spent_value": spent_value if spent_value > 0 else (spent_usd or spent_sol),
            "spent_symbol": spent_symbol,
            "signature": tx.get("signature"),
            "timestamp": tx.get("timestamp") or tx.get("blockTime") or int(time.time()),
        }
    return None
