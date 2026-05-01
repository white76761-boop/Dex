from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import time
from utils.ton_rpc import TonClient

NANOTON = 1_000_000_000


@dataclass
class PaymentResult:
    ok: bool
    reason: str
    amount_sol: float = 0.0  # kept for DB compatibility; value is TON
    slot: Optional[int] = None
    timestamp: Optional[int] = None
    signature: Optional[str] = None


def _addr(v):
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        return v.get("address") or v.get("account_address") or v.get("friendly") or v.get("raw")
    return None


def _details(a: dict) -> dict:
    d = a.get("details") or {}
    for key in ("ton_transfer", "TonTransfer"):
        if isinstance(d.get(key), dict):
            return d.get(key) or {}
    return d


def _event_hash(ev: dict) -> str:
    return str(ev.get("event_id") or ev.get("trace_id") or ev.get("id") or ev.get("hash") or ev.get("lt") or "")


def _amount_ton(v) -> float:
    try:
        x = float(v or 0)
        return x / NANOTON if x > 10_000 else x
    except Exception:
        return 0.0


def _find_payment_in_event(ev: dict, expected_to: str, min_amount_ton: float) -> PaymentResult | None:
    if ev.get("is_scam") or ev.get("in_progress"):
        return None
    ts = int(ev.get("timestamp") or time.time())
    for a in ev.get("actions") or []:
        typ = str(a.get("type") or "").lower()
        if "ton" not in typ and "transfer" not in typ:
            continue
        d = _details(a)
        dest = _addr(d.get("recipient") or d.get("destination") or d.get("to"))
        if (dest or "").lower() != expected_to.lower():
            continue
        amount = _amount_ton(d.get("amount") or d.get("value") or a.get("amount"))
        if amount + 1e-9 >= min_amount_ton:
            sig = _event_hash(ev)
            return PaymentResult(True, "Payment verified.", amount_sol=amount, timestamp=ts, signature=sig)
    return None


async def verify_sol_transfer(
    rpc: TonClient,
    signature: str,
    expected_to: str,
    min_amount_sol: float,
    max_age_sec: int = 3 * 60 * 60,
) -> PaymentResult:
    ev = await rpc.get_event(signature)
    if not ev:
        return PaymentResult(False, "Transaction not found yet. Try again in 10 seconds.")
    ts = int(ev.get("timestamp") or time.time())
    if int(time.time()) - ts > max_age_sec:
        return PaymentResult(False, "Transaction is too old.")
    res = _find_payment_in_event(ev, expected_to, min_amount_sol)
    if res:
        return res
    return PaymentResult(False, f"Payment not detected for this invoice. Send at least {min_amount_sol:g} TON to the invoice wallet.")


async def find_recent_payment(
    rpc: TonClient,
    expected_to: str,
    min_amount_sol: float,
    used_signatures: set[str] | None = None,
) -> PaymentResult:
    used_signatures = used_signatures or set()
    try:
        events = await rpc.get_account_events(expected_to, limit=30)
    except Exception:
        return PaymentResult(False, "Could not fetch wallet payments right now.")
    for ev in events:
        sig = _event_hash(ev)
        if not sig or sig in used_signatures:
            continue
        res = _find_payment_in_event(ev, expected_to, min_amount_sol)
        if res and res.ok:
            return res
    return PaymentResult(False, "Payment not detected yet.")
