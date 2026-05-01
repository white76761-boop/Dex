from __future__ import annotations
import time
import aiosqlite
from typing import Optional

class AdsService:
    def __init__(self, conn: aiosqlite.Connection):
        self.conn = conn

    async def create_ad(self, created_by: int, text: str, link: str | None, start_ts: int, end_ts: int, tx_sig: str, amount_sol: float, kind: str = "ad"):
        await self.conn.execute(
            "INSERT INTO ads(created_by,text,link,start_ts,end_ts,tx_sig,amount_sol,kind) VALUES(?,?,?,?,?,?,?,?)",
            (created_by, text, link, start_ts, end_ts, tx_sig, amount_sol, kind),
        )
        await self.conn.commit()

    async def active_ads(self, now_ts: Optional[int] = None, limit: int = 2):
        now_ts = now_ts or int(time.time())
        cur = await self.conn.execute(
            "SELECT id, text, link FROM ads WHERE kind='ad' AND start_ts<=? AND end_ts>=? ORDER BY id ASC LIMIT ?",
            (now_ts, now_ts, limit),
        )
        return await cur.fetchall()

    async def get_active_ad(self, now_ts: Optional[int] = None) -> tuple[Optional[str], Optional[str]]:
        now_ts = now_ts or int(time.time())
        rows = await self.active_ads(now_ts=now_ts, limit=2)
        if not rows:
            return (None, None)
        idx = (now_ts // 60) % len(rows)
        row = rows[idx]
        return (row["text"], row["link"])

    async def set_owner_fallback(self, text: str):
        await self.conn.execute("INSERT INTO state_kv(k,v) VALUES('owner_fallback_ad', ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (text,))
        await self.conn.commit()

    async def get_owner_fallback(self) -> Optional[str]:
        cur = await self.conn.execute("SELECT v FROM state_kv WHERE k='owner_fallback_ad'")
        row = await cur.fetchone()
        return row["v"] if row else None
