import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID") or "0")
USDT_WALLET = os.getenv("USDT_WALLET", "")

# 套餐配置（key = plan_id，也用作代理卡类型）
PLANS = {
    "trial7": {"name": "试用周卡", "days": 7,   "price": 0},    # 代理专用，不对外零售
    "7":      {"name": "周卡",    "days": 7,   "price": 60},
    "30":     {"name": "1个月",   "days": 30,  "price": 240},
    "90":     {"name": "3个月",   "days": 90,  "price": 680},
    "365":    {"name": "12个月",  "days": 365, "price": 2000},
}

# price=0 的套餐仅限代理发卡，不出现在零售购买流程
RETAIL_PLANS = {k: v for k, v in PLANS.items() if v["price"] > 0}

# 允许的价格误差（USDT），防止用户多/少转0.01
PRICE_TOLERANCE = 0.5
