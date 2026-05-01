from __future__ import annotations
import asyncio
import re
import time
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

from bot.config import settings
from bot.keyboards import main_menu_kb, token_list_kb, trending_package_kb, advert_duration_kb, invoice_kb, lang_kb, token_edit_page_kb
from services.payment_verifier import find_recent_payment
from services.ads_service import AdsService
from services.token_meta import fetch_token_meta
from database.db import DB
from utils.ton_client import TonClient

router = Router()
CA_RE = re.compile(r"^(?:EQ|UQ)[A-Za-z0-9_-]{46}$|^(?:-?1|0):[A-Fa-f0-9]{64}$")

class TrendingFlow(StatesGroup):
    link = State()
    package = State()

class AdvertFlow(StatesGroup):
    link = State()
    content = State()
    duration = State()

class AddTokenFlow(StatesGroup):
    mint = State()
    tg = State()

class EditTokenFlow(StatesGroup):
    value = State()

class InvoiceFlow(StatesGroup):
    txhash = State()

TREND_PRICES = {
    "1h": (settings.TRENDING_1H_PRICE_TON, 3600, "1 Hours"),
    "3h": (settings.TRENDING_3H_PRICE_TON, 3 * 3600, "3 Hours"),
    "6h": (settings.TRENDING_6H_PRICE_TON, 6 * 3600, "6 Hours"),
    "9h": (settings.TRENDING_9H_PRICE_TON, 9 * 3600, "9 Hours"),
    "12h": (settings.TRENDING_12H_PRICE_TON, 12 * 3600, "12 Hours"),
    "24h": (settings.TRENDING_24H_PRICE_TON, 24 * 3600, "24 Hours"),
}
ADS_PRICES = {
    "1d": (settings.ADS_1D_PRICE_TON, 86400, "1 Day"),
    "3d": (settings.ADS_3D_PRICE_TON, 3 * 86400, "3 Days"),
    "7d": (settings.ADS_7D_PRICE_TON, 7 * 86400, "7 Days"),
}

def _is_owner(obj: Message | CallbackQuery) -> bool:
    return bool(obj.from_user and int(obj.from_user.id) == int(settings.OWNER_ID))

async def _ensure_owner(msg: Message) -> bool:
    if _is_owner(msg):
        return True
    uid = msg.from_user.id if msg.from_user else 'unknown'
    await msg.reply(f"❌ Owner command failed. Your Telegram ID is: <code>{uid}</code>\nSet Railway <code>OWNER_ID</code> to this exact number, then redeploy.", parse_mode='HTML')
    return False

def _parse_forceadd_args(raw: str) -> tuple[str, str | None]:
    raw = (raw or '').strip()
    if not raw:
        return '', None
    if '|' in raw:
        a,b = raw.split('|',1)
        return a.strip(), _norm_tg(b.strip()) if b.strip() else None
    parts = raw.split()
    mint = parts[0]
    tg = None
    for item in parts[1:]:
        if item.startswith('http://') or item.startswith('https://') or item.startswith('t.me/') or item.startswith('@'):
            tg = _norm_tg(item)
            break
    return mint, tg



def _extract_tx_sig(v: str) -> str:
    t = (v or '').strip()
    if 'tonviewer.com/transaction/' in t:
        t = t.split('tonviewer.com/transaction/', 1)[1]
    if 'ton.fm/tx/' in t:
        t = t.split('ton.fm/tx/', 1)[1]
    if '?' in t:
        t = t.split('?', 1)[0]
    if '#' in t:
        t = t.split('#', 1)[0]
    return t.rstrip('/').strip()

def _norm_tg(v: str | None) -> str | None:
    if not v:
        return None
    t = v.strip()
    if not t or t.lower() == "skip":
        return None
    if t.startswith("@"):
        return f"https://t.me/{t[1:]}"
    if t.startswith("t.me/"):
        return f"https://{t}"
    if t.startswith("http://"):
        return f"https://{t[7:]}"
    return t


def _extract_custom_emoji_html(msg: Message) -> str | None:
    entities = list(getattr(msg, 'entities', None) or [])
    text = msg.text or ''
    for ent in entities:
        if getattr(ent, 'type', None) == 'custom_emoji' and getattr(ent, 'custom_emoji_id', None):
            try:
                value = text[ent.offset: ent.offset + ent.length] or '✨'
            except Exception:
                value = '✨'
            return f'<tg-emoji emoji-id="{ent.custom_emoji_id}">{value}</tg-emoji>'
    raw = (msg.text or '').strip()
    if '<tg-emoji' in raw and 'emoji-id=' in raw:
        return raw
    return None


def _normalize_emoji_input(msg: Message) -> str:
    premium = _extract_custom_emoji_html(msg)
    if premium:
        return premium
    raw = (msg.text or '🟢').strip() or '🟢'
    return raw[:16]

async def _tokens(db: DB) -> list[tuple[str, str]]:
    conn = await db.connect()
    cur = await conn.execute("SELECT mint, COALESCE(symbol, name, mint) AS label FROM tracked_tokens ORDER BY created_at DESC LIMIT 50")
    rows = await cur.fetchall()
    await conn.close()
    return [(r["mint"], r["label"]) for r in rows]

async def _group_token(db: DB, group_id: int) -> str | None:
    conn = await db.connect()
    cur = await conn.execute("SELECT token_mint FROM group_settings WHERE group_id=? AND is_active=1", (group_id,))
    row = await cur.fetchone()
    await conn.close()
    return row[0] if row else None

async def _group_token_entry(db: DB, group_id: int) -> tuple[str, str] | None:
    conn = await db.connect()
    cur = await conn.execute(
        "SELECT g.token_mint, COALESCE(t.symbol, t.name, g.token_mint) AS label FROM group_settings g LEFT JOIN tracked_tokens t ON t.mint=g.token_mint WHERE g.group_id=? AND g.is_active=1",
        (group_id,),
    )
    row = await cur.fetchone()
    await conn.close()
    if not row:
        return None
    return (row["token_mint"], row["label"])


