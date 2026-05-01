from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardMarkup
from urllib.parse import quote
from bot.config import settings

BUY_TEMPLATE = "https://app.ston.fi/swap?chartVisible=false&ft=TON&tt={mint}"


def _display_emoji_value(value: str | None) -> str:
    raw = (value or '').strip()
    if not raw:
        return '🟢'
    if '<tg-emoji' in raw:
        import re
        m = re.search(r'<tg-emoji[^>]*>(.*?)</tg-emoji>', raw)
        if m and m.group(1).strip():
            return m.group(1).strip()
        return '✨'
    return raw[:8]


def buy_kb(mint: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Buy on STON.fi", url=BUY_TEMPLATE.format(mint=quote(mint, safe='')))
    kb.button(text="Trending", url=settings.TRENDING_URL)
    kb.adjust(2)
    return kb.as_markup()


def leaderboard_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Listing", url=settings.LISTING_URL)
    return kb.as_markup()


def main_menu_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🇺🇸 Language", callback_data="menu:lang")
    kb.button(text="✏️ Edit", callback_data="menu:edit")
    kb.button(text="➕ Add Token", callback_data="menu:add")
    kb.button(text="👀 View Tokens", callback_data="menu:view")
    kb.button(text="⚙️ Group Settings", callback_data="menu:group")
    kb.button(text="📈 Trending", callback_data="menu:trending")
    kb.button(text="💎 Advert", callback_data="menu:advert")
    kb.adjust(2, 2, 2, 1)
    return kb.as_markup()


def lang_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🇺🇸 English ✅", callback_data="lang:set:english")
    kb.button(text="🇨🇳 Chinese", callback_data="lang:set:chinese")
    kb.adjust(2)
    return kb.as_markup()


def token_list_kb(tokens: list[tuple[str, str]], prefix: str, back: str = "menu:home") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for mint, label in tokens:
        kb.button(text=f"✏️ {label}", callback_data=f"{prefix}:{mint}")
    kb.button(text="« Return", callback_data=back)
    kb.adjust(1)
    return kb.as_markup()


def token_edit_page_kb(mint: str, page: int, values: dict | None = None) -> InlineKeyboardMarkup:
    values = values or {}
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Page 1", callback_data=f"editpage:{mint}:1")
    rows = [
        ("ℹ️ Buy Step", "buy_step", f"✏️ ({values.get('buy_step', 1)})"),
        ("ℹ️ Min Buy", "min_buy", f"✏️ ({values.get('min_buy', 0)})"),
        ("ℹ️ Link", "link", "✏️ (set)" if values.get('telegram_link') else "✏️ ()"),
        ("ℹ️ Emoji", "emoji", f"✏️ ({_display_emoji_value(values.get('emoji', '🟢'))})"),
        ("ℹ️ Media", "media", "✏️ (📸)" if values.get('media_file_id') else "✏️ ()"),
    ]
    for left, key, right in rows:
        kb.button(text=left, callback_data=f"editset:{mint}:{key}")
        kb.button(text=right, callback_data=f"editset:{mint}:{key}")
    kb.button(text="« Return", callback_data="menu:home")
    kb.adjust(1, 2, 2, 2, 2, 2, 1)
    return kb.as_markup()


def trending_package_kb(selected: str | None = None) -> InlineKeyboardMarkup:
    plans = [("1h", "1 Hours"), ("3h", "3 Hours"), ("6h", "6 Hours"), ("9h", "9 Hours"), ("12h", "12 Hours"), ("24h", "24 Hours")]
    kb = InlineKeyboardBuilder()
    for key, label in plans:
        kb.button(text=("✅ " if selected == key else "☑️ ") + label, callback_data=f"trendpkg:{key}")
    kb.button(text="Continue →", callback_data="trendpkg:continue")
    kb.button(text="« Return", callback_data="menu:home")
    kb.adjust(2, 2, 2, 1, 1)
    return kb.as_markup()


def advert_duration_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for key, label in [("1d", "1 Day"), ("3d", "3 Days"), ("7d", "7 Days")]:
        kb.button(text=label, callback_data=f"adpkg:{key}")
    kb.button(text="« Return", callback_data="menu:home")
    kb.adjust(3, 1)
    return kb.as_markup()


def invoice_kb(invoice_id: int, amount_sol: float) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="« Return", callback_data="menu:home")
    kb.button(text="↻ Refresh", callback_data=f"invoice:refresh:{invoice_id}")
    kb.button(text="✅ I've Paid", callback_data=f"invoice:paid:{invoice_id}")
    kb.button(text="🧾 Submit Tx Hash", callback_data=f"invoice:txhash:{invoice_id}")
    pay_url = f"ton://transfer/{quote(settings.PAYMENT_WALLET)}?amount={int(float(amount_sol) * 1_000_000_000)}"
    kb.button(text=f"Open wallet to pay {amount_sol:g} TON", url=pay_url)
    kb.adjust(2, 2, 1)
    return kb.as_markup()
