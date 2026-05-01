from dataclasses import dataclass
from typing import Optional

@dataclass
class GroupSettings:
    group_id: int
    token_mint: str
    min_buy_sol: float
    emoji: str
    telegram_link: Optional[str]
    media_file_id: Optional[str]
    is_active: bool
