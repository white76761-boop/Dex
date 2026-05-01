from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import time
import re
from utils.ton_client import TonClient

@dataclass
class PaymentResult:
    ok: bool
    reason: str
    amount_sol: float = 0.0  # kept for DB compatibility; value is TON
    slot: Optional[int] = None
    timestamp: Optional[int] = None
    signature: Optional[str] = None


def _extract_hash(v: str) -> str:
    t = (v or "").strip().rstrip("/")
    for marker in ["/transaction/", "/tx/"]:
        if marker in t:
            t = t.split(marker, 1)[1]
    if "?" in t:
        t = t.split("?", 1)[0]
    if "#" in t:
        t = t.split("#", 1)[0]
    return t


def _amount_ton(obj) -> float:
    try:
        f = float(obj or 0)
        if abs(f) >= 1_000_000:
            return f / 1_000_000_000
        return f
    except Exception:
        return 0.0


def _event_hash(ev: dict) -> str:
    return str(ev.get("event_id") or ev.get("hash") or ev.get("tx_hash") or ev.get("id") or "")


def _incoming_ton(ev: dict, expected_to: str) -> float:
    actions = ev.get("actions") or []
    best = 0.0
    for action in actions:
        data = action.get("TonTransfer") or action.get("ton_transfer") or action.get("data") or action
        if not isinstance(data, dict):
            continue
        recipient = str(data.get("recipient") or data.get("receiver") or data.get("destination") or data.get("to") or "")
        if expected_to not in recipient and recipient not in expected_to:
            continue
        best = max(best, _amount_ton(data.get("amount") or data.get("value")))
    return best

async def verify_sol_transfer(rpc: TonClient, signature: str, expected_to: str, min_amount_sol: float, max_age_sec: int = 3 * 60 * 60) -> PaymentResult:
    sig = _extract_hash(signature)
    events = await rpc.get_account_events(expected_to, limit=50)
    now = int(time.time())
    for ev in events:
        eh = _event_hash(ev)
        if sig and sig not in eh and eh not in sig and sig not in str(ev):
            continue
        ts = int(ev.get("timestamp") or now)
        if now - ts > max_age_sec:
            return PaymentResult(False, "Transaction is too old.", timestamp=ts, signature=eh or sig)
        amount = _incoming_ton(ev, expected_to)
        if amount + 1e-9 >= min_amount_sol:
            return PaymentResult(True, "Payment verified.", amount_sol=amount, timestamp=ts, signature=eh or sig)
    return PaymentResult(False, f"Payment not detected for this invoice. Send at least {min_amount_sol:g} TON to the invoice wallet.")

async def find_recent_payment(rpc: TonClient, expected_to: str, min_amount_sol: float, used_signatures: set[str] | None = None) -> PaymentResult:
    used_signatures = used_signatures or set()
    try:
        events = await rpc.get_account_events(expected_to, limit=50)
    except Exception:
        return PaymentResult(False, "Could not fetch wallet payments right now.")
    for ev in events:
        sig = _event_hash(ev)
        if not sig or sig in used_signatures:
            continue
        amount = _incoming_ton(ev, expected_to)
        if amount + 1e-9 >= min_amount_sol:
            return PaymentResult(True, "Payment verified.", amount_sol=amount, timestamp=ev.get("timestamp"), signature=sig)
    return PaymentResult(False, "Payment not detected yet.")
