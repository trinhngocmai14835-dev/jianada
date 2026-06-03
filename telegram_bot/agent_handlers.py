"""代理发卡 + 管理员代理管理"""
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, filters, ContextTypes,
)
from config import ADMIN_ID, PLANS
from database import Database
from license_gen import generate_license_for_machine

db = Database()

# 避免与 bot.py 中 0-2 的 state 冲突
AGENT_WAIT_MID, AGENT_WAIT_PLAN = 10, 11


# ── 代理命令 ──────────────────────────────────────────────────────────────────

async def cmd_mybalance(update: Update, _ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not db.is_agent(uid):
        await update.message.reply_text("❌ 您不是代理，无法使用此命令。")
        return

    balances = db.get_agent_balances(uid)
    if not balances:
        await update.message.reply_text("📭 您的卡库存为空，请联系管理员充值。")
        return

    lines = ["💼 *您的卡库存：*\n"]
    for pid, bal in balances.items():
        name = PLANS.get(pid, {}).get("name", pid)
        lines.append(f"• {name}：{bal} 张")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def agent_genkey_start(update: Update, _ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not db.is_agent(uid):
        return ConversationHandler.END

    balances = db.get_agent_balances(uid)
    if not any(v > 0 for v in balances.values()):
        await update.message.reply_text("❌ 您的卡库存为空，请联系管理员充值。")
        return ConversationHandler.END

    await update.message.reply_text(
        "🔑 *代理发卡*\n\n请输入客户的机器码（16位字母数字）：\n\n/cancel 取消",
        parse_mode="Markdown",
    )
    return AGENT_WAIT_MID


async def agent_recv_mid(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    mid = update.message.text.strip().upper()
    if len(mid) != 16 or not mid.isalnum():
        await update.message.reply_text("❌ 机器码格式不对，请重新输入 16 位字母数字。")
        return AGENT_WAIT_MID

    ctx.user_data["agent_mid"] = mid
    uid = update.effective_user.id
    balances = db.get_agent_balances(uid)

    kbd = [
        [InlineKeyboardButton(
            f"{PLANS.get(pid, {}).get('name', pid)}（剩余 {bal} 张）",
            callback_data=f"agen|{pid}",
        )]
        for pid, bal in balances.items() if bal > 0
    ]

    await update.message.reply_text(
        f"机器码：`{mid}`\n\n请选择卡类型：",
        reply_markup=InlineKeyboardMarkup(kbd),
        parse_mode="Markdown",
    )
    return AGENT_WAIT_PLAN


async def agent_recv_plan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    plan_id = query.data.split("|")[1]
    plan = PLANS.get(plan_id)
    uid = query.from_user.id
    mid = ctx.user_data.get("agent_mid", "")

    if not plan or not mid:
        await query.edit_message_text("❌ 出错了，请重新 /genkey")
        return ConversationHandler.END

    balance = db.get_agent_plan_balance(uid, plan_id)
    if balance <= 0:
        await query.edit_message_text("❌ 该卡库存不足，请联系管理员充值。")
        return ConversationHandler.END

    # 试用卡：同一机器全局只能用一次
    if plan_id == "trial7" and db.has_used_trial(mid):
        await query.edit_message_text(
            f"⚠️ 机器码 `{mid}` 已领取过试用，不可重复使用。\n\n"
            f"如需继续使用，请引导客户购买正式套餐。",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    license_key = generate_license_for_machine(mid, plan["days"])
    ok = db.deduct_agent_card(uid, plan_id, license_key, mid)
    if not ok:
        await query.edit_message_text("❌ 库存已被并发消耗，请重新 /genkey")
        return ConversationHandler.END

    await query.edit_message_text(
        f"✅ *授权码已生成*\n\n"
        f"`{license_key}`\n\n"
        f"套餐：{plan['name']}  机器码：`{mid}`\n"
        f"剩余库存：{balance - 1} 张",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


async def agent_cancel(update: Update, _ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❎ 已取消。")
    return ConversationHandler.END


# ── 管理员 - 代理管理命令 ──────────────────────────────────────────────────────

async def cmd_addagent(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    args = ctx.args or []
    if not args:
        await update.message.reply_text("用法：`/addagent <user_id> [备注]`", parse_mode="Markdown")
        return
    try:
        agent_uid = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ user_id 必须是数字")
        return

    note = " ".join(args[1:])
    db.add_agent(agent_uid, note=note)
    suffix = f"（{note}）" if note else ""
    await update.message.reply_text(
        f"✅ 已添加代理 `{agent_uid}`{suffix}\n"
        f"用 /topup 给他充卡。",
        parse_mode="Markdown",
    )


async def cmd_topup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    args = ctx.args or []
    plan_list = "、".join(PLANS.keys())

    if len(args) < 3:
        await update.message.reply_text(
            f"用法：`/topup <user_id> <套餐> <数量>`\n"
            f"套餐可选：`{plan_list}`\n"
            f"示例：`/topup 123456789 7 20`",
            parse_mode="Markdown",
        )
        return

    try:
        agent_uid = int(args[0])
        plan_id = args[1]
        qty = int(args[2])
    except ValueError:
        await update.message.reply_text("❌ 参数格式错误，user_id 和数量必须是数字")
        return

    if plan_id not in PLANS:
        await update.message.reply_text(f"❌ 套餐 `{plan_id}` 不存在，可选：`{plan_list}`", parse_mode="Markdown")
        return
    if qty <= 0:
        await update.message.reply_text("❌ 数量必须大于 0")
        return
    if not db.is_agent(agent_uid):
        await update.message.reply_text(
            f"❌ 用户 `{agent_uid}` 不是代理\n请先 `/addagent {agent_uid}`",
            parse_mode="Markdown",
        )
        return

    new_bal = db.topup_agent(agent_uid, plan_id, qty)
    plan_name = PLANS[plan_id]["name"]
    await update.message.reply_text(
        f"✅ 已给代理 `{agent_uid}` 充入 {qty} 张 *{plan_name}*\n"
        f"该套餐当前余量：{new_bal} 张",
        parse_mode="Markdown",
    )
    try:
        await ctx.bot.send_message(
            chat_id=agent_uid,
            text=f"🎉 管理员已为您充入 {qty} 张 *{plan_name}*，发送 /mybalance 查看库存。",
            parse_mode="Markdown",
        )
    except Exception:
        pass


async def cmd_agents(update: Update, _ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    agents = db.get_all_agents()
    if not agents:
        await update.message.reply_text("暂无代理，用 /addagent 添加。")
        return

    lines = ["👥 *代理列表：*\n"]
    for a in agents:
        balances = db.get_agent_balances(a["user_id"])
        if balances:
            bal_str = "  ".join(
                f"{PLANS.get(pid, {}).get('name', pid)}: {b}" for pid, b in balances.items()
            )
        else:
            bal_str = "无库存"
        note = f"（{a['note']}）" if a.get("note") else ""
        lines.append(f"• `{a['user_id']}`{note}\n  {bal_str}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_agentlog(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    args = ctx.args or []
    if not args:
        await update.message.reply_text("用法：`/agentlog <user_id>`", parse_mode="Markdown")
        return
    try:
        agent_uid = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ user_id 必须是数字")
        return

    logs = db.get_agent_logs(agent_uid)
    if not logs:
        await update.message.reply_text(f"代理 `{agent_uid}` 暂无发卡记录", parse_mode="Markdown")
        return

    lines = [f"📋 *代理 {agent_uid} 发卡记录（最近20条）：*\n"]
    for log in logs:
        name = PLANS.get(log["plan_id"], {}).get("name", log["plan_id"])
        lines.append(f"• {log['created_at'][:16]}  {name}  `{log['machine_id']}`")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


def _plan_usage() -> str:
    return "、".join(f"`{k}`={v['name']}" for k, v in PLANS.items())


async def cmd_issue(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """生成授权码给管理员复制（线下客户用）"""
    if update.effective_user.id != ADMIN_ID:
        return
    args = ctx.args or []
    if len(args) < 2:
        await update.message.reply_text(
            f"用法：`/issue <机器码> <套餐>`\n"
            f"套餐：{_plan_usage()}\n"
            f"示例：`/issue ABCD1234EFGH5678 7`",
            parse_mode="Markdown",
        )
        return

    mid = args[0].strip().upper()
    plan_id = args[1].strip()

    if len(mid) != 16 or not mid.isalnum():
        await update.message.reply_text("❌ 机器码格式不对，需16位字母数字")
        return
    if plan_id not in PLANS:
        await update.message.reply_text(f"❌ 套餐不存在，可选：{_plan_usage()}", parse_mode="Markdown")
        return

    plan = PLANS[plan_id]
    license_key = generate_license_for_machine(mid, plan["days"])

    await update.message.reply_text(
        f"✅ *授权码已生成（复制发给客户）*\n\n"
        f"`{license_key}`\n\n"
        f"套餐：{plan['name']}  机器码：`{mid}`",
        parse_mode="Markdown",
    )


async def cmd_send(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """生成授权码并直接发送到指定用户的对话框（线上客户用）"""
    if update.effective_user.id != ADMIN_ID:
        return
    args = ctx.args or []
    if len(args) < 3:
        await update.message.reply_text(
            f"用法：`/send <用户ID> <机器码> <套餐>`\n"
            f"套餐：{_plan_usage()}\n"
            f"示例：`/send 123456789 ABCD1234EFGH5678 7`",
            parse_mode="Markdown",
        )
        return

    try:
        target_uid = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ 用户ID 必须是数字")
        return

    mid = args[1].strip().upper()
    plan_id = args[2].strip()

    if len(mid) != 16 or not mid.isalnum():
        await update.message.reply_text("❌ 机器码格式不对，需16位字母数字")
        return
    if plan_id not in PLANS:
        await update.message.reply_text(f"❌ 套餐不存在，可选：{_plan_usage()}", parse_mode="Markdown")
        return

    plan = PLANS[plan_id]
    license_key = generate_license_for_machine(mid, plan["days"])

    try:
        await ctx.bot.send_message(
            chat_id=target_uid,
            text=(
                f"✅ *您的授权码：*\n\n"
                f"`{license_key}`\n\n"
                f"套餐：{plan['name']}  机器码：`{mid}`\n\n"
                f"在软件登录界面粘贴授权码即可激活，感谢购买！"
            ),
            parse_mode="Markdown",
        )
        await update.message.reply_text(
            f"✅ 已发送给用户 `{target_uid}`\n授权码：`{license_key}`",
            parse_mode="Markdown",
        )
    except Exception as e:
        await update.message.reply_text(
            f"❌ 发送失败：{e}\n\n授权码仍有效，请手动转发：\n`{license_key}`",
            parse_mode="Markdown",
        )


def get_agent_conv_handler():
    return ConversationHandler(
        entry_points=[CommandHandler("genkey", agent_genkey_start)],
        states={
            AGENT_WAIT_MID:  [MessageHandler(filters.TEXT & ~filters.COMMAND, agent_recv_mid)],
            AGENT_WAIT_PLAN: [CallbackQueryHandler(agent_recv_plan, pattern=r"^agen\|")],
        },
        fallbacks=[CommandHandler("cancel", agent_cancel)],
        allow_reentry=True,
    )
