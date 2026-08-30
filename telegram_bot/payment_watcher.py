"""后台监听 TRC-20 USDT 到账并通知管理员。"""
import asyncio
import logging
from datetime import datetime
from html import escape

from config import (
    ADMIN_ID,
    PAYMENT_WATCH_ENABLED,
    PAYMENT_WATCH_INTERVAL_SECONDS,
    PAYMENT_WATCH_LIMIT,
    PAYMENT_WATCH_LOOKBACK_MINUTES,
    PAYMENT_WATCH_SKIP_OLD_DEPOSITS,
    USDT_WALLET,
)
from database import Database
from tron_verify import fetch_usdt_incoming_transfers, format_usdt_amount

logger = logging.getLogger(__name__)
db = Database()
BOOTSTRAP_STATE_KEY = "payment_watcher_bootstrapped"


def _code(value) -> str:
    return f"<code>{escape(str(value or ''))}</code>"


def _format_timestamp(block_timestamp) -> str:
    if not block_timestamp:
        return "未知"
    try:
        return datetime.fromtimestamp(int(block_timestamp) / 1000).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return "未知"


def _record_transfer(transfer: dict, notified: bool = True) -> bool:
    return db.record_payment_notification(
        txhash=transfer.get("txhash", ""),
        from_address=transfer.get("from_address", ""),
        to_address=transfer.get("to_address", USDT_WALLET),
        amount=transfer.get("amount", 0),
        block_timestamp=transfer.get("block_timestamp"),
        notified=notified,
    )


def _build_deposit_message(transfer: dict) -> str:
    amount = format_usdt_amount(transfer.get("amount", 0))
    recharge = db.get_recharge_by_txhash(transfer.get("txhash", ""))
    customer_line = "客户：未提交 TxHash"
    if recharge:
        user = recharge.get("username") or recharge.get("user_id")
        customer_line = f"客户：{escape(str(user))} ({_code(recharge.get('user_id'))})"

    return (
        "<b>USDT 到账通知</b>\n\n"
        f"金额：{_code(amount + ' USDT')}\n"
        f"付款地址：{_code(transfer.get('from_address', ''))}\n"
        f"收款地址：{_code(transfer.get('to_address', USDT_WALLET))}\n"
        f"TxHash：{_code(transfer.get('txhash', ''))}\n"
        f"区块时间：{_code(_format_timestamp(transfer.get('block_timestamp')))}\n"
        f"{customer_line}"
    )


async def scan_once(bot) -> int:
    transfers = await fetch_usdt_incoming_transfers(
        USDT_WALLET,
        limit=PAYMENT_WATCH_LIMIT,
        lookback_minutes=PAYMENT_WATCH_LOOKBACK_MINUTES,
    )

    bootstrapped = db.get_app_state(BOOTSTRAP_STATE_KEY)
    if PAYMENT_WATCH_SKIP_OLD_DEPOSITS and not bootstrapped:
        seeded = 0
        for transfer in transfers:
            if _record_transfer(transfer, notified=False):
                seeded += 1
        db.set_app_state(BOOTSTRAP_STATE_KEY, datetime.now().isoformat(timespec="seconds"))
        logger.info("USDT 到账监听首次启动，已标记 %s 笔历史入账为已读", seeded)
        return 0

    sent = 0
    for transfer in transfers:
        txhash = transfer.get("txhash", "")
        if not txhash or db.payment_notification_exists(txhash):
            continue

        await bot.send_message(
            chat_id=ADMIN_ID,
            text=_build_deposit_message(transfer),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        _record_transfer(transfer, notified=True)
        sent += 1

    return sent


async def payment_watcher_loop(bot):
    logger.info(
        "USDT 到账监听已启动，间隔 %s 秒，回看 %s 分钟",
        PAYMENT_WATCH_INTERVAL_SECONDS,
        PAYMENT_WATCH_LOOKBACK_MINUTES,
    )
    while True:
        try:
            sent = await scan_once(bot)
            if sent:
                logger.info("USDT 到账监听已发送 %s 条通知", sent)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("USDT 到账监听失败: %s", exc)
        await asyncio.sleep(PAYMENT_WATCH_INTERVAL_SECONDS)


def start_payment_watcher(application):
    if not PAYMENT_WATCH_ENABLED:
        logger.info("USDT 到账监听已关闭")
        return
    if not ADMIN_ID or not USDT_WALLET:
        logger.warning("USDT 到账监听未启动：ADMIN_ID 或 USDT_WALLET 未配置")
        return
    application.create_task(payment_watcher_loop(application.bot))