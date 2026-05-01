from __future__ import annotations

import asyncio
import time
import re
from typing import Dict, Optional, Any
import aiosqlite

from bot.config import settings
from services.token_meta import fetch_token_meta
from services.ads_service import AdsService
from utils.price import ton_usd
from utils.formatter import build_buy_message_group, build_buy_message_channel
from bot.keyboards import buy_kb
from utils.ton_client import TonClient

TX_URL = "https://tonviewer.com/transaction/{sig}"
TON_SYMBOLS = {"TON", "TONCOIN"}


def _safe_float(v) -> float:
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def _deep_values(obj: Any):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _deep_values(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _deep_values(v)
    else:
        yield obj


def _contains_addr(obj: Any, addr: str) -> bool:
    if not addr:
        return False
    return addr in str(obj)


def _first(*vals):
    for v in vals:
        if v not in (None, "", [], {}):
            return v
    return None



def _normalise_tx_hash(value: Any) -> str | None:
    """Return a Tonviewer-friendly transaction hash, or None if not real.

    TonAPI event_id is commonly account:lt:hash. The old bot sometimes used
    only the LT (for example 72582651000012), which Tonviewer opens as 400.
    """
    if value in (None, "", [], {}):
        return None
    s = str(value).strip()
    if not s:
        return None
    if "/transaction/" in s:
        s = s.split("/transaction/", 1)[1]
    s = s.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    if ":" in s:
        tail = s.rsplit(":", 1)[-1].strip()
        if tail:
            s = tail
    # base64/base64url TON tx hash is normally 43-44 chars; allow common API hex too.
    if re.fullmatch(r"[A-Za-z0-9_-]{40,96}={0,2}", s) or re.fullmatch(r"[A-Fa-f0-9]{64}", s):
        if not s.isdigit():
            return s
    return None

def _event_cursor(ev: dict) -> str | None:
    raw = _first(ev.get("event_id"), ev.get("id"), ev.get("lt"), ev.get("hash"), ev.get("tx_hash"), ev.get("transaction_hash"))
    return str(raw) if raw not in (None, "", [], {}) else None

def _is_successful_action(action: dict) -> bool:
    status = str(_first(action.get("status"), action.get("success"), action.get("result"), "ok")).lower()
    return status not in {"failed", "fail", "false", "error", "aborted"}

def _event_id(ev: dict) -> str:
    return _event_cursor(ev) or ""


def _tx_hash(ev: dict) -> str | None:
    for key in ("tx_hash", "hash", "transaction_hash", "event_id", "id"):
        h = _normalise_tx_hash(ev.get(key))
        if h:
            return h
    tx = ev.get("transaction") or ev.get("tx") or {}
    if isinstance(tx, dict):
        for key in ("hash", "tx_hash", "transaction_hash"):
            h = _normalise_tx_hash(tx.get(key))
            if h:
                return h
    for action in ev.get("actions") or []:
        for v in _deep_values(action):
            h = _normalise_tx_hash(v)
            if h:
                return h
    return None


def _addr_from(obj: Any) -> str | None:
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        return _first(obj.get("address"), obj.get("account_address"), obj.get("account"), obj.get("user_friendly"), obj.get("raw"))
    return None


def _amount_from(obj: dict, *keys, decimals: int = 9) -> float:
    for k in keys:
        v = obj.get(k) if isinstance(obj, dict) else None
        if v in (None, ""):
            continue
        try:
            f = float(v)
            # TonAPI and DEX APIs often return nanoTON / smallest jetton units as strings.
            if abs(f) >= 1_000_000 and float(v).is_integer():
                return f / (10 ** decimals)
            return f
        except Exception:
            continue
    return 0.0


def _asset_symbol(asset: Any) -> str:
    if isinstance(asset, str):
        return asset.upper()
    if isinstance(asset, dict):
        meta = asset.get("metadata") or asset
        return str(_first(meta.get("symbol"), meta.get("name"), meta.get("type"), "")).upper()
    return ""


def _asset_address(asset: Any) -> str:
    if isinstance(asset, str):
        return asset
    if isinstance(asset, dict):
        return str(_first(asset.get("address"), asset.get("contract_address"), asset.get("jetton_address"), asset.get("master"), asset.get("root"), ""))
    return ""


def _parse_swap_action(action: dict, token: str) -> Optional[dict]:
    # Only accept explicit DEX swap actions. Generic transfer actions caused fake/old buy spam.
    if not _is_successful_action(action):
        return None
    action_type = str(action.get("type") or action.get("action_type") or "").lower()
    data = action.get("JettonSwap") or action.get("jetton_swap")
    if not data and "swap" in action_type:
        data = action.get("data") or action.get("swap")
    if not isinstance(data, dict) or not _contains_addr(data, token):
        return None

    # TonAPI action shape commonly contains dex_incoming_transfer and dex_outgoing_transfer.
    incoming = _first(data.get("dex_incoming_transfer"), data.get("incoming_transfer"), data.get("asset_in"), data.get("in"), data.get("input"), data.get("from")) or {}
    outgoing = _first(data.get("dex_outgoing_transfer"), data.get("outgoing_transfer"), data.get("asset_out"), data.get("out"), data.get("output"), data.get("to")) or {}

    in_asset = _first(incoming.get("asset") if isinstance(incoming, dict) else None, data.get("asset_in"), data.get("token_in"), data.get("in_token"))
    out_asset = _first(outgoing.get("asset") if isinstance(outgoing, dict) else None, data.get("asset_out"), data.get("token_out"), data.get("out_token"))
    in_addr = _asset_address(in_asset)
    out_addr = _asset_address(out_asset)
    in_sym = _asset_symbol(in_asset)
    out_sym = _asset_symbol(out_asset)

    # A buy means TON came in and the tracked jetton went out to the buyer.
    token_is_out = token in str(outgoing) or token == out_addr or token in str(out_asset)
    ton_is_in = (in_sym in TON_SYMBOLS or in_addr in {"", "ton", "TON_NATIVE"} or in_addr == "EQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAM9c" or "native" in str(in_asset).lower() or "toncoin" in str(in_asset).lower())
    if not (token_is_out and ton_is_in):
        return None

    spent_ton = _amount_from(incoming if isinstance(incoming, dict) else data, "amount", "value", "quantity", "amount_in") or _amount_from(data, "amount_in", "ton_amount", "spent_ton")
    got_tokens = _amount_from(outgoing if isinstance(outgoing, dict) else data, "amount", "value", "quantity", "amount_out", decimals=9) or _amount_from(data, "amount_out", "jetton_amount", "got_tokens", decimals=9)
    buyer = _addr_from(_first(data.get("user_wallet"), data.get("wallet"), data.get("sender"), data.get("owner"), data.get("recipient"))) or "Unknown"
    if spent_ton <= 0 and got_tokens <= 0:
        return None
    return {"buyer": buyer, "got_tokens": got_tokens, "spent_ton": spent_ton, "spent_value": spent_ton, "spent_symbol": "TON"}


def _parse_dedust_trade(tr: dict, token: str) -> Optional[dict]:
    if not _contains_addr(tr, token):
        return None
    # Handle several possible DeDust trade response shapes.
    asset_in = _first(tr.get("assetIn"), tr.get("asset_in"), tr.get("in"), tr.get("fromAsset"), tr.get("from")) or {}
    asset_out = _first(tr.get("assetOut"), tr.get("asset_out"), tr.get("out"), tr.get("toAsset"), tr.get("to")) or {}
    in_sym = _asset_symbol(asset_in)
    out_addr = _asset_address(asset_out)
    token_is_out = token in str(asset_out) or token == out_addr
    ton_is_in = in_sym in TON_SYMBOLS or "native" in str(asset_in).lower() or "ton" == str(asset_in).lower()
    if not (token_is_out and ton_is_in):
        return None
    spent_ton = _amount_from(tr, "amountIn", "amount_in", "volumeIn", "inAmount", "tonAmount") or _amount_from(asset_in if isinstance(asset_in, dict) else {}, "amount", "value")
    got_tokens = _amount_from(tr, "amountOut", "amount_out", "outAmount", "jettonAmount") or _amount_from(asset_out if isinstance(asset_out, dict) else {}, "amount", "value")
    return {
        "buyer": str(_first(tr.get("sender"), tr.get("account"), tr.get("user"), tr.get("wallet"), "Unknown")),
        "got_tokens": got_tokens,
        "spent_ton": spent_ton,
        "spent_value": spent_ton,
        "spent_symbol": "TON",
        "signature": str(_first(tr.get("txHash"), tr.get("tx_hash"), tr.get("hash"), tr.get("lt"), tr.get("createdAt"), time.time())),
        "timestamp": int(_safe_float(_first(tr.get("timestamp"), tr.get("createdAt"), time.time()))),
    }


class BuyWatcher:
    def __init__(self, bot, db, rpc: TonClient):
        self.bot = bot
        self.db = db
        self.rpc = rpc
        self._running = False
        self._last_ton_price = 0.0
        self._chat_type_cache: Dict[int, str] = {}
        # Prevent replaying historical swaps after redeploy/restart.
        self._started_at = int(time.time())

    async def _chat_type(self, chat_id: int) -> str:
        if chat_id in self._chat_type_cache:
            return self._chat_type_cache[chat_id]
        try:
            chat = await self.bot.get_chat(chat_id)
            ctype = getattr(chat, "type", "") or ""
        except Exception:
            ctype = ""
        self._chat_type_cache[chat_id] = ctype
        return ctype

    async def _load_targets(self, conn: aiosqlite.Connection) -> dict:
        cur = await conn.execute("SELECT * FROM group_settings WHERE is_active=1")
        rows = await cur.fetchall()
        m = {}
        for r in rows:
            mint = r["token_mint"]
            m.setdefault(mint, {"groups": [], "post_channel": False})
            m[mint]["groups"].append(r)
        cur = await conn.execute("SELECT mint, post_mode FROM tracked_tokens")
        rows2 = await cur.fetchall()
        for r in rows2:
            mint = r["mint"]
            m.setdefault(mint, {"groups": [], "post_channel": False})
            if r["post_mode"] == "channel":
                m[mint]["post_channel"] = True
        return m

    async def _get_last_sig(self, conn: aiosqlite.Connection, key: str) -> str | None:
        cur = await conn.execute("SELECT v FROM state_kv WHERE k=?", (f"last_sig:{key}",))
        row = await cur.fetchone()
        return row["v"] if row else None

    async def _set_last_sig(self, conn: aiosqlite.Connection, key: str, sig: str):
        await conn.execute("INSERT INTO state_kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (f"last_sig:{key}", sig))
        await conn.commit()

    async def run_forever(self):
        self._running = True
        while self._running:
            try:
                await self.tick()
            except Exception:
                pass
            await asyncio.sleep(settings.POLL_INTERVAL_SEC)

    async def _fetch_events(self, mint: str, last_sig: str | None):
        collected: list[dict] = []
        newest_token_cursor: str | None = None
        pools = await self.rpc.discover_pools(mint)
        if not pools:
            # Fallback only reads the jetton master events, but parsing stays strict: explicit swap actions only.
            pools = [{"address": mint, "dex": "tonapi"}]

        for pool in pools[:8]:
            addr = pool.get("address")
            if not addr:
                continue
            source_key = f"{mint}:{addr}"
            source_last = await self._get_last_sig(self._conn, source_key) if hasattr(self, "_conn") else None
            source_newest: str | None = None

            # DeDust direct trades endpoint when the pool is known. Only use trades with a real tx hash.
            if pool.get("dex") == "dedust":
                trades = await self.rpc.latest_dedust_trades(addr, 20)
                for tr in trades:
                    cursor = str(_first(tr.get("txHash"), tr.get("tx_hash"), tr.get("hash"), tr.get("lt"), tr.get("createdAt"), ""))
                    if not cursor:
                        continue
                    source_newest = source_newest or cursor
                    newest_token_cursor = newest_token_cursor or cursor
                    if cursor == source_last or cursor == last_sig:
                        break
                    ev = _parse_dedust_trade(tr, mint)
                    txh = _normalise_tx_hash(_first(tr.get("txHash"), tr.get("tx_hash"), tr.get("hash"), ev.get("signature") if ev else None))
                    if ev and txh:
                        ev["signature"] = txh
                        collected.append(ev)

            # TonAPI account events expose STON.fi and some DeDust swap actions.
            events = await self.rpc.get_account_events(addr, 30)
            for raw in events:
                cursor = _event_cursor(raw)
                if not cursor:
                    continue
                source_newest = source_newest or cursor
                newest_token_cursor = newest_token_cursor or cursor
                if cursor == source_last or cursor == last_sig:
                    break

                txh = _tx_hash(raw)
                if not txh:
                    # Do not post if Tonviewer cannot open the tx. This prevents wrong 400 links.
                    continue
                for action in raw.get("actions") or []:
                    parsed = _parse_swap_action(action, mint)
                    if parsed:
                        parsed.update({"signature": txh, "timestamp": raw.get("timestamp") or int(time.time())})
                        collected.append(parsed)
                        break

            if source_newest:
                try:
                    await self._set_last_sig(self._conn, source_key, source_newest)
                except Exception:
                    pass

        # Drop duplicates by real tx hash before posting.
        uniq: dict[str, dict] = {}
        for ev in collected:
            sig = ev.get("signature")
            if sig:
                uniq[sig] = ev
        return list(reversed(list(uniq.values()))), newest_token_cursor

    async def tick(self):
        conn = await self.db.connect()
        self._conn = conn
        targets = await self._load_targets(conn)
        ads_svc = AdsService(conn)
        active_ad_text, active_ad_link = await ads_svc.get_active_ad()
        ad_text = active_ad_text or await ads_svc.get_owner_fallback()
        ad_link = active_ad_link if active_ad_text else None
        price = await ton_usd(settings.TON_PRICE_URL)
        if price and price > 0:
            self._last_ton_price = price
        else:
            price = self._last_ton_price

        for mint, tgt in targets.items():
            last_sig = await self._get_last_sig(conn, mint)
            new_events, newest_sig = await self._fetch_events(mint, last_sig)
            if last_sig is None:
                if newest_sig:
                    await self._set_last_sig(conn, mint, newest_sig)
                continue
            if newest_sig and newest_sig != last_sig and not new_events:
                await self._set_last_sig(conn, mint, newest_sig)
            for ev in new_events:
                sig = ev.get("signature")
                if not sig:
                    continue
                # Do not replay old swaps after restart/redeploy. Cursor is still advanced below.
                try:
                    ev_ts = int(float(ev.get("timestamp") or 0))
                except Exception:
                    ev_ts = 0
                if ev_ts and ev_ts < self._started_at - 30:
                    await self._set_last_sig(conn, mint, sig)
                    continue
                # one transaction can appear in multiple pool/account feeds; never post twice
                if await self._get_last_sig(conn, f"posted:{sig}"):
                    continue
                await self._set_last_sig(conn, f"posted:{sig}", "1")
                await self._set_last_sig(conn, mint, sig)
                await self._post_buy(mint, ev, tgt, ad_text, ad_link, price)
        await conn.close()

    async def _post_buy(self, mint: str, ev: dict, tgt: dict, ad_text: str | None, ad_link: str | None, ton_price: float):
        meta = await fetch_token_meta(mint)
        token_name = meta.get("symbol") or meta.get("name") or mint[:6]
        spent_ton = float(ev.get("spent_ton") or ev.get("spent_sol") or 0.0)
        got_tokens = float(ev.get("got_tokens") or 0.0)
        buyer = ev.get("buyer") or "Unknown"
        spent_symbol = ev.get("spent_symbol") or "TON"
        spent_value = float(ev.get("spent_value") or spent_ton or 0.0)
        live_ton_price = float(ton_price or self._last_ton_price or 0.0)
        spent_usd = spent_ton * live_ton_price if spent_ton and live_ton_price else 0.0
        if spent_ton < float(settings.MIN_BUY_DEFAULT_TON):
            return

        now_ts = int(time.time())
        try:
            conn2 = await self.db.connect()
            if spent_usd > 0:
                await conn2.execute("INSERT INTO buys(mint, usd, ts) VALUES(?,?,?)", (mint, float(spent_usd), now_ts))
            if meta.get("priceUsd") is not None:
                await conn2.execute("INSERT INTO price_snapshots(mint, price_usd, ts) VALUES(?,?,?)", (mint, float(meta.get("priceUsd")), now_ts))
            if meta.get("mcapUsd") is not None:
                await conn2.execute("INSERT INTO mcap_snapshots(mint, mcap_usd, ts) VALUES(?,?,?)", (mint, float(meta.get("mcapUsd")), now_ts))
            await conn2.commit(); await conn2.close()
        except Exception:
            pass

        tx_url = TX_URL.format(sig=ev["signature"])
        tg_url = None
        token_cfg = {"buy_step": 1, "min_buy": 0.0, "emoji": "🟢", "media_file_id": None, "media_kind": "photo"}
        try:
            for _r in tgt.get("groups", []):
                if _r.get("telegram_link"):
                    tg_url = _r.get("telegram_link")
                    break
        except Exception:
            pass
        try:
            conn_tg = await self.db.connect()
            cur2 = await conn_tg.execute("SELECT telegram_link FROM tracked_tokens WHERE mint=?", (mint,))
            row2 = await cur2.fetchone()
            cur3 = await conn_tg.execute("SELECT buy_step, min_buy, emoji, media_file_id, media_kind FROM token_settings WHERE mint=?", (mint,))
            row3 = await cur3.fetchone()
            await conn_tg.close()
            if row2 and row2[0]:
                tg_url = row2[0]
            if row3:
                token_cfg = {"buy_step": row3[0] or 1, "min_buy": float(row3[1] or 0.0), "emoji": row3[2] or "🟢", "media_file_id": row3[3], "media_kind": row3[4] or "photo"}
        except Exception:
            pass

        def make_msg(emoji: str):
            return build_buy_message_channel(
                token_symbol=token_name,
                emoji=emoji,
                spent_sol=spent_ton,
                spent_usd=spent_usd,
                spent_symbol=spent_symbol,
                spent_value=spent_value,
                got_tokens=got_tokens,
                buyer=buyer,
                tx_url=tx_url,
                price_usd=meta.get("priceUsd"),
                mcap_usd=meta.get("mcapUsd"),
                tg_url=tg_url,
                ad_text=ad_text,
                ad_link=ad_link,
                chart_url=meta.get("dexUrl"),
            )

        for r in tgt["groups"]:
            min_buy = max(float(settings.MIN_BUY_DEFAULT_TON), float(r["min_buy_sol"] or 0), float(token_cfg.get("min_buy") or 0))
            if spent_ton < min_buy:
                continue
            emoji = token_cfg.get("emoji") or r["emoji"] or "🟢"
            media = token_cfg.get("media_file_id") or r["media_file_id"]
            media_kind = token_cfg.get("media_kind") or "photo"
            chat_id = int(r["group_id"])
            msg_text = make_msg(emoji)
            try:
                if media and await self._chat_type(chat_id) != "channel":
                    if media_kind == "video":
                        await self.bot.send_video(chat_id, media, caption=msg_text, reply_markup=buy_kb(mint), parse_mode="HTML")
                    else:
                        await self.bot.send_photo(chat_id, media, caption=msg_text, reply_markup=buy_kb(mint), parse_mode="HTML")
                else:
                    await self.bot.send_message(chat_id, msg_text, reply_markup=buy_kb(mint), disable_web_page_preview=True, parse_mode="HTML")
            except Exception:
                pass

        if tgt.get("post_channel") and settings.POST_CHANNEL:
            try:
                await self.bot.send_message(settings.POST_CHANNEL, make_msg("✅"), reply_markup=buy_kb(mint), disable_web_page_preview=True, parse_mode="HTML")
            except Exception:
                pass

    async def close(self):
        self._running = False
        await self.rpc.close()
