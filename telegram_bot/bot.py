import logging
from datetime import datetime
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from config import ADMIN_ID, BOT_TOKEN, USDT_WALLET
from database import Database
from payment_watcher import start_payment_watcher
from tron_verify import format_usdt_amount, verify_usdt_deposit

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

db = Database()
WAIT_TXHASH = 1


def _code(value) -> str:
    return f"<code>{escape(str(value or ''))}</code>"


def _user_label(user) -> str:
    username = f"@{user.username}" if user.username else (user.first_name or str(user.id))
    return f"{escape(username)} ({_code(user.id)})"


def _format_timestamp(block_timestamp) -> str:
    if not block_timestamp:
        return "未知"
    try:
        return datetime.fromtimestamp(int(block_timestamp) / 1000).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return "未知"


def _copy_address_button() -> InlineKeyboardButton:
    return InlineKeyboardButton(
        "复制地址",
        api_kwargs={"copy_text": {"text": USDT_WALLET}},
    )


def _main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [_copy_address_button()],
        [InlineKeyboardButton("提交 TxHash", callback_data="submit_txhash")],
        [InlineKeyboardButton("我的充值", callback_data="my_recharges")],
    ])


def _recharge_text() -> str:
    return (
        "<b>TRC20-USDT 充值</b>\n\n"
        "网络：<b>TRC-20 / TRON</b>\n"
        f"收款地址：{_code(USDT_WALLET)}\n"
        "金额：<b>1 USDT = 6.8</b>\n\n"
        "转账完成后，请把交易哈希 TxHash 发给我，我会自动查询是否到账。\n\n"
        "注意：只能使用 TRC-20 网络，其他网络无法在这里确认。"
    )


async def cmd_start(update: Update, _ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        _recharge_text(),
        parse_mode="HTML",
        reply_markup=_main_keyboard(),
        disable_web_page_preview=True,
    )


async def cmd_help(update: Update, _ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        _recharge_text(),
        parse_mode="HTML",
        reply_markup=_main_keyboard(),
        disable_web_page_preview=True,
    )


async def recharge_start(update: Update, _ctx: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
        reply = update.callback_query.message.reply_text
    else:
        reply = update.message.reply_text

    await reply(
        _recharge_text() + "\n\n请直接发送 TxHash。\n/cancel 取消",
        parse_mode="HTML",
        reply_markup=_main_keyboard(),
        disable_web_page_preview=True,
    )
    return WAIT_TXHASH


async def recv_txhash(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not USDT_WALLET:
        await update.message.reply_text("收款地址还没有配置，请联系管理员。")
        return ConversationHandler.END

    txhash = (update.message.text or "").strip()
    if len(txhash) != 64 or any(c not in "0123456789abcdefABCDEF" for c in txhash):
        await update.message.reply_text(
            "TxHash 格式不对。请发送 64 位交易哈希，或 /cancel 取消。"
        )
        return WAIT_TXHASH

    if db.recharge_txhash_exists(txhash):
        await update.message.reply_text("这笔 TxHash 已经提交过，请不要重复提交。")
        return ConversationHandler.END

    msg = await update.message.reply_text("正在查询链上交易，请稍等...")
    verified, verify_msg, deposit = await verify_usdt_deposit(txhash, USDT_WALLET)
    if not verified or not deposit:
        await msg.edit_text(
            f"暂未确认到账：{escape(verify_msg)}\n\n"
            "请确认使用的是 TRC-20 网络，并稍后重新提交 TxHash。",
            parse_mode="HTML",
        )
        return ConversationHandler.END

    user = update.effective_user
    amount = deposit.get("amount", 0)
    recharge = db.create_recharge(
        user_id=user.id,
        username=user.username or user.first_name or str(user.id),
        txhash=txhash,
        from_address=deposit.get("from_address", ""),
        to_address=deposit.get("to_address", USDT_WALLET),
        amount=amount,
        block_timestamp=deposit.get("block_timestamp"),
    )
    db.record_payment_notification(
        txhash=txhash,
        from_address=deposit.get("from_address", ""),
        to_address=deposit.get("to_address", USDT_WALLET),
        amount=amount,
        block_timestamp=deposit.get("block_timestamp"),
        notified=True,
    )

    recharge_id = recharge["id"] if recharge else "-"
    amount_text = format_usdt_amount(amount)
    await msg.edit_text(
        "充值已确认。\n\n"
        f"充值编号：{_code(recharge_id)}\n"
        f"到账金额：{_code(amount_text + ' USDT')}\n"
        f"TxHash：{_code(txhash)}",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )

    await ctx.bot.send_message(
        chat_id=ADMIN_ID,
        text=(
            "<b>客户充值到账</b>\n\n"
            f"充值编号：{_code(recharge_id)}\n"
            f"客户：{_user_label(user)}\n"
            f"金额：{_code(amount_text + ' USDT')}\n"
            f"付款地址：{_code(deposit.get('from_address', ''))}\n"
            f"收款地址：{_code(deposit.get('to_address', USDT_WALLET))}\n"
            f"TxHash：{_code(txhash)}\n"
            f"区块时间：{_code(_format_timestamp(deposit.get('block_timestamp')))}"
        ),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    return ConversationHandler.END


async def _send_my_recharges(message, user_id: int):
    rows = db.get_user_recharges(user_id)
    if not rows:
        await message.reply_text("暂无充值记录。", reply_markup=_main_keyboard())
        return

    lines = ["<b>我的充值记录</b>"]
    for row in rows:
        amount = format_usdt_amount(row["amount"])
        lines.append(
            f"#{row['id']}  {escape(row['created_at'][:16])}  "
            f"{_code(amount + ' USDT')}"
        )
    await message.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_my_recharges(update: Update, _ctx: ContextTypes.DEFAULT_TYPE):
    await _send_my_recharges(update.message, update.effective_user.id)


async def my_recharges_callback(update: Update, _ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await _send_my_recharges(query.message, query.from_user.id)


async def cmd_recent(update: Update, _ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    rows = db.get_recent_recharges(20)
    if not rows:
        await update.message.reply_text("暂无充值记录。")
        return

    lines = ["<b>最近充值</b>"]
    for row in rows:
        amount = format_usdt_amount(row["amount"])
        user = row.get("username") or row["user_id"]
        lines.append(
            f"#{row['id']}  {escape(str(user))} ({row['user_id']})  "
            f"{_code(amount + ' USDT')}  {escape(row['created_at'][:16])}"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_myid(update: Update, _ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"您的 Telegram ID：{_code(update.effective_user.id)}",
        parse_mode="HTML",
    )


async def cmd_cancel(update: Update, _ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("已取消。发送 /recharge 可重新提交 TxHash。")
    return ConversationHandler.END


async def post_init(application: Application):
    start_payment_watcher(application)


def main():
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("recharge", recharge_start),
            CallbackQueryHandler(recharge_start, pattern=r"^submit_txhash$"),
        ],
        states={
            WAIT_TXHASH: [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_txhash)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("myid", cmd_myid))
    app.add_handler(CommandHandler("myrecharges", cmd_my_recharges))
    app.add_handler(CommandHandler("recent", cmd_recent))
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(my_recharges_callback, pattern=r"^my_recharges$"))
    app.add_handler(MessageHandler(filters.Regex(r"^[0-9a-fA-F]{64}$"), recv_txhash))

    logger.info("Bot 启动中...")
    app.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()