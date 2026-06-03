import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler, filters, ContextTypes,
)
from config import BOT_TOKEN, ADMIN_ID, USDT_WALLET, PLANS, RETAIL_PLANS
from database import Database
from tron_verify import verify_usdt_payment
from license_gen import generate_license_for_machine
from agent_handlers import (
    cmd_mybalance, cmd_addagent, cmd_topup, cmd_agents, cmd_agentlog,
    cmd_issue, cmd_send, get_agent_conv_handler,
)

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

db = Database()

# Conversation states
WAIT_MID, WAIT_PLAN, WAIT_TX = range(3)

STATUS_EMOJI = {
    "pending": "⏳", "reviewing": "🔍", "confirmed": "✅", "rejected": "❌",
}
STATUS_TEXT = {
    "pending": "待验证", "reviewing": "人工审核中", "confirmed": "已激活", "rejected": "已拒绝",
}

# ── 公共命令 ────────────────────────────────────────────────────────────────

def _price_table() -> str:
    lines = []
    for p in RETAIL_PLANS.values():
        lines.append(f"  {p['name']:<6} — {p['price']} USDT")
    return "\n".join(lines)


async def cmd_start(update: Update, _ctx: ContextTypes.DEFAULT_TYPE):
    kbd = InlineKeyboardMarkup([
        [InlineKeyboardButton("🛒 立即购买", callback_data="nav|buy")],
        [
            InlineKeyboardButton("📋 我的订单", callback_data="nav|mystatus"),
            InlineKeyboardButton("❓ 购买说明", callback_data="nav|help"),
        ],
    ])
    await update.message.reply_text(
        "👋 欢迎使用 *自动下单系统Pro*！\n\n"
        "专业自动下注工具，支持：\n"
        "• 三球9粒自动下注\n"
        "• 多账号跟投\n\n"
        "━━━━━━━━ 套餐价格 ━━━━━━━━\n"
        f"```\n{_price_table()}\n```\n"
        "━━━━━━━━ 收款地址 ━━━━━━━━\n"
        f"网络：TRC-20（TRON）\n"
        f"`{USDT_WALLET}`",
        parse_mode="Markdown",
        reply_markup=kbd,
    )


async def cmd_help(update: Update, _ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *购买说明*\n\n"
        "*套餐价格：*\n"
        f"```\n{_price_table()}\n```\n"
        "*收款地址（TRC-20）：*\n"
        f"`{USDT_WALLET}`\n\n"
        "*购买步骤：*\n"
        "1. 发送 /buy\n"
        "2. 输入软件登录界面的 *机器码*\n"
        "3. 选择套餐\n"
        "4. 按金额转账 USDT 到上方地址\n"
        "5. 提交交易哈希（TxHash）\n"
        "6. 系统自动验证并发送授权码\n\n"
        "⚠️ *注意*\n"
        "• 必须使用 *TRC-20* 网络，其他网络无法到账\n"
        "• 转账金额须与套餐价格完全一致\n"
        "• 授权码与机器码绑定，不可转让",
        parse_mode="Markdown",
    )


async def cmd_mystatus(update: Update, _ctx: ContextTypes.DEFAULT_TYPE):
    orders = db.get_user_orders(update.effective_user.id)
    if not orders:
        await update.message.reply_text(
            "📭 暂无订单，发送 /buy 购买授权。"
        )
        return

    lines = ["📋 *您的近期订单：*\n"]
    for o in orders[:5]:
        e = STATUS_EMOJI.get(o["status"], "❓")
        s = STATUS_TEXT.get(o["status"], o["status"])
        plan_name = PLANS.get(o["plan_id"], {}).get("name", o["plan_id"])
        lines.append(f"{e} `#{o['id']}` {o['created_at'][:10]}  {plan_name}  {s}")
        if o["status"] == "confirmed" and o["license_key"]:
            lines.append(f"   授权码：`{o['license_key']}`")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── 购买流程 ────────────────────────────────────────────────────────────────

