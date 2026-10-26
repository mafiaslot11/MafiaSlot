"""Group member management: /ban /mute /kick /unban /unmute

- Group admin (Ban ခွင့်ရှိသူ) နဲ့ bot owner ပဲ သုံးလို့ရတယ်၊ တခြားသူတွေရဲ့ command ကို ဘာမှမပြန်ဘူး
- Admin တွေနဲ့ owner ကို ban / mute / kick မလုပ်နိုင်ဘူး
- Bot ကို Group မှာ Admin လုပ်ပြီး "Ban users" ခွင့်ပေးထားရမယ်
"""
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from telegram import ChatPermissions
from telegram.error import TelegramError
from telegram.ext import CallbackQueryHandler, CommandHandler, filters

import db
from common import OWNER_ID, btn, edit_msg, esc, kb, log, mention

MMT = ZoneInfo("Asia/Yangon")
ANON_ADMIN_ID = 1087968824          # "Remain anonymous" admin ရဲ့ sender id
DEFAULT_MUTE = 3600                 # /mute မှာ အချိန်မပြောရင် ၁ နာရီ
MIN_MUTE, MAX_MUTE = 60, 366 * 86400
UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
DUR_RE = re.compile(r"^(\d+)([smhdw]?)$", re.I)

with db.tx() as _c:
    _c.execute("CREATE TABLE IF NOT EXISTS mod_users(user_id INTEGER PRIMARY KEY, name TEXT, username TEXT)")


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
    for unit, name in ((86400, "ရက်"), (3600, "နာရီ"), (60, "မိနစ်")):
        if secs >= unit and secs % unit == 0:
            return f"{secs // unit} {name}"
    return f"{max(1, secs // 60)} မိနစ်"


def all_perms():
    f = getattr(ChatPermissions, "all_permissions", None)
    if f:
        return f()
    return ChatPermissions(can_send_messages=True, can_send_polls=True, can_send_other_messages=True,
                           can_add_web_page_previews=True, can_invite_users=True)


async def is_mod(bot, chat_id, uid):
    """owner၊ Group creator၊ သို့ 'Ban users' ခွင့်ရှိတဲ့ admin"""
    if uid == OWNER_ID:
        return True
    try:
        mem = await bot.get_chat_member(chat_id, uid)
    except TelegramError:
        return False
    if mem.status == "creator":
        return True
    return mem.status == "administrator" and bool(getattr(mem, "can_restrict_members", False))


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


USAGE = {
    "ban": "🔨 <b>/ban</b> – Group ကနေ အပြီးပိတ်ပင်\nစာကို reply ထောက်ပြီး <code>/ban အကြောင်းရင်း</code>\n(သို့) <code>/ban @username</code> / <code>/ban user_id</code>",
    "mute": "🔇 <b>/mute</b> – စာပို့လို့မရအောင် ယာယီပိတ်\nစာကို reply ထောက်ပြီး <code>/mute 30m</code> (s မရှိ၊ m=မိနစ်၊ h=နာရီ၊ d=ရက်၊ w=ပတ်)\nအချိန်မပြောရင် ၁ နာရီ။ <code>/mute @username 2h အကြောင်းရင်း</code> လည်းရ",
    "kick": "👢 <b>/kick</b> – Group ကနေ ထုတ်၊ ပြန်ဝင်လို့ရ\nစာကို reply ထောက်ပြီး <code>/kick</code> (သို့) <code>/kick @username</code>",
    "unban": "🔓 <b>/unban</b> – Ban ဖြေ\n<code>/unban @username</code> (သို့) <code>/unban user_id</code>",
    "unmute": "🔊 <b>/unmute</b> – Mute ဖြေ\nစာကို reply ထောက်ပြီး <code>/unmute</code> (သို့) <code>/unmute @username</code>",
}