async def _latest_pending_invoice_for_user(db: DB, user_id: int):
    conn = await db.connect()
    cur = await conn.execute("SELECT id FROM invoices WHERE user_id=? AND status='pending' ORDER BY created_at DESC LIMIT 1", (user_id,))
    row = await cur.fetchone()
    await conn.close()
    return int(row[0]) if row else None

async def _ensure_token_settings(db: DB, mint: str):
    conn = await db.connect()
    await conn.execute("INSERT OR IGNORE INTO token_settings(mint, created_at) VALUES(?,?)", (mint, int(time.time())))
    await conn.commit()
    await conn.close()

async def _render_edit_page(db: DB, mint: str) -> tuple[str, dict]:
    conn = await db.connect()
    cur = await conn.execute("SELECT COALESCE(name, symbol, mint) AS label, telegram_link FROM tracked_tokens WHERE mint=?", (mint,))
    tr = await cur.fetchone()
    cur = await conn.execute("SELECT buy_step, min_buy, emoji, media_file_id, COALESCE(media_kind,'photo') AS media_kind FROM token_settings WHERE mint=?", (mint,))
    ts = await cur.fetchone()
    await conn.close()
    label = tr["label"] if tr else mint[:6]
    values = {
        "buy_step": ts["buy_step"] if ts else 1,
        "min_buy": float(ts["min_buy"] or 0) if ts else 0.0,
        "emoji": ts["emoji"] if ts and ts["emoji"] else "🟢",
        "media_file_id": ts["media_file_id"] if ts else None,
        "media_kind": ts["media_kind"] if ts else "photo",
        "telegram_link": tr["telegram_link"] if tr else None,
    }
    text = f"Customize your Token\n\n<code>{mint}</code>\n\nName: <b>{label}</b>"
    return text, values

async def _create_invoice(db: DB, user_id: int, username: str | None, token_mint: str, kind: str, link: str | None, content: str | None, amount_sol: float, duration_sec: int) -> int:
    conn = await db.connect()
    cur = await conn.execute(
        "INSERT INTO invoices(user_id, username, token_mint, kind, link, content, amount_sol, duration_sec, wallet, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (user_id, username, token_mint, kind, link, content, amount_sol, duration_sec, settings.PAYMENT_WALLET, int(time.time())),
    )
    await conn.commit()
    iid = int(cur.lastrowid)
    await conn.close()
    return iid

async def _invoice_text(db: DB, invoice_id: int) -> tuple[str, float]:
    conn = await db.connect()
    cur = await conn.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,))
    inv = await cur.fetchone()
    await conn.close()
    title = "Trending" if inv["kind"] == "trending" else "Advert"
    text = (
        f"💱 <b>Invoice</b>\n\n"
        f"Paying for: <b>{title}</b>\n\n"
        f"Wallet:\n<code>{inv['wallet']}</code>\n"
        f"Wallet Balance: 0 TON\n\n"
        f"⋙ Please send <b>{inv['amount_sol']:g} TON</b> to the wallet above\n\n"
        f"After payment, the bot verifies it automatically in the background.\n"
        f"You can also tap <b>Refresh</b> to check instantly."
    )
    return text, float(inv["amount_sol"])

async def _activation_notice(db: DB, invoice_id: int) -> str:
    conn = await db.connect()
    cur = await conn.execute("SELECT i.kind, i.token_mint, i.duration_sec, COALESCE(t.symbol, t.name, i.token_mint) AS label FROM invoices i LEFT JOIN tracked_tokens t ON t.mint=i.token_mint WHERE i.id=?", (invoice_id,))
    row = await cur.fetchone()
    await conn.close()
    if not row:
        return "✅ Payment verified and campaign activated."
    duration_sec = int(row["duration_sec"] or 0)
    if row["kind"] == "trending":
        hours = max(1, duration_sec // 3600)
        return f"✅ Payment verified.\n🔥 {row['label']} started trending for {hours} hour{'s' if hours != 1 else ''}."
    days = max(1, duration_sec // 86400)
    return f"✅ Payment verified.\n💎 {row['label']} advert started for {days} day{'s' if days != 1 else ''}."

async def _used_signatures(db: DB) -> set[str]:
    conn = await db.connect()
    cur = await conn.execute("SELECT tx_sig FROM invoices WHERE tx_sig IS NOT NULL")
    rows = await cur.fetchall()
    await conn.close()
    return {r[0] for r in rows if r[0]}

async def _check_invoice_payment(db: DB, rpc: TonClient, invoice_id: int):
    conn = await db.connect()
    cur = await conn.execute("SELECT status, amount_sol FROM invoices WHERE id=?", (invoice_id,))
    inv = await cur.fetchone()
    await conn.close()
    if not inv:
        return (False, "Invoice not found.")
    if inv["status"] == "paid":
        return (True, "Already paid.")
    used = await _used_signatures(db)
    res = await find_recent_payment(rpc, settings.PAYMENT_WALLET, float(inv["amount_sol"]), used)
    if not res.ok or not res.signature:
        return (False, "Payment not detected yet.")
    if await _activate_invoice(db, invoice_id, res.signature, res.amount_sol):
        return (True, await _activation_notice(db, invoice_id))
    return (True, "Already paid.")

async def _activate_invoice(db: DB, invoice_id: int, sig: str, amount_sol: float):
    conn = await db.connect()
    cur = await conn.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,))
    inv = await cur.fetchone()
    if not inv or inv["status"] == "paid":
        await conn.close()
        return False
    now = int(time.time())
    await conn.execute("UPDATE invoices SET status='paid', tx_sig=?, verified_at=? WHERE id=?", (sig, now, invoice_id))
    if inv["kind"] == "trending":
        await conn.execute(
            "UPDATE tracked_tokens SET force_trending=1, force_leaderboard=1, trend_until_ts=?, telegram_link=COALESCE(?, telegram_link) WHERE mint=?",
            (now + int(inv["duration_sec"]), inv["link"], inv["token_mint"]),
        )
    else:
        ads = AdsService(conn)
        await ads.create_ad(inv["user_id"], inv["content"] or "", inv["link"], now, now + int(inv["duration_sec"]), sig, amount_sol, "ad")
    await conn.commit()
    await conn.close()
    return True

