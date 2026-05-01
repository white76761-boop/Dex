from pydantic import BaseModel
import os


def _get(name: str, default: str | None = None) -> str:
    v = os.getenv(name, default)
    if v is None:
        raise RuntimeError(f"Missing env var: {name}")
    return v


class Settings(BaseModel):
    BOT_TOKEN: str = _get("BOT_TOKEN")
    OWNER_ID: int = int(_get("OWNER_ID"))
    BOT_USERNAME: str = _get("BOT_USERNAME", "BazaTonBuyBot")
    POST_CHANNEL: str = _get("POST_CHANNEL", "@BazaTonTrending")
    LISTING_URL: str = _get("LISTING_URL", "https://t.me/BazaTonHub/6")
    TRENDING_URL: str = _get("TRENDING_URL", "https://t.me/BazaTonHub/6")
    LEADERBOARD_MESSAGE_ID: int = int(_get("LEADERBOARD_MESSAGE_ID", "0"))

    DATABASE_URL: str = _get("DATABASE_URL", "sqlite+aiosqlite:///data/bazaton_buybot.db")

    # Optional. TonAPI free tier also works without a key, but a key is recommended.
    TONAPI_KEY: str = os.getenv("TONAPI_KEY", "")
    TON_PRICE_URL: str = _get("TON_PRICE_URL", "https://api.coingecko.com/api/v3/simple/price?ids=the-open-network&vs_currencies=usd")

    PAYMENT_WALLET: str = _get("PAYMENT_WALLET")
    TRENDING_1H_PRICE_TON: float = float(_get("TRENDING_1H_PRICE_TON", "5"))
    TRENDING_3H_PRICE_TON: float = float(_get("TRENDING_3H_PRICE_TON", "15"))
    TRENDING_6H_PRICE_TON: float = float(_get("TRENDING_6H_PRICE_TON", "25"))
    TRENDING_9H_PRICE_TON: float = float(_get("TRENDING_9H_PRICE_TON", "35"))
    TRENDING_12H_PRICE_TON: float = float(_get("TRENDING_12H_PRICE_TON", "45"))
    TRENDING_24H_PRICE_TON: float = float(_get("TRENDING_24H_PRICE_TON", "75"))

    ADS_1D_PRICE_TON: float = float(_get("ADS_1D_PRICE_TON", "20"))
    ADS_3D_PRICE_TON: float = float(_get("ADS_3D_PRICE_TON", "48"))
    ADS_7D_PRICE_TON: float = float(_get("ADS_7D_PRICE_TON", "100"))

    POLL_INTERVAL_SEC: int = int(_get("POLL_INTERVAL_SEC", "3"))
    MIN_BUY_DEFAULT_TON: float = float(_get("MIN_BUY_DEFAULT_TON", "1"))

    @property
    def BOOK_ADS_URL(self) -> str:
        return f"https://t.me/{self.BOT_USERNAME}?start=ads"


settings = Settings()