# ------------------------------------------------------------------ commands
async def mod_cmd(update, context, action):
    m, chat, u = update.message, update.effective_chat, update.effective_user
    if chat.type not in ("group", "supergroup"):
        await m.reply_text("⚠️ ဒီ command ကို Group ထဲမှာပဲ သုံးလို့ရပါတယ်")
        return
    anon = u.id == ANON_ADMIN_ID and m.sender_chat is not None and m.sender_chat.id == chat.id
    if not (anon or await is_mod(context.bot, chat.id, u.id)):
        return                                              # admin မဟုတ်ရင် ဘာမှမပြန်ဘူး
    r = await resolve(update, context)
    if not r:
        await m.reply_text(USAGE[action])
        return
    uid, name, username, rest = r
    if not await bot_can_restrict(context.bot, chat.id):
        await m.reply_text("⚠️ Bot ကို Group မှာ <b>Admin</b> လုပ်ပြီး <b>Ban users</b> ခွင့်ပေးထားမှ သုံးလို့ရမယ်")
        return
    if uid == u.id:
        await m.reply_text("⚠️ ကိုယ့်ကိုယ်ကို မလုပ်နိုင်ပါ")
        return
    if action in ("ban", "mute", "kick") and await is_protected(context.bot, chat.id, uid):
        await m.reply_text("⛔ Admin / Owner / Bot ကို အရေးယူလို့မရပါ")
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
        await m.reply_text(f"❌ မအောင်မြင်ပါ: {esc(e.message if hasattr(e, 'message') else e)}")
        return
    remember(uid, name, username)

    who = mention(uid, name)
    admin = "Admin" if anon else mention(u.id, u.full_name)
    tail = f"\n👮 အရေးယူသူ: {admin}" + (f"\n📝 အကြောင်းရင်း: {esc(reason)}" if reason else "")
    markup = None
    if action == "ban":
        text = f"🔨 {who} ကို Group ထဲကနေ <b>အပြီးပိတ်ပင်</b>လိုက်ပါပြီ" + tail
        markup = kb([[btn("🔓 Unban", f"mod:ub:{chat.id}:{uid}", "green", emoji=False)]])
    elif action == "mute":
        end = (datetime.now(MMT) + timedelta(seconds=secs)).strftime("%d/%m %H:%M")
        text = (f"🔇 {who} ကို <b>{dur_text(secs)}</b> စာပို့ခွင့်ပိတ်လိုက်ပါပြီ\n"
                f"⏰ ပြန်ဖွင့်မယ့်အချိန်: {end} (မြန်မာစံတော်ချိန်)" + tail)
        markup = kb([[btn("🔊 Unmute", f"mod:um:{chat.id}:{uid}", "green", emoji=False)]])
    elif action == "kick":
        text = f"👢 {who} ကို Group ထဲကနေ <b>ထုတ်</b>လိုက်ပါပြီ (ပြန်ဝင်လို့ရပါတယ်)" + tail
    elif action == "unban":
        text = f"🔓 {who} ကို <b>Ban ဖြေ</b>လိုက်ပါပြီ၊ ပြန်ဝင်လို့ရပါပြီ" + tail
    else:
        text = f"🔊 {who} ကို စာပို့ခွင့် <b>ပြန်ပေး</b>လိုက်ပါပြီ" + tail
    await m.reply_text(text, reply_markup=markup)


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
        await q.answer("⛔ Admin ပဲ နှိပ်လို့ရပါတယ်", show_alert=True)
        return
    action = "unban" if act == "ub" else "unmute"
    try:
        await perform(context.bot, chat_id, uid, action)
    except TelegramError as e:
        log.warning("moderation button %s failed: %s", action, e)
        await q.answer("❌ မအောင်မြင်ပါ", show_alert=True)
        return
    await q.answer("✅ ပြီးပါပြီ")
    who = mention(uid, known_name(uid))
    admin = mention(q.from_user.id, q.from_user.full_name)
    text = (f"🔓 {who} ကို <b>Ban ဖြေ</b>လိုက်ပါပြီ၊ ပြန်ဝင်လို့ရပါပြီ" if action == "unban"
            else f"🔊 {who} ကို စာပို့ခွင့် <b>ပြန်ပေး</b>လိုက်ပါပြီ") + f"\n👮 အရေးယူသူ: {admin}"
    await edit_msg(context.bot, q.message.chat_id, q.message.message_id, text)


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
