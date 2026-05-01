from __future__ import annotations
import asyncio
import time
import re
from typing import Dict, Optional
import aiosqlite

from bot.config import settings
from services.token_meta import fetch_token_meta
from services.ads_service import AdsService
from utils.price import ton_usd
from utils.formatter import build_buy_message_group, build_buy_message_channel
from bot.keyboards import buy_kb

TX_URL = settings.TONVIEWER_BASE.rstrip("/") + "/transaction/{sig}"
NANOTON = 1_000_000_000
GROYPAD_GRADUATION_NANO = 1050 * NANOTON
STONFI_HINTS = ("ston", "ston.fi", "stonfi")
DEDUST_HINTS = ("dedust", "de dust")


def _safe_float(v) -> float:
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def _addr(v) -> str | None:
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        return v.get("address") or v.get("account_address") or v.get("raw") or v.get("friendly")
    return None


def _action_type(a: dict) -> str:
    return str(a.get("type") or a.get("action_type") or "").lower()


def _details(a: dict) -> dict:
    d = a.get("details") or {}
    # TonAPI often nests by snake/camel case inside details.
    for key in ("jetton_swap", "JettonSwap", "ton_transfer", "TonTransfer", "jetton_transfer", "JettonTransfer"):
        if isinstance(d.get(key), dict):
            return d.get(key) or {}
    return d


def _asset_address(asset: dict | None) -> str | None:
    if not isinstance(asset, dict):
        return None
    jetton = asset.get("jetton") or asset.get("jetton_info") or asset.get("metadata") or asset
    return _addr(jetton.get("address") if isinstance(jetton, dict) else jetton) or asset.get("address") or asset.get("contract_address")


def _asset_symbol(asset: dict | None) -> str:
    if not isinstance(asset, dict):
        return "TOKEN"
    if (asset.get("type") or "").lower() == "ton" or asset.get("is_ton"):
        return "TON"
    meta = asset.get("metadata") or asset.get("jetton") or {}
    return (meta.get("symbol") if isinstance(meta, dict) else None) or asset.get("symbol") or "TOKEN"


def _asset_amount(asset: dict | None) -> float:
    if not isinstance(asset, dict):
        return 0.0
    for k in ("amount", "quantity", "value"):
        if asset.get(k) is not None:
            raw = asset.get(k)
            break
    else:
        raw = 0
    decimals = asset.get("decimals")
    if decimals is None:
        meta = asset.get("metadata") or asset.get("jetton") or {}
        decimals = meta.get("decimals") if isinstance(meta, dict) else None
    try:
        rawf = float(raw or 0)
        dec = int(decimals if decimals is not None else (9 if rawf > 10_000_000 else 0))
        if dec and rawf > 10_000:
            return rawf / (10 ** dec)
        return rawf
    except Exception:
        return 0.0


def _is_ton_asset(asset: dict | None) -> bool:
    if not isinstance(asset, dict):
        return False
    sym = _asset_symbol(asset).upper()
    return sym in {"TON", "WTON"} or (asset.get("type") or "").lower() == "ton" or asset.get("is_ton") is True


def _event_id(ev: dict) -> str:
    return str(ev.get("event_id") or ev.get("id") or ev.get("trace_id") or ev.get("lt") or ev.get("timestamp") or "")


def _event_hash(ev: dict) -> str:
    return str(ev.get("event_id") or ev.get("trace_id") or ev.get("id") or ev.get("hash") or ev.get("lt") or int(time.time()))


def _event_lt(ev: dict) -> int | None:
    for k in ("lt", "account_event_seqno", "seqno"):
        try:
            if ev.get(k) is not None:
                return int(ev[k])
        except Exception:
            pass
    return None


def _extract_opcode(a: dict) -> str | None:
    d = _details(a)
    candidates = [a.get("opcode"), d.get("opcode"), d.get("op_code"), d.get("operation"), d.get("operation_code")]
    for c in candidates:
        if c is None:
            continue
        s = str(c).lower()
        if s.startswith("0x"):
            return s
        try:
            return hex(int(s))
        except Exception:
            m = re.search(r"0x[0-9a-f]+", s)
            if m:
                return m.group(0)
    return None


def _buyer_from_action(a: dict) -> str:
    d = _details(a)
    for k in ("sender", "sender_address", "source", "from", "owner", "account"):
        v = _addr(d.get(k) or a.get(k))
        if v:
            return v
    return "Unknown"