async def buy_start(update: Update, _ctx: ContextTypes.DEFAULT_TYPE):
    # 兼容指令触发和按钮触发两种入口
    if update.callback_query:
        await update.callback_query.answer()
        reply = update.callback_query.message.reply_text
    else:
        reply = update.message.reply_text

    await reply(
        "🛒 *开始购买*\n\n"
        "请打开软件，在登录界面找到您的 *机器码*，然后发给我。\n\n"
        "机器码格式：`ABCD1234EFGH5678`（16位字母数字）\n\n"
        "/cancel 取消",
        parse_mode="Markdown",
    )
    return WAIT_MID


async def recv_machine_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    mid = update.message.text.strip().upper()
    if len(mid) != 16 or not mid.isalnum():
        await update.message.reply_text(
            "❌ 机器码格式不对，应为 16 位字母数字。\n请重新输入，或 /cancel 取消。"
        )
        return WAIT_MID

    ctx.user_data["machine_id"] = mid

    kbd = [[
        InlineKeyboardButton(
            f"{p['name']} — {p['price']} USDT",
            callback_data=f"plan|{pid}",
        )
    ] for pid, p in RETAIL_PLANS.items()]

    await update.message.reply_text(
        f"✅ 机器码：`{mid}`\n\n请选择套餐：",
        reply_markup=InlineKeyboardMarkup(kbd),
        parse_mode="Markdown",
    )
    return WAIT_PLAN


async def recv_plan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    pid = query.data.split("|")[1]
    plan = RETAIL_PLANS.get(pid)   # 只认零售套餐，防伪造 trial7 回调
    if not plan:
        await query.edit_message_text("❌ 无效选择，请重新发送 /buy")
        return ConversationHandler.END

    ctx.user_data["plan_id"] = pid
    ctx.user_data["plan"] = plan

    await query.edit_message_text(
        f"✅ 套餐：*{plan['name']}* — {plan['price']} USDT\n\n"
        f"💳 *付款信息*\n"
        f"网络：`TRC-20 (TRON)`\n"
        f"地址：`{USDT_WALLET}`\n"
        f"金额：`{plan['price']}` USDT\n\n"
        f"⚠️ 务必通过 *TRC-20* 网络转账，金额须精确。\n\n"
        f"转账后将 *交易哈希（TxHash）* 发给我（在钱包交易记录里可找到）。\n\n"
        f"/cancel 取消",
        parse_mode="Markdown",
    )
    return WAIT_TX


async def recv_txhash(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    txhash = update.message.text.strip()
    if len(txhash) < 60:
        await update.message.reply_text(
            "❌ 交易哈希太短，请粘贴完整的 TxHash。\n或 /cancel 取消。"
        )
        return WAIT_TX

    if db.txhash_exists(txhash):
        await update.message.reply_text(
            "❌ 该交易哈希已被提交过，请勿重复使用。\n"
            "如有疑问请联系管理员。"
        )
        return WAIT_TX

    mid = ctx.user_data.get("machine_id")
    pid = ctx.user_data.get("plan_id")
    plan = ctx.user_data.get("plan")
    if not mid or not pid or not plan:
        await update.message.reply_text("❌ 会话已过期，请重新 /buy")
        return ConversationHandler.END
    user = update.effective_user

    order_id = db.create_order(
        user_id=user.id,
        username=user.username or user.first_name or str(user.id),
        machine_id=mid,
        plan_id=pid,
        days=plan["days"],
        price=plan["price"],
        txhash=txhash,
    )

    msg = await update.message.reply_text(
        f"⏳ 正在验证链上交易…\n订单号：`#{order_id}`",
        parse_mode="Markdown",
    )

    verified, verify_msg = await verify_usdt_payment(txhash, USDT_WALLET, plan["price"])

    if verified:
        license_key = generate_license_for_machine(mid, plan["days"])
        db.confirm_order(order_id, license_key)
        await msg.edit_text(
            f"✅ *付款验证成功！*\n\n"
            f"您的授权码：\n`{license_key}`\n\n"
            f"套餐：{plan['name']}  机器码：`{mid}`\n\n"
            f"在软件登录界面粘贴授权码即可激活，感谢购买！",
            parse_mode="Markdown",
        )
    else:
        db.set_reviewing(order_id)

        kbd = [[
            InlineKeyboardButton("✅ 确认发放", callback_data=f"adm|ok|{order_id}"),
            InlineKeyboardButton("❌ 拒绝",   callback_data=f"adm|no|{order_id}"),
        ]]
        await ctx.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"🔔 *订单 #{order_id} 待人工审核*\n\n"
                f"用户：@{user.username or user.first_name} (`{user.id}`)\n"
                f"机器码：`{mid}`\n"
                f"套餐：{plan['name']}（{plan['days']}天）\n"
                f"金额：{plan['price']} USDT\n"
                f"TxHash：`{txhash}`\n\n"
                f"链上验证：❌ {verify_msg}"
            ),
            reply_markup=InlineKeyboardMarkup(kbd),
            parse_mode="Markdown",
        )
        await msg.edit_text(
            f"📋 订单 `#{order_id}` 已提交，正在人工审核。\n"
            f"通常 1–24 小时内处理，完成后自动发送授权码。",
            parse_mode="Markdown",
        )

    return ConversationHandler.END


