from pydantic import BaseModel
from dotenv import load_dotenv
load_dotenv()
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
    POST_CHANNEL: str = _get("POST_CHANNEL", "@BazaTonHub")
    LISTING_URL: str = _get("LISTING_URL", "https://t.me/BazaTonHub/8")
    TRENDING_URL: str = _get("TRENDING_URL", "https://t.me/BazaTonHub/6")
    LEADERBOARD_MESSAGE_ID: int = int(_get("LEADERBOARD_MESSAGE_ID", "0"))

    DATABASE_URL: str = _get("DATABASE_URL", "sqlite+aiosqlite:///data/bazaton_buybot.db")

    TONAPI_BASE: str = _get("TONAPI_BASE", "https://tonapi.io/v2")
    TONAPI_KEY: str = os.getenv("TONAPI_KEY", "")
    TONCENTER_BASE: str = _get("TONCENTER_BASE", "https://toncenter.com/api/v3")
    TONCENTER_API_KEY: str = os.getenv("TONCENTER_API_KEY", "")
    TONVIEWER_BASE: str = _get("TONVIEWER_BASE", "https://tonviewer.com")

    PAYMENT_WALLET: str = _get("PAYMENT_WALLET")
    TRENDING_1H_PRICE_TON: float = float(_get("TRENDING_1H_PRICE_TON", _get("TRENDING_1H_PRICE_SOL", "0.5")))
    TRENDING_3H_PRICE_TON: float = float(_get("TRENDING_3H_PRICE_TON", _get("TRENDING_3H_PRICE_SOL", "1.5")))
    TRENDING_6H_PRICE_TON: float = float(_get("TRENDING_6H_PRICE_TON", _get("TRENDING_6H_PRICE_SOL", "2.5")))
    TRENDING_9H_PRICE_TON: float = float(_get("TRENDING_9H_PRICE_TON", _get("TRENDING_9H_PRICE_SOL", "3.5")))
    TRENDING_12H_PRICE_TON: float = float(_get("TRENDING_12H_PRICE_TON", _get("TRENDING_12H_PRICE_SOL", "4.5")))
    TRENDING_24H_PRICE_TON: float = float(_get("TRENDING_24H_PRICE_TON", _get("TRENDING_24H_PRICE_SOL", "7.5")))

    ADS_1D_PRICE_TON: float = float(_get("ADS_1D_PRICE_TON", _get("ADS_1D_PRICE_SOL", "2")))
    ADS_3D_PRICE_TON: float = float(_get("ADS_3D_PRICE_TON", _get("ADS_3D_PRICE_SOL", "4.8")))
    ADS_7D_PRICE_TON: float = float(_get("ADS_7D_PRICE_TON", _get("ADS_7D_PRICE_SOL", "10")))

    POLL_INTERVAL_SEC: int = int(_get("POLL_INTERVAL_SEC", "2"))
    MIN_BUY_DEFAULT_TON: float = float(_get("MIN_BUY_DEFAULT_TON", _get("MIN_BUY_DEFAULT_SOL", "0.25")))

    GROYPAD_FACTORY: str = _get("GROYPAD_FACTORY", "EQAO4cYqithwdltzmrlal1L5JKLK5Xk76feAJq0VoBC6Fy8T")
    GROYPAD_BUY_OPCODE: str = _get("GROYPAD_BUY_OPCODE", "0x742b36d8")
    BLUM_BUY_OPCODE: str = _get("BLUM_BUY_OPCODE", "0xaf750d34")
    GROYPAD_API_URL: str = _get("GROYPAD_API_URL", "https://groypfi.io/api/launchpad-screener")

    @property
    def BOOK_ADS_URL(self) -> str:
        return f"https://t.me/{self.BOT_USERNAME}?start=ads"


settings = Settings()