def _find_swap_buy(ev: dict, token: str) -> Optional[dict]:
    actions = ev.get("actions") or []
    for a in actions:
        if "swap" not in _action_type(a):
            continue
        d = _details(a)
        in_asset = d.get("asset_in") or d.get("from_asset") or d.get("in") or d.get("ask_asset") or d.get("token_in")
        out_asset = d.get("asset_out") or d.get("to_asset") or d.get("out") or d.get("offer_asset") or d.get("token_out")
        out_addr = (_asset_address(out_asset) or "").lower()
        in_addr = (_asset_address(in_asset) or "").lower()
        if out_addr != token.lower() and in_addr == token.lower():
            continue
        if out_addr != token.lower():
            # Some parsers only expose jetton_master fields directly.
            out_addr = str(d.get("jetton_master") or d.get("jetton_address") or "").lower()
            if out_addr != token.lower():
                continue
        if not _is_ton_asset(in_asset):
            # still accept if amount in TON is exposed directly
            spent = _safe_float(d.get("ton_in") or d.get("amount_in_ton"))
        else:
            spent = _asset_amount(in_asset)
        got = _asset_amount(out_asset) or _safe_float(d.get("amount_out") or d.get("jetton_amount"))
        if spent <= 0 and _safe_float(d.get("value")) > 0:
            spent = _safe_float(d.get("value")) / NANOTON
        if got <= 0:
            got = _safe_float(d.get("received") or 0)
        if spent <= 0 and got <= 0:
            continue
        platform = "stonfi" if any(h in str(a).lower() for h in STONFI_HINTS) else "dedust" if any(h in str(a).lower() for h in DEDUST_HINTS) else "dex"
        return {
            "buyer": _buyer_from_action(a),
            "got_tokens": got,
            "spent_ton": spent,
            "spent_value": spent,
            "spent_symbol": "TON",
            "signature": _event_hash(ev),
            "timestamp": ev.get("timestamp") or int(time.time()),
            "platform": platform,
        }
    return None


def _find_opcode_buy(ev: dict, token: str) -> Optional[dict]:
    actions = ev.get("actions") or []
    for a in actions:
        d = _details(a)
        op = _extract_opcode(a)
        destination = _addr(d.get("recipient") or d.get("destination") or d.get("to") or a.get("destination"))
        is_to_token = (destination or "").lower() == token.lower()
        raw = str(a).lower()
        if not is_to_token and token.lower() not in raw:
            continue
        if op not in {settings.GROYPAD_BUY_OPCODE.lower(), settings.BLUM_BUY_OPCODE.lower()}:
            continue
        value = d.get("amount") or d.get("value") or a.get("amount") or 0
        spent_ton = _safe_float(value)
        if spent_ton > 10_000:
            spent_ton = spent_ton / NANOTON
        platform = "groypad" if op == settings.GROYPAD_BUY_OPCODE.lower() else "blum"
        return {
            "buyer": _buyer_from_action(a),
            "got_tokens": _safe_float(d.get("tokens_out") or d.get("jetton_amount") or d.get("amount_out") or 0),
            "spent_ton": spent_ton,
            "spent_value": spent_ton,
            "spent_symbol": "TON",
            "signature": _event_hash(ev),
            "timestamp": ev.get("timestamp") or int(time.time()),
            "platform": platform,
        }
    return None


def _find_buy_in_event(ev: dict, token: str) -> Optional[dict]:
    if ev.get("is_scam") or ev.get("in_progress"):
        return None
    return _find_opcode_buy(ev, token) or _find_swap_buy(ev, token)


def _stack_int(item) -> int:
    if isinstance(item, dict):
        v = item.get("value") or item.get("num") or item.get("number") or "0"
    else:
        v = item
    if isinstance(v, int):
        return v
    s = str(v or "0")
    try:
        return int(s, 16) if s.startswith("0x") else int(s)
    except Exception:
        return 0


def _progress_bar(progress: float | None) -> str | None:
    if progress is None:
        return None
    p = max(0, min(100, float(progress)))
    filled = int(round(p / 10))
    return "█" * filled + "░" * (10 - filled) + f" {p:.1f}%"