async def cmd_cancel(update: Update, _ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❎ 已取消。/buy 重新开始。")
    return ConversationHandler.END


async def cmd_myid(update: Update, _ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"🪪 您的 Telegram ID：\n`{user.id}`\n\n将此 ID 发给管理员即可。",
        parse_mode="Markdown",
    )


# ── 导航按钮回调 ─────────────────────────────────────────────────────────────

async def nav_callback(update: Update, _ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = query.data.split("|")[1]

    if action == "mystatus":
        orders = db.get_user_orders(query.from_user.id)
        if not orders:
            await query.message.reply_text("📭 暂无订单，点「立即购买」购买授权。")
            return
        lines = ["📋 *您的近期订单：*\n"]
        for o in orders[:5]:
            e = STATUS_EMOJI.get(o["status"], "❓")
            s = STATUS_TEXT.get(o["status"], o["status"])
            plan_name = PLANS.get(o["plan_id"], {}).get("name", o["plan_id"])
            lines.append(f"{e} `#{o['id']}` {o['created_at'][:10]}  {plan_name}  {s}")
            if o["status"] == "confirmed" and o["license_key"]:
                lines.append(f"   授权码：`{o['license_key']}`")
        await query.message.reply_text("\n".join(lines), parse_mode="Markdown")

    if action == "help":
        await query.message.reply_text(
            "📖 *购买说明*\n\n"
            "*套餐价格：*\n"
            f"```\n{_price_table()}\n```\n"
            "*收款地址（TRC-20）：*\n"
            f"`{USDT_WALLET}`\n\n"
            "*购买步骤：*\n"
            "1. 点「立即购买」\n"
            "2. 输入软件登录界面的 *机器码*\n"
            "3. 选择套餐\n"
            "4. 按金额转账 USDT 到上方地址\n"
            "5. 提交交易哈希（TxHash）\n"
            "6. 系统自动验证并发送授权码\n\n"
            "⚠️ *注意*\n"
            "• 必须使用 *TRC-20* 网络，其他网络无法到账\n"
            "• 转账金额须与套餐价格完全一致\n"
            "• 授权码与机器码绑定，不可转让",
            parse_mode="Markdown",
        )


# ── 管理员回调 ───────────────────────────────────────────────────────────────

async def admin_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("无权操作", show_alert=True)
        return
    await query.answer()

    _, action, oid_str = query.data.split("|")
    order_id = int(oid_str)
    order = db.get_order(order_id)
    if not order:
        await query.edit_message_text("❌ 订单不存在")
        return

    if action == "ok":
        license_key = generate_license_for_machine(order["machine_id"], order["days"])
        db.confirm_order(order_id, license_key)
        plan_name = PLANS.get(order["plan_id"], {}).get("name", order["plan_id"])

        await ctx.bot.send_message(
            chat_id=order["user_id"],
            text=(
                f"✅ *付款已确认，授权码如下：*\n\n"
                f"`{license_key}`\n\n"
                f"套餐：{plan_name}  机器码：`{order['machine_id']}`\n\n"
                f"在软件登录界面粘贴授权码即可激活，感谢购买！"
            ),
            parse_mode="Markdown",
        )
        await query.edit_message_text(
            f"✅ 订单 #{order_id} 已发放\n授权码：`{license_key}`",
            parse_mode="Markdown",
        )

    elif action == "no":
        db.reject_order(order_id)
        await ctx.bot.send_message(
            chat_id=order["user_id"],
            text=(
                f"❌ 订单 #{order_id} 验证未通过。\n\n"
                f"可能原因：金额不符、TxHash 有误、或未用 TRC-20 网络。\n"
                f"如有疑问请联系管理员，或重新 /buy 下单。"
            ),
        )
        await query.edit_message_text(f"❌ 订单 #{order_id} 已拒绝")


async def cmd_pending(update: Update, _ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    orders = db.get_pending_orders()
    if not orders:
        await update.message.reply_text("✅ 无待处理订单")
        return
    for o in orders:
        kbd = [[
            InlineKeyboardButton("✅ 确认发放", callback_data=f"adm|ok|{o['id']}"),
            InlineKeyboardButton("❌ 拒绝",   callback_data=f"adm|no|{o['id']}"),
        ]]
        plan_name = PLANS.get(o["plan_id"], {}).get("name", o["plan_id"])
        await update.message.reply_text(
            f"📋 *订单 #{o['id']}*\n"
            f"用户：`{o['user_id']}` (@{o['username']})\n"
            f"机器码：`{o['machine_id']}`\n"
            f"套餐：{plan_name}（{o['days']}天）\n"
            f"金额：{o['price']} USDT\n"
            f"TxHash：`{o['txhash']}`\n"
            f"状态：{STATUS_TEXT.get(o['status'], o['status'])}\n"
            f"时间：{o['created_at'][:16]}",
            reply_markup=InlineKeyboardMarkup(kbd),
            parse_mode="Markdown",
        )


# ── 启动 ────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("buy", buy_start),
            CallbackQueryHandler(buy_start, pattern=r"^nav\|buy$"),
        ],
        states={
            WAIT_MID:  [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_machine_id)],
            WAIT_PLAN: [CallbackQueryHandler(recv_plan, pattern=r"^plan\|")],
            WAIT_TX:   [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_txhash)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start",      cmd_start))
    app.add_handler(CommandHandler("help",       cmd_help))
    app.add_handler(CommandHandler("mystatus",   cmd_mystatus))
    app.add_handler(conv)
    # 导航按钮（help / mystatus，buy 已在 conv 里处理）
    app.add_handler(CallbackQueryHandler(nav_callback, pattern=r"^nav\|(?!buy)"))
    # 代理命令
    app.add_handler(get_agent_conv_handler())
    app.add_handler(CommandHandler("mybalance",  cmd_mybalance))
    app.add_handler(CommandHandler("myid",       cmd_myid))
    # 管理员命令
    app.add_handler(CommandHandler("pending",    cmd_pending))
    app.add_handler(CommandHandler("issue",      cmd_issue))
    app.add_handler(CommandHandler("send",       cmd_send))
    app.add_handler(CommandHandler("addagent",   cmd_addagent))
    app.add_handler(CommandHandler("topup",      cmd_topup))
    app.add_handler(CommandHandler("agents",     cmd_agents))
    app.add_handler(CommandHandler("agentlog",   cmd_agentlog))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern=r"^adm\|"))

    logger.info("Bot 启动中...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
