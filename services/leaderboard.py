from __future__ import annotations
import asyncio, time
import aiosqlite
from typing import List, Tuple
from bot.config import settings
from services.token_meta import fetch_token_meta
from utils.formatter import build_leaderboard_message
from bot.keyboards import leaderboard_kb
from aiogram.exceptions import TelegramBadRequest


class LeaderboardUpdater:
    def __init__(self, bot, db):
        self.bot = bot
        self.db = db
        self._running = False

    async def _get_kv(self, conn: aiosqlite.Connection, key: str) -> str | None:
        cur = await conn.execute("SELECT v FROM state_kv WHERE k=?", (key,))
        row = await cur.fetchone()
        return row["v"] if row else None

    async def _set_kv(self, conn: aiosqlite.Connection, key: str, val: str):
        await conn.execute(
            "INSERT INTO state_kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (key, val),
        )
        await conn.commit()

    async def run_forever(self):
        self._running = True
        while self._running:
            try:
                await self.tick()
            except Exception:
                pass
            await asyncio.sleep(30)

    async def tick(self):
        if not settings.POST_CHANNEL:
            return
        conn = await self.db.connect()
        now = int(time.time())
        since = now - 24 * 3600
        cur = await conn.execute(
            "SELECT mint, SUM(usd) AS vol FROM buys WHERE ts>=? GROUP BY mint ORDER BY vol DESC LIMIT 25",
            (since,),
        )
        buy_rows = await cur.fetchall()
        forced_cur = await conn.execute(
            "SELECT mint, COALESCE(symbol, name, mint) AS label, force_trending, force_leaderboard, manual_rank, trend_until_ts FROM tracked_tokens ORDER BY created_at DESC"
        )
        tracked = await forced_cur.fetchall()

        metrics: dict[str, float] = {r['mint']: float(r['vol'] or 0) for r in buy_rows}
        labels: dict[str, str] = {}
        chart_urls: dict[str, str | None] = {}
        mcaps: dict[str, float | None] = {}
        all_mints: set[str] = set(metrics.keys())
        for row in tracked:
            labels[row['mint']] = row['label']
            all_mints.add(row['mint'])
            if row['force_trending'] or row['force_leaderboard'] or (row['trend_until_ts'] or 0) > now:
                metrics[row['mint']] = max(metrics.get(row['mint'], 0.0), 1.0)

        # Prefer fresh DexScreener market cap data first; fall back to stored snapshots only if needed.
        for mint in list(all_mints):
            snap_mcap = None
            try:
                curm = await conn.execute("SELECT mcap_usd FROM mcap_snapshots WHERE mint=? ORDER BY ts DESC LIMIT 1", (mint,))
                rowm = await curm.fetchone()
                if rowm and rowm[0]:
                    snap_mcap = float(rowm[0])
            except Exception:
                snap_mcap = None
            try:
                meta = await fetch_token_meta(mint)
                labels[mint] = meta.get('symbol') or meta.get('name') or labels.get(mint) or mint[:6]
                chart_urls[mint] = meta.get('dexUrl')
                fresh_mcap = meta.get('mcapUsd')
                # Ignore obviously wrong tiny values when a real market cap should be in K/M.
                if fresh_mcap is not None and float(fresh_mcap) >= 1000:
                    mcaps[mint] = float(fresh_mcap)
                elif snap_mcap is not None and float(snap_mcap) >= 1000:
                    mcaps[mint] = float(snap_mcap)
                elif fresh_mcap is not None:
                    mcaps[mint] = float(fresh_mcap)
                else:
                    mcaps[mint] = snap_mcap
            except Exception:
                labels[mint] = labels.get(mint) or mint[:6]
                chart_urls[mint] = chart_urls.get(mint)
                mcaps[mint] = snap_mcap

        ordered = sorted(metrics.items(), key=lambda kv: kv[1], reverse=True)[:10]
        rows: List[Tuple[int, str, str, float, str | None]] = []
        for rank, (mint, vol) in enumerate(ordered, start=1):
            pct = await self._pct_change_24h(conn, mint, now)
            mcap = mcaps.get(mint)
            metric = self._compact_metric(mcap if mcap and mcap > 0 else vol)
            rows.append((rank, labels.get(mint, mint[:6]), metric, pct, chart_urls.get(mint)))
        while len(rows) < 10:
            n = len(rows) + 1
            rows.append((n, "TOKEN", "0", 0.0, None))

        footer_handle = f"@{settings.BOT_USERNAME}"
        text = build_leaderboard_message(rows, footer_handle)
        fixed_mid = int(getattr(settings, "LEADERBOARD_MESSAGE_ID", 0) or 0)
        if fixed_mid:
            await self._set_kv(conn, "leaderboard_message_id", str(fixed_mid))
        mid = str(fixed_mid) if fixed_mid else await self._get_kv(conn, "leaderboard_message_id")
        if not mid:
            msg = await self.bot.send_message(settings.POST_CHANNEL, text, reply_markup=leaderboard_kb(), disable_web_page_preview=True, parse_mode="HTML")
            await self._set_kv(conn, "leaderboard_message_id", str(msg.message_id))
        else:
            try:
                await self.bot.edit_message_text(text=text, chat_id=settings.POST_CHANNEL, message_id=int(mid), reply_markup=leaderboard_kb(), disable_web_page_preview=True, parse_mode="HTML")
            except TelegramBadRequest as e:
                err = str(e).lower()
                if "message is not modified" in err:
                    await conn.close()
                    return
                if fixed_mid:
                    await conn.close()
                    return
                msg = await self.bot.send_message(settings.POST_CHANNEL, text, reply_markup=leaderboard_kb(), disable_web_page_preview=True, parse_mode="HTML")
                await self._set_kv(conn, "leaderboard_message_id", str(msg.message_id))
            except Exception:
                if fixed_mid:
                    await conn.close()
                    return
                msg = await self.bot.send_message(settings.POST_CHANNEL, text, reply_markup=leaderboard_kb(), disable_web_page_preview=True, parse_mode="HTML")
                await self._set_kv(conn, "leaderboard_message_id", str(msg.message_id))
        await conn.close()

    def _compact_metric(self, x: float) -> str:
        if x >= 1_000_000:
            return f"{x/1_000_000:.0f}M"
        if x >= 1_000:
            return f"{x/1_000:.0f}K"
        return f"{x:.0f}"

    async def _pct_change_24h(self, conn: aiosqlite.Connection, mint: str, now: int) -> float:
        since = now - 24 * 3600
        cur = await conn.execute("SELECT price_usd, ts FROM price_snapshots WHERE mint=? AND ts>=? ORDER BY ts ASC LIMIT 1", (mint, since))
        first = await cur.fetchone()
        cur = await conn.execute("SELECT price_usd, ts FROM price_snapshots WHERE mint=? ORDER BY ts DESC LIMIT 1", (mint,))
        last = await cur.fetchone()
        if not first or not last:
            return 0.0
        p0 = float(first['price_usd'] or 0.0)
        p1 = float(last['price_usd'] or 0.0)
        if p0 <= 0:
            return 0.0
        return ((p1 - p0) / p0) * 100.0

    async def close(self):
        self._running = False
