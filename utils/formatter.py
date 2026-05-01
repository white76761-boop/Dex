from __future__ import annotations
from bot.config import settings
from typing import Optional
from html import escape

RANK_EMOJIS = {1: "1️⃣", 2: "2️⃣", 3: "3️⃣", 4: "4️⃣", 5: "5️⃣", 6: "6️⃣", 7: "7️⃣", 8: "8️⃣", 9: "9️⃣", 10: "🔟"}

PREMIUM_EMOJIS = {
    "spent": '<tg-emoji emoji-id="5409048419211682843">💲</tg-emoji>',
    "got": '<tg-emoji emoji-id="5262577510293457429">↔️</tg-emoji>',
    "wallet": '<tg-emoji emoji-id="5260547274957672345">🫂</tg-emoji>',
    "price": '<tg-emoji emoji-id="5224257782013769471">🗝️</tg-emoji>',
    "mcap": '<tg-emoji emoji-id="5451882707875276247">📈</tg-emoji>',
    "chart": '<tg-emoji emoji-id="5417971815064561628">🛡️</tg-emoji>',
    "listing": '<tg-emoji emoji-id="5427168083074628963">🔥</tg-emoji>',
    "buy": '<tg-emoji emoji-id="5352784961814405440">🐸</tg-emoji>',
    "moon": '<tg-emoji emoji-id="5258332798409783582">🌙</tg-emoji>',
}


def short_addr(a: str, left: int = 4, right: int = 4) -> str:
    if not a:
        return "Unknown"
    if len(a) <= left + right + 3:
        return a
    return f"{a[:left]}...{a[-right:]}"


def emoji_bar(emoji: str, count: int = 3) -> str:
    e = (emoji or "").strip() or PREMIUM_EMOJIS["buy"]
    return " ".join([e] * max(1, count))


def fmt_num(x: float, decimals: int = 2) -> str:
    try:
        return f"{x:,.{decimals}f}"
    except Exception:
        return str(x)


def premium_text_or_plain(key: str, fallback: str) -> str:
    return PREMIUM_EMOJIS.get(key, fallback)


def fmt_spent_amount(value: float, symbol: str) -> str:
    try:
        v = float(value or 0)
    except Exception:
        v = 0.0
    # user requested maximum 2 decimal places on the spent/buy amount line
    return fmt_num(v, 2)


def _norm_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    u = url.strip()
    if not u:
        return None
    if u.startswith("@"):
        return f"https://t.me/{u[1:]}"
    if u.startswith("t.me/"):
        return "https://" + u
    if u.startswith("http://"):
        return "https://" + u[len("http://"):]
    return u


def _a(label: str, url: Optional[str]) -> str:
    safe_label = escape(str(label or ""))
    u = _norm_url(url)
    if not u:
        return safe_label
    safe_url = escape(u, quote=True)
    return f'<a href="{safe_url}">{safe_label}</a>'


def _default_ad_line() -> str:
    return f'ad: <a href="{settings.BOOK_ADS_URL}">Promote here with BazaTon Ads</a>'


def _ad_line(ad_text: str | None, ad_link: str | None = None) -> str:
    if ad_text and ad_text.strip():
        if ad_link:
            return f'ad: <a href="{_norm_url(ad_link)}">{ad_text}</a>'
        return f"ad: {ad_text}"
    return _default_ad_line()


def _build(token_symbol, emoji, spent_sol, spent_usd, got_tokens, buyer, tx_url, price_usd, mcap_usd, tg_url, ad_text, ad_link, chart_url=None, spent_symbol="TON", spent_value=None, platform="ton", progress_bar=None):
    platform_label = {"stonfi": "STON.fi", "dedust": "DeDust", "groypad": "Groypad", "blum": "Blum", "dex": "TON DEX"}.get((platform or "ton").lower(), "TON")
    title = f'{premium_text_or_plain("moon", "🪐")} {_a(token_symbol, tg_url)} Buy on {platform_label}!'
    count = max(3, min(12, int(spent_sol * 4) or 3))
    display_value = spent_value if spent_value is not None else spent_sol
    usd_part = f" (${fmt_num(spent_usd, 2)})" if spent_usd > 0 else ""
    lines = [title, "", emoji_bar(emoji, count), ""]
    lines.append(f"{premium_text_or_plain('spent', '💵')} {fmt_spent_amount(display_value, spent_symbol)} {spent_symbol}{usd_part}")
    lines.append(f"{premium_text_or_plain('got', '🔁')} {fmt_num(got_tokens, 2)} {_a(token_symbol, tg_url)}")
    if progress_bar:
        lines.append(f"🚀 Bonding: <code>{escape(str(progress_bar))}</code>")
    lines.append(f"{premium_text_or_plain('wallet', '👤')} {_a(short_addr(buyer), tx_url)}: New! | {_a('Txn', tx_url)}")
    if price_usd is not None:
        lines.append(f"{premium_text_or_plain('price', '🏷')} Price: ${fmt_num(price_usd, 6)}")
    if mcap_usd is not None:
        lines.append(f"{premium_text_or_plain('mcap', '📊')} MarketCap: ${fmt_num(mcap_usd, 0)}")
    lines.append("")
    lines.append(f'{premium_text_or_plain("listing", "🤍")} {_a("Listing", settings.LISTING_URL)} | {premium_text_or_plain("chart", "📈")} {_a("Chart", chart_url or tx_url)}')
    lines.append("")
    lines.append(_ad_line(ad_text, ad_link))
    return "\n".join(lines)


def build_buy_message_group(**kwargs) -> str:
    return _build(**kwargs)


def build_buy_message_channel(**kwargs) -> str:
    return _build(**kwargs)


def build_leaderboard_message(rows: list[tuple[int, str, str, float, str | None]], footer_handle: str) -> str:
    lines = ["🟢 BAZATON TRENDING", ""]

    visible_rows = []
    for row in rows[:10]:
        rank, label, metric, pct, chart_url = row
        clean_label = (label or "").strip()
        clean_metric = (metric or "").strip()

        is_empty_label = clean_label.upper() in {"", "TOKEN"}
        is_empty_metric = clean_metric in {"", "0", "0.0", "0%"}
        is_empty_pct = abs(float(pct or 0.0)) < 0.000001

        if is_empty_label and is_empty_metric and is_empty_pct:
            continue
        visible_rows.append(row)

    for idx, row in enumerate(visible_rows, start=1):
        rank, label, metric, pct, chart_url = row
        sign = "+" if pct > 0 else ""
        clean_label = (label or "TOKEN").strip()[:32]
        token_part = _a(clean_label, chart_url or settings.LISTING_URL)
        metric_part = _a(metric, chart_url or settings.LISTING_URL)
        lines.append(f'{RANK_EMOJIS.get(rank, str(rank))} {token_part} | {metric_part} | {sign}{pct:.0f}%')
        if idx == 3 and len(visible_rows) > 3:
            lines.append("────────────────────")

    if len(visible_rows) == 0:
        lines.append("No trending tokens yet")

    return "\n".join(lines)
