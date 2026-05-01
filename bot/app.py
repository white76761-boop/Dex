from __future__ import annotations
import asyncio
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

from bot.config import settings
from database.db import DB
from database.migrations import CREATE_TABLES
from utils.ton_rpc import TonClient
from bot.wizard import router as wizard_router
from bot.handlers import router as handlers_router
from services.buy_watcher import BuyWatcher
from services.leaderboard import LeaderboardUpdater

async def _migrate(db: DB):
    conn = await db.connect()
    for stmt in CREATE_TABLES:
        try:
            await conn.execute(stmt)
        except Exception:
            pass

    # best-effort schema upgrades
    upgrades = [
        "ALTER TABLE tracked_tokens ADD COLUMN telegram_link TEXT",
        "ALTER TABLE tracked_tokens ADD COLUMN symbol TEXT",
        "ALTER TABLE tracked_tokens ADD COLUMN name TEXT",
        "ALTER TABLE tracked_tokens ADD COLUMN force_trending INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE tracked_tokens ADD COLUMN force_leaderboard INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE tracked_tokens ADD COLUMN manual_rank INTEGER",
        "ALTER TABLE tracked_tokens ADD COLUMN trend_until_ts INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE ads ADD COLUMN link TEXT",
        "ALTER TABLE ads ADD COLUMN kind TEXT NOT NULL DEFAULT 'ad'",
        "ALTER TABLE token_settings ADD COLUMN media_kind TEXT NOT NULL DEFAULT 'photo'",
    ]
    for stmt in upgrades:
        try:
            await conn.execute(stmt)
        except Exception:
            pass

    await conn.commit()
    await conn.close()

async def run():
    load_dotenv()

    db = DB(settings.DATABASE_URL)
    await _migrate(db)

    bot = Bot(token=settings.BOT_TOKEN, parse_mode=ParseMode.HTML)
    dp = Dispatcher(storage=MemoryStorage())

    rpc = TonClient(settings.TONAPI_BASE, settings.TONAPI_KEY, settings.TONCENTER_BASE, settings.TONCENTER_API_KEY)

    # Attach shared objects to dispatcher workflow_data for Aiogram v3 DI.
    # Handlers can receive these as function parameters: (db: DB, rpc: TonRPC)
    dp.workflow_data.update({"db": db, "rpc": rpc})

    dp.include_router(handlers_router)
    dp.include_router(wizard_router)

    watcher = BuyWatcher(bot=bot, db=db, rpc=rpc)
    lb = LeaderboardUpdater(bot=bot, db=db)
    task = asyncio.create_task(watcher.run_forever())
    task_lb = asyncio.create_task(lb.run_forever())

    try:
        await dp.start_polling(bot)
    finally:
        task.cancel()
        task_lb.cancel()
        await lb.close()
        await watcher.close()
        await rpc.close()
        await bot.session.close()
