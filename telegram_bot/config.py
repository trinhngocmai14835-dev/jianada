import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))


def _bool_env(name, default=False):
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "y"}


def _int_env(name, default, minimum=None, maximum=None):
    try:
        value = int(os.getenv(name) or default)
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


# 当前 TRC20-USDT 收款机器人使用这三项。
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID") or "0")
USDT_WALLET = os.getenv("USDT_WALLET", "")

# TronScan API Key 可选；高频监听建议配置，避免被限流。
TRONSCAN_API_KEY = os.getenv("TRONSCAN_API_KEY", "")

# USDT 到账后台监听配置。
PAYMENT_WATCH_ENABLED = _bool_env("PAYMENT_WATCH_ENABLED", True)
PAYMENT_WATCH_INTERVAL_SECONDS = _int_env("PAYMENT_WATCH_INTERVAL_SECONDS", 60, 30, 3600)
PAYMENT_WATCH_LOOKBACK_MINUTES = _int_env("PAYMENT_WATCH_LOOKBACK_MINUTES", 180, 1, 10080)
PAYMENT_WATCH_LIMIT = _int_env("PAYMENT_WATCH_LIMIT", 20, 1, 50)
PAYMENT_WATCH_SKIP_OLD_DEPOSITS = _bool_env("PAYMENT_WATCH_SKIP_OLD_DEPOSITS", True)

# 下面是旧授权码/代理脚本的兼容配置；当前 bot.py 不再使用套餐价格。
PLANS = {
    "trial7": {"name": "试用周卡", "days": 7,   "price": 0},
    "7":      {"name": "周卡",    "days": 7,   "price": 60},
    "30":     {"name": "1个月",   "days": 30,  "price": 240},
    "90":     {"name": "3个月",   "days": 90,  "price": 680},
    "365":    {"name": "12个月",  "days": 365, "price": 2000},
}

RETAIL_PLANS = {k: v for k, v in PLANS.items() if v["price"] > 0}
PRICE_TOLERANCE = 0.5