async def _watch_invoice(bot, db: DB, rpc: TonClient, chat_id: int, invoice_id: int):
    for _ in range(18):
        await asyncio.sleep(10)
        conn = await db.connect()
        cur = await conn.execute("SELECT status, amount_sol FROM invoices WHERE id=?", (invoice_id,))
        inv = await cur.fetchone()
        await conn.close()
        if not inv or inv["status"] == "paid":
            return
        used = await _used_signatures(db)
        res = await find_recent_payment(rpc, settings.PAYMENT_WALLET, float(inv["amount_sol"]), used)
        if res.ok and res.signature:
            if await _activate_invoice(db, invoice_id, res.signature, res.amount_sol):
                await bot.send_message(chat_id, await _activation_notice(db, invoice_id))
            return

async def _upsert_tracked_token(db: DB, mint: str, telegram_link: str | None = None):
    meta = await fetch_token_meta(mint)
    conn = await db.connect()
    await conn.execute(
        "INSERT INTO tracked_tokens(mint, post_mode, telegram_link, symbol, name, force_trending, force_leaderboard, created_at) VALUES(?,?,?,?,?,?,?,?) "
        "ON CONFLICT(mint) DO UPDATE SET telegram_link=COALESCE(excluded.telegram_link, tracked_tokens.telegram_link), symbol=excluded.symbol, name=excluded.name",
        (mint, "channel", telegram_link, meta.get("symbol"), meta.get("name"), 0, 0, int(time.time())),
    )
    await conn.commit()
    await conn.close()
    await _ensure_token_settings(db, mint)
    return meta

