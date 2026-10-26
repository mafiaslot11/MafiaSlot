"""Group member management: /ban /mute /kick /unban /unmute

- Group admin ("Ban users" ခွင့်ရှိသူ) နဲ့ bot owner ပဲ သုံးလို့ရတယ်၊ တခြားသူကို ဘာကြောင့်မရလဲ ပြန်ပြောတယ်
- Admin တွေနဲ့ owner ကို ban / mute / kick မလုပ်နိုင်ဘူး
- Bot ကို Group မှာ Admin လုပ်ပြီး "Ban users" ခွင့်ပေးထားရမယ်
- စာအားလုံး English + quote style၊ emoji တွေကို owner က /emojis နဲ့ premium ပြောင်းလို့ရတယ်
"""
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from telegram import ChatPermissions
from telegram.error import BadRequest, TelegramError
from telegram.ext import CallbackQueryHandler, CommandHandler, filters

import db
from common import OWNER_ID, btn, esc, kb, log, mention
from emojis import E, plain, register as register_emojis

MMT = ZoneInfo("Asia/Yangon")
ANON_ADMIN_ID = 1087968824          # "Remain anonymous" admin ရဲ့ sender id
DEFAULT_MUTE = 3600                 # /mute မှာ အချိန်မပြောရင် ၁ နာရီ
MIN_MUTE, MAX_MUTE = 60, 366 * 86400
UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
DUR_RE = re.compile(r"^(\d+)([smhdw]?)$", re.I)

register_emojis("👮 Moderation", {
    "mod_ban": "🔨", "mod_mute": "🔇", "mod_kick": "👢", "mod_unban": "🔓", "mod_unmute": "🔊",
    "mod_user": "👤", "mod_by": "👮", "mod_reason": "📝", "mod_time": "⏱", "mod_until": "⏰",
    "mod_perm": "🚫", "mod_back": "↩️", "mod_ok": "✅", "mod_no": "⛔", "mod_warn": "⚠️", "mod_fail": "❌",
})

with db.tx() as _c:
    _c.execute("CREATE TABLE IF NOT EXISTS mod_users(user_id INTEGER PRIMARY KEY, name TEXT, username TEXT)")


# ------------------------------------------------------------------ text helpers
def quote(*lines):
    return "<blockquote>" + "\n".join(lines) + "</blockquote>"


async def say(m, text, markup=None):
    """premium emoji ကို Telegram က မလက်ခံရင် ပုံမှန် emoji နဲ့ ပြန်ပို့"""
    try:
        return await m.reply_text(text, reply_markup=markup)
    except BadRequest:
        return await m.reply_text(plain(text), reply_markup=markup)


async def edit_text(bot, chat_id, mid, text):
    for t in dict.fromkeys((text, plain(text))):
        try:
            await bot.edit_message_text(t, chat_id=chat_id, message_id=mid)
            return
        except BadRequest as e:
            if "not modified" in str(e).lower():
                return
        except Exception:
            log.exception("moderation edit failed")
            return


def why(status):
    if status == "notadmin":
        return quote(f"{E('mod_no')} Only group admins can use this command.")
    if status == "norights":
        return quote(f"{E('mod_no')} You're an admin, but you don't have the \"Ban users\" permission.",
                     "Enable it in the group's admin settings.")
    return quote(f"{E('mod_warn')} Couldn't check your admin status.",
                 "Make sure the bot is an admin in this group.")


def usage(action):
    if action == "ban":
        return quote(f"{E('mod_ban')} <b>/ban</b> – ban a member permanently",
                     "Reply to a message with <code>/ban reason</code>",
                     "or use <code>/ban @username</code> / <code>/ban user_id</code>")
    if action == "mute":
        return quote(f"{E('mod_mute')} <b>/mute</b> – temporarily stop a member from sending messages",
                     "Reply to a message with <code>/mute 30m reason</code>",
                     "Units: m = minutes, h = hours, d = days, w = weeks",
                     "Default is 1 hour. <code>/mute @username 2h</code> works too.")
    if action == "kick":
        return quote(f"{E('mod_kick')} <b>/kick</b> – remove a member (they can rejoin)",
                     "Reply to a message with <code>/kick</code> or use <code>/kick @username</code>")
    if action == "unban":
        return quote(f"{E('mod_unban')} <b>/unban</b> – let a banned member rejoin",
                     "<code>/unban @username</code> or <code>/unban user_id</code>")
    return quote(f"{E('mod_unmute')} <b>/unmute</b> – let a muted member send messages again",
                 "Reply to a message with <code>/unmute</code> or use <code>/unmute @username</code>")


# ------------------------------------------------------------------ helpers
def remember(uid, name, username):
    with db.tx() as c:
        c.execute("INSERT OR REPLACE INTO mod_users(user_id,name,username) VALUES(?,?,?)", (uid, name, username))