class BuyWatcher:
    def __init__(self, bot, db, rpc):
        self.bot = bot
        self.db = db
        self.rpc = rpc
        self._running = False
        self._last_ton_price = 0.0
        self._chat_type_cache: Dict[int, str] = {}

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
            token = r["token_mint"]
            m.setdefault(token, {"groups": [], "post_channel": True})
            m[token]["groups"].append(r)
        cur = await conn.execute("SELECT mint, post_mode FROM tracked_tokens")
        rows2 = await cur.fetchall()
        for r in rows2:
            token = r["mint"]
            m.setdefault(token, {"groups": [], "post_channel": True})
            # BazaTon requirement: every buy goes to the trending section/channel.
            m[token]["post_channel"] = True
        return m

    async def _get_last_id(self, conn: aiosqlite.Connection, token: str) -> str | None:
        cur = await conn.execute("SELECT v FROM state_kv WHERE k=?", (f"last_event:{token}",))
        row = await cur.fetchone()
        return row["v"] if row else None

    async def _set_last_id(self, conn: aiosqlite.Connection, token: str, eid: str):
        await conn.execute("INSERT INTO state_kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (f"last_event:{token}", eid))
        await conn.commit()

    async def run_forever(self):
        self._running = True
        while self._running:
            try:
                await self.tick()
            except Exception:
                pass
            await asyncio.sleep(settings.POLL_INTERVAL_SEC)

    async def _fetch_events(self, token: str, last_id: str | None):
        newest_id = None
        collected = []
        before_lt = None
        try:
            for _ in range(4):
                events = await self.rpc.get_account_events(token, limit=30, before_lt=before_lt)
                if not events:
                    break
                for ev in events:
                    eid = _event_id(ev)
                    if not eid:
                        continue
                    if newest_id is None:
                        newest_id = eid
                    if eid == last_id:
                        return list(reversed(collected)), newest_id
                    buy = _find_buy_in_event(ev, token)
                    if buy:
                        collected.append(buy)
                before_lt = _event_lt(events[-1])
                if not before_lt:
                    break
            return list(reversed(collected)), newest_id
        except Exception:
            return [], newest_id

    async def _get_bonding_progress(self, token: str, platform: str, meta: dict) -> float | None:
        if meta.get("progress") is not None:
            return float(meta.get("progress"))
        if platform != "groypad":
            return None
        try:
            res = await self.rpc.run_get_method(token, "get_meme_data", [])
            stack = (res or {}).get("stack") or []
            if len(stack) >= 12:
                raised = _stack_int(stack[11])
                return min(100.0, (raised * 100.0) / GROYPAD_GRADUATION_NANO)
        except Exception:
            return None
        return None

    async def tick(self):
        conn = await self.db.connect()
        targets = await self._load_targets(conn)
        ads_svc = AdsService(conn)
        active_ad_text, active_ad_link = await ads_svc.get_active_ad()
        ad_text = active_ad_text or await ads_svc.get_owner_fallback()
        ad_link = active_ad_link if active_ad_text else None
        price = await ton_usd(self.rpc)
        if price > 0:
            self._last_ton_price = price
        else:
            price = self._last_ton_price

        for token, tgt in targets.items():
            last_id = await self._get_last_id(conn, token)
            new_events, newest_id = await self._fetch_events(token, last_id)
            if last_id is None:
                if newest_id:
                    await self._set_last_id(conn, token, newest_id)
                continue
            if newest_id and newest_id != last_id and not new_events:
                await self._set_last_id(conn, token, newest_id)
            for ev in new_events:
                await self._set_last_id(conn, token, ev["signature"])
                await self._post_buy(token, ev, tgt, ad_text, ad_link, price)
        await conn.close()

    async def _post_buy(self, token: str, ev: dict, tgt: dict, ad_text: str | None, ad_link: str | None, ton_price: float):
        meta = await fetch_token_meta(token, self.rpc)
        token_name = meta.get("symbol") or meta.get("name") or token[:6]
        spent_ton = float(ev.get("spent_ton") or ev.get("spent_sol") or 0.0)
        got_tokens = float(ev.get("got_tokens") or 0.0)
        spent_symbol = ev.get("spent_symbol") or "TON"
        spent_value = float(ev.get("spent_value") or spent_ton)
        platform = ev.get("platform") or meta.get("platform") or "ton"
        spent_usd = spent_ton * float(ton_price or 0.0) if spent_ton > 0 and ton_price > 0 else 0.0
        effective_spent_ton = spent_ton if spent_symbol == "TON" else 0.0
        if effective_spent_ton < float(settings.MIN_BUY_DEFAULT_TON):
            return

        progress = await self._get_bonding_progress(token, platform, meta)
        progress_bar = _progress_bar(progress) if platform in {"groypad", "blum"} else None
        now_ts = int(time.time())
        try:
            conn2 = await self.db.connect()
            if spent_usd > 0:
                await conn2.execute("INSERT INTO buys(mint, usd, ts) VALUES(?,?,?)", (token, spent_usd, now_ts))
            if meta.get("priceUsd") is not None:
                await conn2.execute("INSERT INTO price_snapshots(mint, price_usd, ts) VALUES(?,?,?)", (token, float(meta.get("priceUsd")), now_ts))
            if meta.get("mcapUsd") is not None:
                await conn2.execute("INSERT INTO mcap_snapshots(mint, mcap_usd, ts) VALUES(?,?,?)", (token, float(meta.get("mcapUsd")), now_ts))
            await conn2.commit(); await conn2.close()
        except Exception:
            pass

        tx_url = TX_URL.format(sig=ev["signature"])
        tg_url = None
        token_cfg = {"buy_step": 1, "min_buy": 0.0, "emoji": "🟢", "media_file_id": None, "media_kind": "photo"}
        try:
            for _r in tgt.get("groups", []):
                if _r.get("telegram_link"):
                    tg_url = _r.get("telegram_link"); break
        except Exception:
            pass
        if meta.get("telegram") and not tg_url:
            tg_url = meta.get("telegram")
        try:
            conn_tg = await self.db.connect()
            cur2 = await conn_tg.execute("SELECT telegram_link FROM tracked_tokens WHERE mint=?", (token,))
            row2 = await cur2.fetchone()
            cur3 = await conn_tg.execute("SELECT buy_step, min_buy, emoji, media_file_id, media_kind FROM token_settings WHERE mint=?", (token,))
            row3 = await cur3.fetchone()
            await conn_tg.close()
            if row2 and row2[0]:
                tg_url = row2[0]
            if row3:
                token_cfg = {"buy_step": row3[0] or 1, "min_buy": float(row3[1] or 0.0), "emoji": row3[2] or "🟢", "media_file_id": row3[3], "media_kind": row3[4] or "photo"}
        except Exception:
            pass

        base_kwargs = dict(
            token_symbol=token_name,
            emoji="✅",
            spent_sol=effective_spent_ton,
            spent_usd=spent_usd,
            spent_symbol=spent_symbol,
            spent_value=spent_value,
            got_tokens=got_tokens,
            buyer=ev.get("buyer") or "Unknown",
            tx_url=tx_url,
            price_usd=meta.get("priceUsd"),
            mcap_usd=meta.get("mcapUsd"),
            tg_url=tg_url,
            ad_text=ad_text,
            ad_link=ad_link,
            chart_url=meta.get("dexUrl") or f"https://tonviewer.com/{token}",
            platform=platform,
            progress_bar=progress_bar,
        )
        msg_text_channel = build_buy_message_channel(**base_kwargs)

        for r in tgt.get("groups", []):
            min_buy = max(float(settings.MIN_BUY_DEFAULT_TON), float(r["min_buy_sol"] or 0), float(token_cfg.get("min_buy") or 0))
            if effective_spent_ton < min_buy:
                continue
            kwargs = dict(base_kwargs)
            kwargs.update({"emoji": token_cfg.get("emoji") or r["emoji"] or "🟢", "tg_url": tg_url or r["telegram_link"]})
            msg_text2 = build_buy_message_group(**kwargs)
            media = token_cfg.get("media_file_id") or r["media_file_id"]
            media_kind = token_cfg.get("media_kind") or "photo"
            chat_id = int(r["group_id"])
            try:
                if await self._chat_type(chat_id) == "channel" or not media:
                    await self.bot.send_message(chat_id, msg_text2, reply_markup=buy_kb(token), disable_web_page_preview=True, parse_mode="HTML")
                elif media_kind == "animation":
                    await self.bot.send_animation(chat_id, media, caption=msg_text2, reply_markup=buy_kb(token), parse_mode="HTML")
                elif media_kind == "video":
                    await self.bot.send_video(chat_id, media, caption=msg_text2, reply_markup=buy_kb(token), parse_mode="HTML")
                elif media_kind == "document":
                    await self.bot.send_document(chat_id, media, caption=msg_text2, reply_markup=buy_kb(token), parse_mode="HTML")
                else:
                    await self.bot.send_photo(chat_id, media, caption=msg_text2, reply_markup=buy_kb(token), parse_mode="HTML")
            except Exception:
                pass

        channel_min_buy = max(float(settings.MIN_BUY_DEFAULT_TON), float(token_cfg.get("min_buy") or 0))
        if settings.POST_CHANNEL and effective_spent_ton >= channel_min_buy:
            # Send buys into the configured trending channel/topic.  If a thread id is
            # provided via POST_CHANNEL_THREAD_ID, messages will be sent to that
            # thread; otherwise they go directly to the channel.
            try:
                send_kwargs = {
                    "chat_id": settings.POST_CHANNEL,
                    "text": msg_text_channel,
                    "reply_markup": buy_kb(token),
                    "disable_web_page_preview": True,
                    "parse_mode": "HTML",
                }
                if getattr(settings, "POST_CHANNEL_THREAD_ID", None):
                    # When targeting a forum topic, include message_thread_id so
                    # Telegram routes the message correctly.
                    send_kwargs["message_thread_id"] = settings.POST_CHANNEL_THREAD_ID
                await self.bot.send_message(**send_kwargs)
            except Exception:
                pass

    async def close(self):
        await self.rpc.close()