@router.message(Command("start"))
async def start(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer("BazaTon main menu", reply_markup=main_menu_kb())

@router.callback_query(F.data == "menu:home")
async def menu_home(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    await cq.message.answer("BazaTon main menu", reply_markup=main_menu_kb())
    await cq.answer()

@router.callback_query(F.data == "menu:lang")
async def menu_lang(cq: CallbackQuery):
    await cq.message.answer("Choose your buybot language.", reply_markup=lang_kb())
    await cq.answer()

@router.callback_query(F.data.startswith("lang:set:"))
async def lang_set(cq: CallbackQuery):
    await cq.message.answer("✅ Language updated.")
    await cq.answer()

@router.callback_query(F.data == "menu:add")
async def menu_add(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(AddTokenFlow.mint)
    await cq.message.answer("⬇️ Paste the token contract address")
    await cq.answer()

@router.message(AddTokenFlow.mint)
async def add_token_mint(msg: Message, state: FSMContext, db: DB):
    mint = (msg.text or "").strip()
    if not CA_RE.match(mint):
        return await msg.reply("Send a valid TON jetton contract address.")
    meta = await _upsert_tracked_token(db, mint)
    if msg.chat.type in ("group", "supergroup"):
        conn = await db.connect()
        await conn.execute(
            "INSERT INTO group_settings(group_id, token_mint, min_buy_sol, emoji, telegram_link, media_file_id, is_active, created_at) VALUES(?,?,?,?,?,?,1,?) "
            "ON CONFLICT(group_id) DO UPDATE SET token_mint=excluded.token_mint, is_active=1",
            (msg.chat.id, mint, float(settings.MIN_BUY_DEFAULT_TON), "🟢", None, None, int(time.time())),
        )
        await conn.commit()
        await conn.close()
        await state.clear()
        return await msg.answer(
            f"✅ Token Added\n• Token: {meta.get('name') or meta.get('symbol') or mint[:6]}\n• Symbol: {meta.get('symbol') or '—'}\n\nNow posting buys automatically for this group.\nUse Edit to customize token settings.",
            reply_markup=main_menu_kb(),
        )
    await state.update_data(token_mint=mint)
    await state.set_state(AddTokenFlow.tg)
    await msg.answer("Send token Telegram link or type skip.")

@router.message(AddTokenFlow.tg)
async def add_token_tg(msg: Message, state: FSMContext, db: DB):
    mint = (await state.get_data()).get("token_mint")
    conn = await db.connect()
    await conn.execute("UPDATE tracked_tokens SET telegram_link=? WHERE mint=?", (_norm_tg(msg.text), mint))
    await conn.commit(); await conn.close()
    await state.clear()
    await msg.answer("✅ Token saved.", reply_markup=main_menu_kb())

@router.callback_query(F.data == "menu:view")
async def menu_view(cq: CallbackQuery, db: DB):
    mint = None
    if cq.message and cq.message.chat.type in ("group", "supergroup"):
        mint = await _group_token(db, cq.message.chat.id)
    if mint:
        conn = await db.connect()
        cur = await conn.execute("SELECT * FROM tracked_tokens WHERE mint=?", (mint,))
        row = await cur.fetchone(); await conn.close()
        if row:
            await cq.message.answer(
                f"Token Details\nName: <b>{row['name'] or row['symbol'] or mint[:6]}</b>\nSymbol: <b>{row['symbol'] or '—'}</b>\nMint: <code>{mint}</code>\nTelegram: {row['telegram_link'] or '—'}",
                parse_mode="HTML",
            )
            return await cq.answer()
    tokens = await _tokens(db)
    if not tokens:
        await cq.message.answer("No tracked tokens yet.")
    else:
        await cq.message.answer("👀 Select a token below.", reply_markup=token_list_kb(tokens, "viewtoken", back="menu:home"))
    await cq.answer()

@router.callback_query(F.data.startswith("viewtoken:"))
async def view_token(cq: CallbackQuery, db: DB):
    mint = cq.data.split(":", 1)[1]
    conn = await db.connect()
    cur = await conn.execute("SELECT * FROM tracked_tokens WHERE mint=?", (mint,))
    row = await cur.fetchone(); await conn.close()
    if not row:
        return await cq.answer("Token not found", show_alert=True)
    await cq.message.answer(
        f"Token Details\nName: <b>{row['name'] or row['symbol'] or mint[:6]}</b>\nSymbol: <b>{row['symbol'] or '—'}</b>\nMint: <code>{mint}</code>\nTelegram: {row['telegram_link'] or '—'}",
        parse_mode="HTML",
    )
    await cq.answer()

@router.callback_query(F.data == "menu:edit")
async def menu_edit(cq: CallbackQuery, state: FSMContext, db: DB):
    # Answer immediately so Telegram does not leave the button stuck on "Loading...".
    try:
        await cq.answer()
    except Exception:
        pass
    mint = None
    if cq.message and cq.message.chat.type in ("group", "supergroup"):
        mint = await _group_token(db, cq.message.chat.id)
    if mint:
        await _ensure_token_settings(db, mint)
        await state.clear()
        text2, values = await _render_edit_page(db, mint)
        await cq.message.answer(text2, parse_mode="HTML", reply_markup=token_edit_page_kb(mint, 1, values))
        return
    tokens = await _tokens(db)
    if not tokens:
        await cq.message.answer("No tracked tokens yet.")
    else:
        await cq.message.answer("Hi, please select your token below.", reply_markup=token_list_kb(tokens, "edittoken", back="menu:home"))

@router.callback_query(F.data.startswith("edittoken:"))
async def edit_token(cq: CallbackQuery, state: FSMContext, db: DB):
    try:
        await cq.answer()
    except Exception:
        pass
    mint = cq.data.split(":", 1)[1]
    try:
        await _ensure_token_settings(db, mint)
        await state.clear()
        text2, values = await _render_edit_page(db, mint)
        await cq.message.answer(text2, parse_mode="HTML", reply_markup=token_edit_page_kb(mint, 1, values))
    except Exception as e:
        await cq.answer("Could not open token editor.", show_alert=True)

@router.callback_query(F.data.startswith("editpage:"))
async def edit_page(cq: CallbackQuery, db: DB):
    try:
        await cq.answer()
    except Exception:
        pass
    mint = cq.data.split(":")[1]
    text, values = await _render_edit_page(db, mint)
    await cq.message.answer(text, parse_mode="HTML", reply_markup=token_edit_page_kb(mint, 1, values))

@router.callback_query(F.data.startswith("editset:"))
async def edit_set(cq: CallbackQuery, state: FSMContext):
    try:
        await cq.answer()
    except Exception:
        pass
    _, mint, key = cq.data.split(":", 2)
    await state.clear()
    await state.set_state(EditTokenFlow.value)
    await state.update_data(edit_mint=mint, edit_key=key)
    prompts = {
        "buy_step": "Send buy step number.",
        "min_buy": "Send minimum buy in TON.",
        "link": "Send Telegram link or type skip.",
        "emoji": "Send emoji.",
        "media": "Send a photo, GIF, or video to use as media, or type skip to clear it.",
    }
    await cq.message.answer(prompts.get(key, "Send value."))

@router.message(EditTokenFlow.value)
async def edit_token_value(msg: Message, state: FSMContext, db: DB):
    data = await state.get_data()
    mint = data.get("edit_mint")
    key = data.get("edit_key")
    if not mint:
        await state.clear()
        return await msg.answer("Please open Edit again.")
    conn = await db.connect()
    await conn.execute("INSERT OR IGNORE INTO token_settings(mint, created_at) VALUES(?,?)", (mint, int(time.time())))
    if key == "link":
        await conn.execute("UPDATE tracked_tokens SET telegram_link=? WHERE mint=?", (_norm_tg(msg.text), mint))
    elif key == "buy_step":
        await conn.execute("UPDATE token_settings SET buy_step=? WHERE mint=?", (max(1, int(float((msg.text or '1').strip()))), mint))
    elif key == "min_buy":
        await conn.execute("UPDATE token_settings SET min_buy=? WHERE mint=?", (max(0.0, float((msg.text or '0').strip())), mint))
    elif key == "emoji":
        await conn.execute("UPDATE token_settings SET emoji=? WHERE mint=?", (_normalize_emoji_input(msg), mint))
    elif key == "media":
        txt = (msg.text or '').strip().lower()
        if txt == 'skip':
            await conn.execute("UPDATE token_settings SET media_file_id=NULL, media_kind='photo' WHERE mint=?", (mint,))
        elif msg.photo:
            await conn.execute("UPDATE token_settings SET media_file_id=?, media_kind='photo' WHERE mint=?", (msg.photo[-1].file_id, mint))
        elif getattr(msg, 'animation', None):
            await conn.execute("UPDATE token_settings SET media_file_id=?, media_kind='animation' WHERE mint=?", (msg.animation.file_id, mint))
        elif getattr(msg, 'video', None):
            await conn.execute("UPDATE token_settings SET media_file_id=?, media_kind='video' WHERE mint=?", (msg.video.file_id, mint))
        elif getattr(msg, 'animation', None):
            await conn.execute("UPDATE token_settings SET media_file_id=?, media_kind='animation' WHERE mint=?", (msg.animation.file_id, mint))
        elif getattr(msg, 'document', None):
            mime = (getattr(msg.document, 'mime_type', '') or '').lower()
            if 'gif' in mime:
                kind = 'animation'
            elif mime.startswith('video/') or mime == 'application/octet-stream':
                kind = 'video'
            else:
                kind = 'document'
            await conn.execute("UPDATE token_settings SET media_file_id=?, media_kind=? WHERE mint=?", (msg.document.file_id, kind, mint))
        else:
            await conn.close()
            return await msg.answer('Send a photo, GIF, or video, or type skip.')
    await conn.commit(); await conn.close()
    await state.clear()
    text, values = await _render_edit_page(db, mint)
    await msg.answer("✅ Token updated.")
    await msg.answer(text, parse_mode="HTML", reply_markup=token_edit_page_kb(mint, 1, values))

@router.callback_query(F.data == "menu:group")
async def menu_group(cq: CallbackQuery):
    await cq.message.answer("⚙️ Group settings are managed from the token you add to this group.")
    await cq.answer()

@router.callback_query(F.data == "menu:advert")
async def advert_menu(cq: CallbackQuery, db: DB, state: FSMContext):
    await state.clear()
    if cq.message and cq.message.chat.type in ("group", "supergroup"):
        group_token = await _group_token_entry(db, cq.message.chat.id)
        if not group_token:
            await cq.message.answer("No active token is set for this group. Use ➕ Add Token first.")
        else:
            mint, label = group_token
            await cq.message.answer(
                f"💎 Advertise your token\nPromote your token to millions of users across thousands of groups.\n\nThis booking will use this group's token only:\n<b>{label}</b>",
                parse_mode="HTML",
                reply_markup=token_list_kb([(mint, label)], "adtoken", back="menu:home"),
            )
        await cq.answer()
        return
    tokens = await _tokens(db)
    if not tokens:
        await cq.message.answer("No tracked tokens yet. Use ➕ Add Token first.")
    else:
        await cq.message.answer(
            "💎 Advertise your token\nPromote your token to millions of users across thousands of groups.\n\nSelect your token to continue.",
            reply_markup=token_list_kb(tokens, "adtoken", back="menu:home"),
        )
    await cq.answer()
@router.callback_query(F.data.startswith("adtoken:"))
async def advert_pick_token(cq: CallbackQuery, state: FSMContext):
    mint = cq.data.split(":", 1)[1]
    meta = await fetch_token_meta(mint)
    label = meta.get("symbol") or meta.get("name") or mint[:6]
    await state.clear(); await state.set_state(AdvertFlow.link)
    await state.update_data(token_mint=mint, token_label=label)
    await cq.message.answer(f"💎 Fill in the advert form to finish.\nToken: <b>{label}</b>", parse_mode="HTML")
    await cq.message.answer("⬇️ Send your Telegram group/channel link (e.g. https://t.me/BazaTonPortal)")
    await cq.answer()

@router.message(AdvertFlow.link)
async def advert_link(msg: Message, state: FSMContext):
    await state.update_data(link=(msg.text or "").strip())
    await state.set_state(AdvertFlow.content)
    await msg.answer("⬇️ Enter your advert text.")

@router.message(AdvertFlow.content)
async def advert_content(msg: Message, state: FSMContext):
    await state.update_data(content=(msg.text or "").strip())
    await state.set_state(AdvertFlow.duration)
    await msg.answer("How many days should this advert run?", reply_markup=advert_duration_kb())

@router.callback_query(F.data.startswith("adpkg:"))
async def advert_duration(cq: CallbackQuery, state: FSMContext, db: DB, rpc: TonClient):
    key = cq.data.split(":", 1)[1]
    if key not in ADS_PRICES:
        return await cq.answer()
    data = await state.get_data()
    if not data.get("token_mint"):
        return await cq.answer("Please select token again.", show_alert=True)
    conn = await db.connect(); ads = AdsService(conn); active = await ads.active_ads(limit=3); await conn.close()
    if len(active) >= 2:
        return await cq.answer("Slot not available right now.", show_alert=True)
    price, seconds, label = ADS_PRICES[key]
    invoice_id = await _create_invoice(db, cq.from_user.id, cq.from_user.username, data["token_mint"], "ad", data.get("link"), data.get("content"), price, seconds)
    text, amount = await _invoice_text(db, invoice_id)
    await cq.message.answer(text, reply_markup=invoice_kb(invoice_id, amount), disable_web_page_preview=True)
    await state.clear(); asyncio.create_task(_watch_invoice(cq.bot, db, rpc, cq.message.chat.id, invoice_id))
    await cq.answer(f"Invoice created for {label}")

@router.message(AdvertFlow.duration)
async def advert_duration_text(msg: Message, state: FSMContext, db: DB, rpc: TonClient):
    key = {'1 day':'1d','3 days':'3d','7 days':'7d'}.get((msg.text or '').strip().lower())
    if not key:
        return
    conn = await db.connect(); ads = AdsService(conn); active = await ads.active_ads(limit=3); await conn.close()
    if len(active) >= 2:
        await state.clear(); return await msg.answer('Slot not available right now.')
    price, seconds, _ = ADS_PRICES[key]
    data = await state.get_data()
    invoice_id = await _create_invoice(db, msg.from_user.id, msg.from_user.username, data["token_mint"], "ad", data.get("link"), data.get("content"), price, seconds)
    text, amount = await _invoice_text(db, invoice_id)
    await msg.answer(text, reply_markup=invoice_kb(invoice_id, amount), disable_web_page_preview=True)
    await state.clear(); asyncio.create_task(_watch_invoice(msg.bot, db, rpc, msg.chat.id, invoice_id))

@router.callback_query(F.data == "menu:trending")
async def trending_menu(cq: CallbackQuery, db: DB, state: FSMContext):
    await state.clear()
    if cq.message and cq.message.chat.type in ("group", "supergroup"):
        group_token = await _group_token_entry(db, cq.message.chat.id)
        if not group_token:
            await cq.message.answer("No active token is set for this group. Use ➕ Add Token first.")
        else:
            mint, label = group_token
            await cq.message.answer(
                f"<blockquote>Your token will be shown here:\n@BazaTonTrending.\nChoose how many hours you want your token to trend.</blockquote>\n\n🎉 This booking will use this group's token only:\n<b>{label}</b>",
                parse_mode="HTML",
                reply_markup=token_list_kb([(mint, label)], "trendtoken", back="menu:home"),
            )
        await cq.answer()
        return
    tokens = await _tokens(db)
    if not tokens:
        await cq.message.answer("No tracked tokens yet. Use ➕ Add Token first.")
    else:
        await cq.message.answer(
            "<blockquote>Your token will be shown here:\n@BazaTonTrending.\nChoose how many hours you want your token to trend.</blockquote>\n\n🎉 Hi, please select your token below.",
            parse_mode="HTML",
            reply_markup=token_list_kb(tokens, "trendtoken", back="menu:home"),
        )
    await cq.answer()
@router.callback_query(F.data.startswith("trendtoken:"))
async def trending_pick_token(cq: CallbackQuery, state: FSMContext):
    mint = cq.data.split(":", 1)[1]
    meta = await fetch_token_meta(mint)
    label = meta.get("symbol") or meta.get("name") or mint[:6]
    await state.clear(); await state.set_state(TrendingFlow.link)
    await state.update_data(token_mint=mint, token_label=label, package="1h")
    await cq.message.answer("⬇️ Send your Telegram group/channel link (e.g. https://t.me/BazaTonPortal)")
    await cq.answer()

@router.message(TrendingFlow.link)
async def trending_link(msg: Message, state: FSMContext):
    await state.update_data(link=(msg.text or "").strip())
    await state.set_state(TrendingFlow.package)
    data = await state.get_data()
    package = data.get('package', '1h')
    price, _, label = TREND_PRICES[package]
    await msg.answer(f"📊 Almost done. Choose your Trending package, then provide a link.\nToken: {data.get('token_label', 'Token')}\nDuration: {label}\nPrice: {price:g} TON\nLink: {data.get('link') or '—'}", reply_markup=trending_package_kb(package))

@router.callback_query(F.data.startswith("trendpkg:"))
async def trending_package(cq: CallbackQuery, state: FSMContext, db: DB, rpc: TonClient):
    action = cq.data.split(":", 1)[1]
    data = await state.get_data()
    if not data.get("token_mint"):
        return await cq.answer("Please select token again.", show_alert=True)
    if action in TREND_PRICES:
        await state.update_data(package=action)
        price, _, label = TREND_PRICES[action]
        await cq.message.answer(f"📊 Almost done. Choose your Trending package, then provide a link.\nToken: {data.get('token_label', 'Token')}\nDuration: {label}\nPrice: {price:g} TON\nLink: {data.get('link') or '—'}", reply_markup=trending_package_kb(action))
        return await cq.answer()
    if action == 'continue':
        if not data.get('link'):
            await state.set_state(TrendingFlow.link)
            await cq.message.answer('⬇️ Send your Telegram group/channel link (e.g. https://t.me/BazaTonPortal)')
            return await cq.answer()
        package = data.get('package', '1h')
        price, seconds, _ = TREND_PRICES[package]
        invoice_id = await _create_invoice(db, cq.from_user.id, cq.from_user.username, data['token_mint'], 'trending', data.get('link'), None, price, seconds)
        text, amount = await _invoice_text(db, invoice_id)
        await cq.message.answer(text, reply_markup=invoice_kb(invoice_id, amount), disable_web_page_preview=True)
        await state.clear(); asyncio.create_task(_watch_invoice(cq.bot, db, rpc, cq.message.chat.id, invoice_id))
        return await cq.answer('Invoice created')
    await cq.answer()

@router.message(TrendingFlow.package)
async def trending_package_text(msg: Message, state: FSMContext, db: DB, rpc: TonClient):
    mapping = {'1 hours':'1h','1 hour':'1h','3 hours':'3h','3 hour':'3h','6 hours':'6h','6 hour':'6h','9 hours':'9h','9 hour':'9h','12 hours':'12h','12 hour':'12h','24 hours':'24h','24 hour':'24h','continue →':'continue','continue':'continue'}
    action = mapping.get((msg.text or '').strip().lower())
    if not action:
        return
    data = await state.get_data()
    if action in TREND_PRICES:
        await state.update_data(package=action)
        price, _, label = TREND_PRICES[action]
        return await msg.answer(f"📊 Almost done. Choose your Trending package, then provide a link.\nToken: {data.get('token_label', 'Token')}\nDuration: {label}\nPrice: {price:g} TON\nLink: {data.get('link') or '—'}", reply_markup=trending_package_kb(action))
    if not data.get('link'):
        await state.set_state(TrendingFlow.link)
        return await msg.answer('⬇️ Send your Telegram group/channel link (e.g. https://t.me/BazaTonPortal)')
    package = data.get('package', '1h')
    price, seconds, _ = TREND_PRICES[package]
    invoice_id = await _create_invoice(db, msg.from_user.id, msg.from_user.username, data['token_mint'], 'trending', data.get('link'), None, price, seconds)
    text, amount = await _invoice_text(db, invoice_id)
    await msg.answer(text, reply_markup=invoice_kb(invoice_id, amount), disable_web_page_preview=True)
    await state.clear(); asyncio.create_task(_watch_invoice(msg.bot, db, rpc, msg.chat.id, invoice_id))

@router.callback_query(F.data.startswith("invoice:paid:"))
async def invoice_paid(cq: CallbackQuery, db: DB, rpc: TonClient):
    invoice_id = int(cq.data.rsplit(':', 1)[1])
    ok, message = await _check_invoice_payment(db, rpc, invoice_id)
    if ok and message.startswith("✅"):
        await cq.message.answer(message)
        return await cq.answer("Verified")
    await cq.answer(message, show_alert=True)

@router.callback_query(F.data.startswith("invoice:txhash:"))
async def invoice_txhash_prompt(cq: CallbackQuery, state: FSMContext):
    invoice_id = int(cq.data.rsplit(':', 1)[1])
    await state.clear()
    await state.set_state(InvoiceFlow.txhash)
    await state.update_data(invoice_id=invoice_id)
    await cq.message.answer("Send your transaction hash and I will verify the payment.")
    await cq.answer()

@router.message(InvoiceFlow.txhash)
async def invoice_txhash_submit(msg: Message, state: FSMContext, db: DB, rpc: TonClient):
    invoice_id = (await state.get_data()).get("invoice_id")
    sig = _extract_tx_sig((msg.text or '').strip())
    if not invoice_id:
        invoice_id = await _latest_pending_invoice_for_user(db, msg.from_user.id)
    if not invoice_id or len(sig) < 20:
        return await msg.answer('Send a valid transaction hash or Tonviewer link.')
    conn = await db.connect()
    cur = await conn.execute("SELECT status, amount_sol FROM invoices WHERE id=?", (invoice_id,))
    inv = await cur.fetchone()
    await conn.close()
    if not inv:
        await state.clear()
        return await msg.answer("Invoice not found.")
    if inv["status"] == "paid":
        await state.clear()
        return await msg.answer("✅ Already paid.")
    used = await _used_signatures(db)
    if sig in used:
        await state.clear()
        return await msg.answer("This transaction hash was already used.")
    await msg.answer('Checking transaction hash...')
    try:
        from services.payment_verifier import verify_sol_transfer
        res = await verify_sol_transfer(rpc, sig, settings.PAYMENT_WALLET, float(inv['amount_sol']))
    except Exception:
        await state.clear()
        return await msg.answer('Could not check that transaction right now. Please tap Refresh in a moment.')
    if not res.ok or not res.signature:
        await state.clear()
        return await msg.answer(f'❌ Payment not detected. {res.reason}')
    if await _activate_invoice(db, int(invoice_id), res.signature, res.amount_sol):
        await msg.answer('✅ Payment verified.')
        await msg.answer(await _activation_notice(db, int(invoice_id)))
    else:
        await msg.answer('✅ Already paid.')
    await state.clear()

@router.callback_query(F.data.startswith("invoice:refresh:"))
async def invoice_refresh(cq: CallbackQuery, db: DB, rpc: TonClient):
    invoice_id = int(cq.data.rsplit(':', 1)[1])
    ok, message = await _check_invoice_payment(db, rpc, invoice_id)
    if ok and message.startswith("✅"):
        await cq.message.answer(message)
        return await cq.answer("Verified")
    await cq.answer(message, show_alert=True)



@router.message(F.chat.type.in_({"group", "supergroup"}), F.text.func(lambda t: (t or "").strip().lower() == "ca"))
async def send_group_token_ca(msg: Message, db: DB):
    mint = await _group_token(db, msg.chat.id)
    if not mint:
        return await msg.reply("No token contract address is set for this group yet.")
    await msg.reply(f"<code>{mint}</code>", parse_mode="HTML", disable_web_page_preview=True)

@router.message(Command("whoami"))
async def whoami(msg: Message):
    uid = msg.from_user.id if msg.from_user else 'unknown'
    await msg.reply(f"Your Telegram ID: <code>{uid}</code>", parse_mode='HTML')

@router.message(Command("tokens"))
async def tokens_cmd(msg: Message, db: DB):
    rows = await _tokens(db)
    if not rows:
        return await msg.reply("No tracked tokens.")
    text = "Tracked tokens:\n" + "\n".join([f"• {label} — <code>{mint}</code>" for mint, label in rows])
    await msg.reply(text, parse_mode="HTML")

@router.message(Command("forceadd"))
async def forceadd(msg: Message, command: CommandObject, db: DB):
    if not await _ensure_owner(msg):
        return
    if not command.args:
        return await msg.reply("Usage:\n<code>/forceadd CA|https://t.me/yourlink</code>\nOr:\n<code>/forceadd CA https://t.me/yourlink</code>", parse_mode="HTML")
    mint, tg = _parse_forceadd_args(command.args)
    if not mint:
        return await msg.reply("❌ Missing token mint.")
    meta = await _upsert_tracked_token(db, mint, tg)
    await msg.reply(f"✅ Token added: {meta.get('symbol') or meta.get('name') or mint[:6]}")

@router.message(Command("forcetrending"))
async def forcetrending(msg: Message, command: CommandObject, db: DB):
    if not await _ensure_owner(msg):
        return
    if not command.args:
        return await msg.reply("Usage:\n<code>/forcetrending CA [hours] [telegram_link]</code>", parse_mode="HTML")
    mint, tg = _parse_forceadd_args(command.args)
    if not mint:
        return await msg.reply("❌ Missing token mint.")
    parts = command.args.split()
    hours = 24
    for item in parts[1:]:
        if item.isdigit():
            hours = int(item)
            break
    meta = await _upsert_tracked_token(db, mint, tg)
    conn = await db.connect()
    await conn.execute("UPDATE tracked_tokens SET post_mode='channel', force_trending=1, force_leaderboard=1, trend_until_ts=?, telegram_link=COALESCE(?, telegram_link) WHERE mint=?", (int(time.time()) + hours * 3600, tg, mint))
    await conn.commit(); await conn.close()
    await msg.reply(f"✅ {meta.get('symbol') or meta.get('name') or mint[:6]} forced into trending for {hours}h.")

@router.message(Command("forceleaderboard"))
async def forceleaderboard(msg: Message, command: CommandObject, db: DB):
    if not await _ensure_owner(msg):
        return
    if not command.args:
        return await msg.reply("Usage:\n<code>/forceleaderboard CA</code>", parse_mode="HTML")
    mint = command.args.strip().split()[0]
    await _upsert_tracked_token(db, mint)
    conn = await db.connect()
    await conn.execute("UPDATE tracked_tokens SET force_leaderboard=1 WHERE mint=?", (mint,))
    await conn.commit(); await conn.close()
    await msg.reply("✅ Token forced into leaderboard.")

@router.message(Command("removetrending"))
async def removetrending(msg: Message, command: CommandObject, db: DB):
    if not await _ensure_owner(msg):
        return
    if not command.args:
        return await msg.reply("Usage:\n<code>/removetrending CA</code>", parse_mode="HTML")
    mint = command.args.strip().split()[0]
    conn = await db.connect()
    await conn.execute("UPDATE tracked_tokens SET force_trending=0, trend_until_ts=0 WHERE mint=?", (mint,))
    await conn.commit(); await conn.close()
    await msg.reply("✅ Trending removed for token.")

@router.message(Command("disabletoken"))
async def disabletoken(msg: Message, command: CommandObject, db: DB):
    if not await _ensure_owner(msg):
        return
    if not command.args:
        return await msg.reply("Usage:\n<code>/disabletoken CA</code>", parse_mode="HTML")
    mint = command.args.strip().split()[0]
    conn = await db.connect()
    await conn.execute("UPDATE tracked_tokens SET post_mode='disabled', force_trending=0 WHERE mint=?", (mint,))
    await conn.commit(); await conn.close()
    await msg.reply("✅ Token disabled.")

@router.message(Command("enabletoken"))
async def enabletoken(msg: Message, command: CommandObject, db: DB):
    if not await _ensure_owner(msg):
        return
    if not command.args:
        return await msg.reply("Usage:\n<code>/enabletoken CA</code>", parse_mode="HTML")
    mint = command.args.strip().split()[0]
    await _upsert_tracked_token(db, mint)
    conn = await db.connect()
    await conn.execute("UPDATE tracked_tokens SET post_mode='channel' WHERE mint=?", (mint,))
    await conn.commit(); await conn.close()
    await msg.reply("✅ Token enabled for channel posting.")

@router.message(Command("setglobalad"))
async def setglobalad(msg: Message, command: CommandObject, db: DB):
    if not await _ensure_owner(msg):
        return
    if not command.args:
        return await msg.reply("Usage:\n<code>/setglobalad your ad text</code>", parse_mode="HTML")
    conn = await db.connect(); ads = AdsService(conn)
    await ads.set_owner_fallback(command.args.strip())
    await conn.close()
    await msg.reply("✅ Fallback ad text updated.")

@router.message(Command("listads"))
async def listads(msg: Message, db: DB):
    if not await _ensure_owner(msg):
        return
    conn = await db.connect()
    cur = await conn.execute("SELECT id, text, link, start_ts, end_ts, kind FROM ads ORDER BY id DESC LIMIT 20")
    rows = await cur.fetchall()
    await conn.close()
    if not rows:
        return await msg.reply("No ads found.")
    lines = []
    now = int(time.time())
    for r in rows:
        status = 'active' if r['start_ts'] <= now <= r['end_ts'] else ('upcoming' if now < r['start_ts'] else 'ended')
        lines.append(f"#{r['id']} [{status}] {r['kind']} — {r['text'][:40]}")
    await msg.reply("Ads:\n" + "\n".join(lines))

@router.message(Command("addad"))
async def addad(msg: Message, command: CommandObject, db: DB):
    if not await _ensure_owner(msg):
        return
    if not command.args:
        return await msg.reply("Usage:\n<code>/addad text|https://t.me/link|days</code>", parse_mode="HTML")
    raw = command.args.strip()
    if '|' not in raw:
        return await msg.reply("Usage:\n<code>/addad text|https://t.me/link|days</code>", parse_mode="HTML")
    parts = [x.strip() for x in raw.split('|')]
    if len(parts) < 3:
        return await msg.reply("Usage:\n<code>/addad text|https://t.me/link|days</code>", parse_mode="HTML")
    text_ad, link, days_s = parts[0], _norm_tg(parts[1]), parts[2]
    try:
        days = max(1, int(days_s))
    except Exception:
        return await msg.reply("❌ Days must be a number.")
    start_ts = int(time.time())
    end_ts = start_ts + days * 86400
    conn = await db.connect(); ads = AdsService(conn)
    tx_sig = f"owner_ad_{start_ts}_{msg.from_user.id}"
    await ads.create_ad(int(msg.from_user.id), text_ad, link, start_ts, end_ts, tx_sig, 0.0, kind='ad')
    await conn.close()
    await msg.reply(f"✅ Ad created for {days} day(s).")

@router.message(Command("deletead"))
async def deletead(msg: Message, command: CommandObject, db: DB):
    if not await _ensure_owner(msg):
        return
    if not command.args:
        return await msg.reply("Usage:\n<code>/deletead ID</code>", parse_mode="HTML")
    try:
        ad_id = int(command.args.strip().split()[0])
    except Exception:
        return await msg.reply("❌ Invalid ad ID.")
    conn = await db.connect()
    await conn.execute("DELETE FROM ads WHERE id=?", (ad_id,))
    await conn.commit(); changes = conn.total_changes
    await conn.close()
    if changes:
        await msg.reply("✅ Ad deleted.")
    else:
        await msg.reply("❌ Ad not found.")

@router.message(Command("status"))
async def status(msg: Message, db: DB):
    if not await _ensure_owner(msg):
        return
    conn = await db.connect()
    cur = await conn.execute("SELECT COUNT(*) FROM tracked_tokens")
    tokens = (await cur.fetchone())[0]
    cur = await conn.execute("SELECT COUNT(*) FROM invoices WHERE status='pending'")
    pending = (await cur.fetchone())[0]
    cur = await conn.execute("SELECT COUNT(*) FROM tracked_tokens WHERE post_mode='channel'")
    enabled = (await cur.fetchone())[0]
    cur = await conn.execute("SELECT COUNT(*) FROM tracked_tokens WHERE force_trending=1 OR trend_until_ts>?", (int(time.time()),))
    trending = (await cur.fetchone())[0]
    await conn.close()
    await msg.reply(f"Tracked tokens: {tokens}\nChannel enabled: {enabled}\nTrending forced/live: {trending}\nPending invoices: {pending}")

@router.message()
async def txhash_fallback(msg: Message, state: FSMContext, db: DB, rpc: TonClient):
    text = (msg.text or '').strip()
    if len(text) < 32 or ' ' in text or text.startswith('/'):
        return
    cur_state = await state.get_state()
    if cur_state == InvoiceFlow.txhash.state:
        return
    invoice_id = await _latest_pending_invoice_for_user(db, msg.from_user.id)
    if not invoice_id:
        return
    await state.set_state(InvoiceFlow.txhash)
    await state.update_data(invoice_id=invoice_id)
    await invoice_txhash_submit(msg, state, db, rpc)

