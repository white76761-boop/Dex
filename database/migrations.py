CREATE_TABLES = [
"""
CREATE TABLE IF NOT EXISTS group_settings (
  group_id INTEGER PRIMARY KEY,
  token_mint TEXT NOT NULL,
  min_buy_sol REAL NOT NULL,
  emoji TEXT NOT NULL,
  telegram_link TEXT,
  media_file_id TEXT,
  media_kind TEXT NOT NULL DEFAULT 'photo',
  is_active INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL
);
""",
"""
CREATE TABLE IF NOT EXISTS tracked_tokens (
  mint TEXT PRIMARY KEY,
  post_mode TEXT NOT NULL DEFAULT 'channel',
  telegram_link TEXT,
  symbol TEXT,
  name TEXT,
  force_trending INTEGER NOT NULL DEFAULT 0,
  force_leaderboard INTEGER NOT NULL DEFAULT 0,
  manual_rank INTEGER,
  trend_until_ts INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL
);
""",
"""
CREATE TABLE IF NOT EXISTS token_settings (
  mint TEXT PRIMARY KEY,
  buy_step INTEGER NOT NULL DEFAULT 1,
  min_buy REAL NOT NULL DEFAULT 0,
  custom_link TEXT,
  emoji TEXT NOT NULL DEFAULT '<tg-emoji emoji-id="5352784961814405440">🐸</tg-emoji>',
  media_file_id TEXT,
  media_kind TEXT NOT NULL DEFAULT 'photo',
  show_media INTEGER NOT NULL DEFAULT 1,
  show_mcap INTEGER NOT NULL DEFAULT 1,
  show_price INTEGER NOT NULL DEFAULT 1,
  show_holders INTEGER NOT NULL DEFAULT 1,
  show_dex INTEGER NOT NULL DEFAULT 1,
  chart_source TEXT NOT NULL DEFAULT 'DexS',
  language TEXT NOT NULL DEFAULT 'English',
  created_at INTEGER NOT NULL DEFAULT 0
);
""",
"""
CREATE TABLE IF NOT EXISTS ads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_by INTEGER NOT NULL,
  text TEXT NOT NULL,
  link TEXT,
  start_ts INTEGER NOT NULL,
  end_ts INTEGER NOT NULL,
  tx_sig TEXT NOT NULL UNIQUE,
  amount_sol REAL NOT NULL,
  kind TEXT NOT NULL DEFAULT 'ad'
);
""",
"""
CREATE TABLE IF NOT EXISTS state_kv (
  k TEXT PRIMARY KEY,
  v TEXT NOT NULL
);
""",
"""
CREATE TABLE IF NOT EXISTS buys (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mint TEXT NOT NULL,
  usd REAL NOT NULL,
  ts INTEGER NOT NULL
);
""",
"""
CREATE TABLE IF NOT EXISTS price_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mint TEXT NOT NULL,
  price_usd REAL NOT NULL,
  ts INTEGER NOT NULL
);
""",

"""
CREATE TABLE IF NOT EXISTS mcap_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mint TEXT NOT NULL,
  mcap_usd REAL NOT NULL,
  ts INTEGER NOT NULL
);
""",
"""
CREATE TABLE IF NOT EXISTS invoices (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  username TEXT,
  token_mint TEXT NOT NULL,
  kind TEXT NOT NULL,
  link TEXT,
  content TEXT,
  emoji TEXT,
  amount_sol REAL NOT NULL,
  duration_sec INTEGER NOT NULL,
  wallet TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  tx_sig TEXT,
  verified_at INTEGER,
  created_at INTEGER NOT NULL
);
""",
]