def known_name(uid):
    r = db.user_row(uid)
    if r:
        return r["name"]
    with db._lock:
        r = db._c.execute("SELECT name FROM mod_users WHERE user_id=?", (uid,)).fetchone()
    return r["name"] if r else str(uid)


def find_by_username(key):
    with db._lock:
        return db._c.execute("SELECT user_id,name,username FROM mod_users WHERE lower(username)=?",
                             (key.lstrip("@").lower(),)).fetchone()


def parse_duration(tok):
    m = DUR_RE.match(tok or "")
    if not m:
        return None
    secs = int(m.group(1)) * UNITS[(m.group(2) or "m").lower()]      # unit မပါရင် မိနစ်
    return max(MIN_MUTE, min(MAX_MUTE, secs))


def dur_text(secs):
    for unit, name in ((86400, "day"), (3600, "hour"), (60, "minute")):
        if secs >= unit and secs % unit == 0:
            n = secs // unit
            return f"{n} {name}{'s' if n != 1 else ''}"
    n = max(1, secs // 60)
    return f"{n} minute{'s' if n != 1 else ''}"


def all_perms():
    f = getattr(ChatPermissions, "all_permissions", None)
    if f:
        return f()
    return ChatPermissions(can_send_messages=True, can_send_polls=True, can_send_other_messages=True,
                           can_add_web_page_previews=True, can_invite_users=True)


async def mod_status(bot, chat_id, uid):
    """'ok' | 'notadmin' | 'norights' | 'error'"""
    if uid == OWNER_ID:
        return "ok"
    try:
        mem = await bot.get_chat_member(chat_id, uid)
    except TelegramError as e:
        log.warning("moderation: get_chat_member failed: %s", e)
        return "error"
    if mem.status == "creator":
        return "ok"
    if mem.status == "administrator":
        return "ok" if getattr(mem, "can_restrict_members", False) else "norights"
    return "notadmin"


async def is_mod(bot, chat_id, uid):
    return await mod_status(bot, chat_id, uid) == "ok"


async def is_protected(bot, chat_id, uid):
    """Admin / owner / bot ကိုယ်တိုင်ကို အရေးယူလို့မရ"""
    if uid == OWNER_ID or uid == bot.id:
        return True
    try:
        mem = await bot.get_chat_member(chat_id, uid)
    except TelegramError:
        return False                    # group ထဲမရှိတဲ့သူ (ban အတွက်ကတော့ ရတယ်)
    return mem.status in ("administrator", "creator")


async def bot_can_restrict(bot, chat_id):
    try:
        mem = await bot.get_chat_member(chat_id, bot.id)
    except TelegramError:
        return False
    return mem.status == "administrator" and bool(getattr(mem, "can_restrict_members", False))


async def perform(bot, chat_id, uid, action, until=None):
    if action == "ban":
        await bot.ban_chat_member(chat_id, uid)
    elif action == "kick":
        await bot.ban_chat_member(chat_id, uid)
        await bot.unban_chat_member(chat_id, uid, only_if_banned=True)      # ထုတ်ပြီး ပြန်ဝင်ခွင့်ပေး
    elif action == "unban":
        await bot.unban_chat_member(chat_id, uid, only_if_banned=True)
    elif action == "mute":
        await bot.restrict_chat_member(chat_id, uid, ChatPermissions(can_send_messages=False), until_date=until)
    elif action == "unmute":
        chat = await bot.get_chat(chat_id)
        await bot.restrict_chat_member(chat_id, uid, getattr(chat, "permissions", None) or all_perms())


async def resolve(update, context):
    """(uid, name, username, ကျန်တဲ့ args) — reply ထောက်ထားရင် အဲဒီသူ၊ မဟုတ်ရင် args[0] (@username / user id)"""
    m, args = update.message, list(context.args)
    rep = m.reply_to_message
    if rep and rep.from_user and not rep.from_user.is_bot:
        t = rep.from_user
        return t.id, t.full_name, t.username, args
    if args:
        key = args[0]
        if key.lstrip("-").isdigit():
            return int(key), known_name(int(key)), None, args[1:]
        if key.startswith("@"):
            row = db.find_user(key)
            if row:
                return row["id"], row["name"], row["username"], args[1:]
            row = find_by_username(key)
            if row:
                return row["user_id"], row["name"], row["username"], args[1:]
    return None


def result_text(action, who, admin, secs=None, reason=""):
    if action == "ban":
        head, body = f"{E('mod_ban')} <b>BANNED</b>", [f"{E('mod_user')} User: {who}", f"{E('mod_perm')} Permanently banned from this group"]
    elif action == "mute":
        end = (datetime.now(MMT) + timedelta(seconds=secs)).strftime("%d/%m %H:%M")
        head = f"{E('mod_mute')} <b>MUTED</b>"
        body = [f"{E('mod_user')} User: {who}", f"{E('mod_time')} Duration: {dur_text(secs)}",
                f"{E('mod_until')} Until: {end} (Myanmar time)"]
    elif action == "kick":
        head, body = f"{E('mod_kick')} <b>KICKED</b>", [f"{E('mod_user')} User: {who}", f"{E('mod_back')} Removed from the group, can rejoin"]
    elif action == "unban":
        head, body = f"{E('mod_unban')} <b>UNBANNED</b>", [f"{E('mod_user')} User: {who}", f"{E('mod_ok')} Can rejoin the group"]
    else:
        head, body = f"{E('mod_unmute')} <b>UNMUTED</b>", [f"{E('mod_user')} User: {who}", f"{E('mod_ok')} Can send messages again"]
    body.append(f"{E('mod_by')} By: {admin}")
    if reason:
        body.append(f"{E('mod_reason')} Reason: {esc(reason)}")
    return quote(head, *body)


# ------------------------------------------------------------------ commands
async def mod_cmd(update, context, action):
    m, chat, u = update.message, update.effective_chat, update.effective_user
    if chat.type not in ("group", "supergroup"):
        await say(m, quote(f"{E('mod_warn')} This command only works in groups."))
        return
    anon = u.id == ANON_ADMIN_ID and m.sender_chat is not None and m.sender_chat.id == chat.id
    log.info("moderation /%s by %s in chat %s (anon=%s)", action, u.id, chat.id, anon)
    if not anon:
        status = await mod_status(context.bot, chat.id, u.id)
        if status != "ok":
            await say(m, why(status))
            return
    r = await resolve(update, context)
    if not r:
        await say(m, usage(action))
        return
    uid, name, username, rest = r
    if not await bot_can_restrict(context.bot, chat.id):
        await say(m, quote(f"{E('mod_warn')} Please make the bot an <b>Admin</b> with the \"Ban users\" permission first."))
        return
    if uid == u.id:
        await say(m, quote(f"{E('mod_warn')} You can't do this to yourself."))
        return
    if action in ("ban", "mute", "kick") and await is_protected(context.bot, chat.id, uid):
        await say(m, quote(f"{E('mod_no')} Can't take action on admins, the owner, or the bot."))
        return

    secs, until = None, None
    if action == "mute":
        secs = parse_duration(rest[0]) if rest else None
        if secs is not None:
            rest = rest[1:]
        else:
            secs = DEFAULT_MUTE
        until = datetime.now(timezone.utc) + timedelta(seconds=secs)
    reason = " ".join(rest).strip()

    try:
        await perform(context.bot, chat.id, uid, action, until)
    except TelegramError as e:
        log.warning("moderation %s failed: %s", action, e)
        await say(m, quote(f"{E('mod_fail')} Failed: {esc(getattr(e, 'message', e))}"))
        return
    remember(uid, name, username)

    admin = "Admin" if anon else mention(u.id, u.full_name)
    markup = None
    if action == "ban":
        markup = kb([[btn("🔓 Unban", f"mod:ub:{chat.id}:{uid}", "green", emoji=False)]])
    elif action == "mute":
        markup = kb([[btn("🔊 Unmute", f"mod:um:{chat.id}:{uid}", "green", emoji=False)]])
    await say(m, result_text(action, mention(uid, name), admin, secs, reason), markup)


async def ban_cmd(update, context):
    await mod_cmd(update, context, "ban")


async def mute_cmd(update, context):
    await mod_cmd(update, context, "mute")


async def kick_cmd(update, context):
    await mod_cmd(update, context, "kick")


async def unban_cmd(update, context):
    await mod_cmd(update, context, "unban")


async def unmute_cmd(update, context):
    await mod_cmd(update, context, "unmute")


# ------------------------------------------------------------------ undo buttons
async def mod_cb(update, context):
    q = update.callback_query
    try:
        _, act, chat_id, uid = q.data.split(":")
        chat_id, uid = int(chat_id), int(uid)
    except ValueError:
        await q.answer()
        return
    if not await is_mod(context.bot, chat_id, q.from_user.id):
        await q.answer("⛔ Admins only", show_alert=True)
        return
    action = "unban" if act == "ub" else "unmute"
    try:
        await perform(context.bot, chat_id, uid, action)
    except TelegramError as e:
        log.warning("moderation button %s failed: %s", action, e)
        await q.answer("❌ Failed", show_alert=True)
        return
    await q.answer("✅ Done")
    text = result_text(action, mention(uid, known_name(uid)), mention(q.from_user.id, q.from_user.full_name))
    await edit_text(context.bot, q.message.chat_id, q.message.message_id, text)


def handlers():
    g = filters.ChatType.GROUPS
    return [
        CommandHandler("ban", ban_cmd, filters=g),
        CommandHandler("mute", mute_cmd, filters=g),
        CommandHandler("kick", kick_cmd, filters=g),
        CommandHandler("unban", unban_cmd, filters=g),
        CommandHandler("unmute", unmute_cmd, filters=g),
        CallbackQueryHandler(mod_cb, pattern=r"^mod:"),
    ]
