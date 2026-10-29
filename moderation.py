"""Group member management

Commands : /ban /mute /kick /unban /unmute   (Group admin + owner ပဲ)
Auto-mod : Link / Forward ပို့ရင် စာကို ချက်ချင်းဖျက် + ၁၀ စက္ကန့် mute + Admin ခလုတ် (Unmute / Add mute time)

- Admin, Owner, Bot, Channel ကလာတဲ့ post တွေကို auto-mod က မထိဘူး
- Bot ကို Group မှာ Admin လုပ်ပြီး "Delete messages" နဲ့ "Ban users" ခွင့်ပေးထားရမယ်
- စာအားလုံး English + quote style (NOTICE_TEXT ကလွဲပြီး)၊ emoji တွေကို owner က /emojis နဲ့ premium ပြောင်းလို့ရ
"""
import re
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from telegram import ChatPermissions, ForceReply
from telegram.error import BadRequest, TelegramError
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler, CommandHandler, MessageHandler, filters

import db
from common import OWNER_ID, btn, esc, kb, log, mention
from emojis import E, plain, register as register_emojis

# ------------------------------------------------------------------ ကိုယ်တိုင်ပြင်လို့ရတဲ့ စာနဲ့ setting များ
NOTICE_TEXT = "စာပို့သမားကြီး ခဏနားဦး၊ ဒီမှာ Delivery လက်မခံဘူး။"   # link/forward ပို့ရင် ပေါ်မယ့်စာ
AUTO_MUTE_SECS = 10                 # link / forward ပို့ရင် mute ချိန်
DEFAULT_MUTE = 3600                 # /mute မှာ အချိန်မပြောရင် ၁ နာရီ
ADD_TIME_CHOICES = [("+10 min", 600), ("+30 min", 1800), ("+1 hour", 3600), ("+1 day", 86400)]
# -------------------------------------------------------------------------------------------

MMT = ZoneInfo("Asia/Yangon")
ANON_ADMIN_ID = 1087968824          # "Remain anonymous" admin ရဲ့ sender id
MIN_MUTE, MAX_MUTE = 60, 366 * 86400
UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
DUR_RE = re.compile(r"^(\d+)([smhdw]?)$", re.I)
LINK_RE = re.compile(r"(?i)(https?://|t\.me/|telegram\.me/|telegram\.dog/|www\.)")

register_emojis("👮 Moderation", {
    "mod_ban": "🔨", "mod_mute": "🔇", "mod_kick": "👢", "mod_unban": "🔓", "mod_unmute": "🔊",
    "mod_user": "👤", "mod_by": "👮", "mod_reason": "📝", "mod_time": "⏱", "mod_until": "⏰",
    "mod_perm": "🚫", "mod_back": "↩️", "mod_ok": "✅", "mod_no": "⛔", "mod_warn": "⚠️", "mod_fail": "❌",
})

with db.tx() as _c:
    _c.execute("CREATE TABLE IF NOT EXISTS mod_users(user_id INTEGER PRIMARY KEY, name TEXT, username TEXT)")
    # soft=1 : ၁၀ စက္ကန့်လို တိုတဲ့ mute (Telegram က ၃၀ စက္ကန့်အောက်ကို "အပြီးပိတ်" လို့ယူလို့ bot က ကိုယ်တိုင် ပြန်ဖြေ)
    _c.execute("CREATE TABLE IF NOT EXISTS mod_mutes(chat_id INTEGER, user_id INTEGER, until_ts REAL, soft INTEGER, "
               "PRIMARY KEY(chat_id,user_id))")

PENDING = {}          # (chat_id, prompt_message_id) -> custom mute time ကို စောင့်နေတဲ့ အချက်အလက်
_admin_cache = {}     # (chat_id, user_id) -> (ts, is_admin)
_hint_ts = {}         # (chat_id, စာ) -> permission hint နောက်ဆုံးပြချိန်
_last_sweep = [0.0]


# ------------------------------------------------------------------ text helpers
def quote(*lines):
    return "<blockquote>" + "\n".join(lines) + "</blockquote>"


async def say(m, text, markup=None):
    """premium emoji ကို Telegram က မလက်ခံရင် ပုံမှန် emoji နဲ့ ပြန်ပို့"""
    try:
        return await m.reply_text(text, reply_markup=markup)
    except BadRequest:
        return await m.reply_text(plain(text), reply_markup=markup)


async def edit_text(bot, chat_id, mid, text, markup=None):
    for t in dict.fromkeys((text, plain(text))):
        try:
            await bot.edit_message_text(t, chat_id=chat_id, message_id=mid, reply_markup=markup)
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


# ------------------------------------------------------------------ db helpers
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


def record_mute(chat_id, uid, until_ts, soft):
    with db.tx() as c:
        c.execute("INSERT OR REPLACE INTO mod_mutes(chat_id,user_id,until_ts,soft) VALUES(?,?,?,?)",
                  (chat_id, uid, until_ts, 1 if soft else 0))


def clear_mute(chat_id, uid):
    with db.tx() as c:
        c.execute("DELETE FROM mod_mutes WHERE chat_id=? AND user_id=?", (chat_id, uid))


def get_mute(chat_id, uid):
    with db._lock:
        return db._c.execute("SELECT until_ts,soft FROM mod_mutes WHERE chat_id=? AND user_id=?",
                             (chat_id, uid)).fetchone()


# ------------------------------------------------------------------ misc helpers
def parse_duration(tok):
    m = DUR_RE.match(tok or "")
    if not m:
        return None
    secs = int(m.group(1)) * UNITS[(m.group(2) or "m").lower()]      # unit မပါရင် မိနစ်
    return max(MIN_MUTE, min(MAX_MUTE, secs))


def dur_text(secs):
    for unit, name in ((86400, "day"), (3600, "hour"), (60, "minute"), (1, "second")):
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
    if action == "mute":
        record_mute(chat.id, uid, time.time() + secs, soft=False)
    elif action in ("unmute", "ban", "unban", "kick"):
        clear_mute(chat.id, uid)

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


# ------------------------------------------------------------------ auto-mod: link / forward
def has_link(message):
    for ents in (message.entities, message.caption_entities):
        for e in ents or ():
            if e.type in ("url", "text_link"):
                return True
    return bool(LINK_RE.search(message.text or message.caption or ""))


def is_forward(message):
    return bool(getattr(message, "forward_origin", None) or getattr(message, "forward_date", None))


class LinkOrForward(filters.MessageFilter):
    """link / forward ပါတဲ့ စာကိုပဲ ဖမ်းတယ်၊ owner / bot / channel post / ခုနကစစ်ပြီးသား admin ကိုတော့ မဖမ်းဘူး"""

    def filter(self, message):
        u = message.from_user
        if not u or u.is_bot or u.id == OWNER_ID or u.id == ANON_ADMIN_ID:
            return False
        if message.sender_chat is not None or getattr(message, "is_automatic_forward", False):
            return False
        hit = _admin_cache.get((message.chat.id, u.id))
        if hit and hit[1] and time.time() - hit[0] < 60:
            return False
        return has_link(message) or is_forward(message)


async def is_admin_any(bot, chat_id, uid):
    """ရိုးရိုး admin (ခွင့်ပေါင်းစုံ) ဟုတ်မဟုတ်၊ စစ်မရရင် admin လို့ယူ (မှားပြီးမဖျက်အောင်)"""
    key = (chat_id, uid)
    hit = _admin_cache.get(key)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    try:
        mem = await bot.get_chat_member(chat_id, uid)
        res = mem.status in ("administrator", "creator")
    except TelegramError:
        res = True
    _admin_cache[key] = (time.time(), res)
    return res


def auto_kb(chat_id, uid):
    return kb([[btn("🔊 Unmute", f"mod:um:{chat_id}:{uid}", "green", emoji=False),
                btn("➕ Add mute time", f"mod:at:{chat_id}:{uid}", "red", emoji=False)]])


def picker_kb(chat_id, uid):
    rows = [[btn(label, f"mod:ad:{chat_id}:{uid}:{secs}", "green", emoji=False) for label, secs in ADD_TIME_CHOICES[:2]],
            [btn(label, f"mod:ad:{chat_id}:{uid}:{secs}", "green", emoji=False) for label, secs in ADD_TIME_CHOICES[2:]],
            [btn("✏️ Custom", f"mod:cu:{chat_id}:{uid}", "green", emoji=False),
             btn("⬅️ Back", f"mod:bk:{chat_id}:{uid}", "green", emoji=False)]]
    return kb(rows)


def auto_text(who, extra=None):
    lines = [f"{E('mod_mute')} <b>MUTED</b>", f"{E('mod_user')} User: {who}", f"{E('mod_perm')} {esc(NOTICE_TEXT)}"]
    if extra:
        lines += extra
    return quote(*lines)


async def permission_hint(context, chat_id, text):
    if time.time() - _hint_ts.get((chat_id, text), 0) < 600:
        return
    _hint_ts[(chat_id, text)] = time.time()
    try:
        await context.bot.send_message(chat_id, quote(f"{E('mod_warn')} {text}"))
    except BadRequest:
        await context.bot.send_message(chat_id, quote(f"⚠️ {text}"))
    except TelegramError:
        pass


async def violation(update, context):
    msg, chat, u = update.effective_message, update.effective_chat, update.effective_user
    if (u is None or u.is_bot or u.id == OWNER_ID or u.id == ANON_ADMIN_ID or msg.sender_chat is not None
            or getattr(msg, "is_automatic_forward", False)):
        return
    if await is_admin_any(context.bot, chat.id, u.id):
        return
    try:
        await msg.delete()
    except TelegramError as e:
        log.warning("auto-mod delete failed: %s", e)
        await permission_hint(context, chat.id, "I need the \"Delete messages\" permission to remove links and forwards.")
        raise ApplicationHandlerStop
    row = get_mute(chat.id, u.id)
    if row and row["until_ts"] > time.time():
        raise ApplicationHandlerStop                    # mute ထဲရှိနေတုန်း၊ စာပဲဖျက် notice ထပ်မပို့
    try:
        await context.bot.restrict_chat_member(chat.id, u.id, ChatPermissions(can_send_messages=False))   # until မပါ = bot ကိုယ်တိုင်ပြန်ဖြေ
    except TelegramError as e:
        log.warning("auto-mod mute failed: %s", e)
        await permission_hint(context, chat.id, "I need the \"Ban users\" permission to mute members.")
        raise ApplicationHandlerStop
    record_mute(chat.id, u.id, time.time() + AUTO_MUTE_SECS, soft=True)
    remember(u.id, u.full_name, u.username)
    for j in context.job_queue.get_jobs_by_name(f"smute{chat.id}:{u.id}"):
        j.schedule_removal()
    context.job_queue.run_once(soft_unmute, AUTO_MUTE_SECS, data=(chat.id, u.id), name=f"smute{chat.id}:{u.id}")
    await send_notice(context, chat.id, u)
    await sweep(context.bot)                            # restart ကြောင့် ကျန်နေတဲ့ တိုတောင်း mute တွေကိုလည်း ဒီမှာ ရှင်း
    raise ApplicationHandlerStop


async def send_notice(context, chat_id, u):
    text, markup = auto_text(mention(u.id, u.full_name)), auto_kb(chat_id, u.id)
    try:
        return await context.bot.send_message(chat_id, text, reply_markup=markup)
    except BadRequest:                                  # premium emoji ကို Telegram က မလက်ခံရင် ပုံမှန် emoji နဲ့
        return await context.bot.send_message(chat_id, plain(text), reply_markup=markup)


async def soft_unmute(context):
    chat_id, uid = context.job.data
    row = get_mute(chat_id, uid)
    if not row or not row["soft"] or row["until_ts"] > time.time() + 1:
        return                                          # admin က ကြားထဲ ပြောင်းသွားပြီ (extend / unmute)
    try:
        await perform(context.bot, chat_id, uid, "unmute")
    except TelegramError as e:
        log.warning("auto-unmute failed: %s", e)
        return
    clear_mute(chat_id, uid)


async def sweep(bot):
    """bot restart ဖြစ်လို့ job ပျောက်သွားတဲ့ တိုတောင်း mute တွေကို ပြန်ဖြေ"""
    with db._lock:
        rows = db._c.execute("SELECT chat_id,user_id FROM mod_mutes WHERE soft=1 AND until_ts<=?",
                             (time.time(),)).fetchall()
    for r in rows:
        try:
            await perform(bot, r["chat_id"], r["user_id"], "unmute")
        except TelegramError as e:
            log.warning("sweep unmute failed: %s", e)
            continue
        clear_mute(r["chat_id"], r["user_id"])


async def sweep_job(context):
    await sweep(context.bot)


def schedule(app):
    """main.py မှာ moderation.schedule(app) ခေါ်ပါ၊ bot restart ဖြစ်လည်း တိုတောင်း mute တွေ အပြီးမပိတ်မိအောင်"""
    app.job_queue.run_repeating(sweep_job, 5, first=3)


# ------------------------------------------------------------------ buttons
def current_until(chat_id, uid):
    row = get_mute(chat_id, uid)
    return row["until_ts"] if row else None


async def apply_extend(context, chat_id, uid, secs, notice_mid, admin):
    """လက်ရှိ mute ကျန်ချိန်ပေါ်မှာ secs ထပ်တိုး"""
    now = time.time()
    cur = current_until(chat_id, uid)
    new_ts = max(now, cur or now) + secs
    until = datetime.fromtimestamp(new_ts, timezone.utc)
    await context.bot.restrict_chat_member(chat_id, uid, ChatPermissions(can_send_messages=False), until_date=until)
    record_mute(chat_id, uid, new_ts, soft=False)
    for j in context.job_queue.get_jobs_by_name(f"smute{chat_id}:{uid}"):
        j.schedule_removal()
    end = datetime.fromtimestamp(new_ts, MMT).strftime("%d/%m %H:%M")
    extra = [f"{E('mod_time')} Added: {dur_text(secs)}", f"{E('mod_until')} Until: {end} (Myanmar time)",
             f"{E('mod_by')} By: {admin}"]
    await edit_text(context.bot, chat_id, notice_mid, auto_text(mention(uid, known_name(uid)), extra), auto_kb(chat_id, uid))


async def _set_markup(q, markup):
    try:
        await q.edit_message_reply_markup(markup)
    except TelegramError:
        pass


async def mod_cb(update, context):
    q = update.callback_query
    parts = (q.data or "").split(":")
    try:
        act, chat_id, uid = parts[1], int(parts[2]), int(parts[3])
    except (IndexError, ValueError):
        await q.answer()
        return
    if not await is_mod(context.bot, chat_id, q.from_user.id):
        await q.answer("⛔ Admins only", show_alert=True)
        return
    admin = mention(q.from_user.id, q.from_user.full_name)
    mid = q.message.message_id

    if act in ("ub", "um"):
        action = "unban" if act == "ub" else "unmute"
        try:
            await perform(context.bot, chat_id, uid, action)
        except TelegramError as e:
            log.warning("moderation button %s failed: %s", action, e)
            await q.answer("❌ Failed", show_alert=True)
            return
        clear_mute(chat_id, uid)
        for j in context.job_queue.get_jobs_by_name(f"smute{chat_id}:{uid}"):
            j.schedule_removal()
        await q.answer("✅ Done")
        await edit_text(context.bot, q.message.chat_id, mid, result_text(action, mention(uid, known_name(uid)), admin))
    elif act == "at":                                   # Add mute time -> အချိန်ရွေးမယ့် ခလုတ်တွေပြ
        await q.answer()
        await _set_markup(q, picker_kb(chat_id, uid))
    elif act == "bk":
        await q.answer()
        await _set_markup(q, auto_kb(chat_id, uid))
    elif act == "ad":
        try:
            secs = int(parts[4])
            await apply_extend(context, chat_id, uid, secs, mid, admin)
        except (IndexError, ValueError):
            await q.answer()
            return
        except TelegramError as e:
            log.warning("extend mute failed: %s", e)
            await q.answer("❌ Failed", show_alert=True)
            return
        await q.answer(f"✅ +{dur_text(secs)}")
    elif act == "cu":                                   # Custom -> admin က အချိန်ကို reply နဲ့ရိုက်
        await q.answer()
        prompt = await q.message.reply_text(
            quote("✏️ Reply to this message with the extra mute time", "Examples: <code>45m</code>, <code>3h</code>, <code>2d</code>"),
            reply_markup=ForceReply(selective=True))
        PENDING[(chat_id, prompt.message_id)] = {"uid": uid, "notice": mid}
    else:
        await q.answer()


class CustomReply(filters.MessageFilter):
    def filter(self, message):
        rep = message.reply_to_message
        return bool(rep and (message.chat.id, rep.message_id) in PENDING)


async def custom_reply(update, context):
    m = update.message
    key = (m.chat.id, m.reply_to_message.message_id)
    info = PENDING.get(key)
    if not info or not await is_mod(context.bot, m.chat.id, m.from_user.id):
        return
    secs = parse_duration((m.text or "").split()[0] if (m.text or "").split() else "")
    if secs is None:
        await say(m, quote(f"{E('mod_warn')} Couldn't read that. Try <code>45m</code>, <code>3h</code> or <code>2d</code>."))
        raise ApplicationHandlerStop
    PENDING.pop(key, None)
    try:
        await apply_extend(context, m.chat.id, info["uid"], secs, info["notice"], mention(m.from_user.id, m.from_user.full_name))
    except TelegramError as e:
        await say(m, quote(f"{E('mod_fail')} Failed: {esc(getattr(e, 'message', e))}"))
        raise ApplicationHandlerStop
    for mid in (m.reply_to_message.message_id, m.message_id):          # prompt နဲ့ admin ရဲ့ အချိန်စာကို ရှင်းထုတ်
        try:
            await context.bot.delete_message(m.chat.id, mid)
        except TelegramError:
            pass
    raise ApplicationHandlerStop


def handlers():
    g = filters.ChatType.GROUPS
    return [
        CommandHandler("ban", ban_cmd, filters=g),
        CommandHandler("mute", mute_cmd, filters=g),
        CommandHandler("kick", kick_cmd, filters=g),
        CommandHandler("unban", unban_cmd, filters=g),
        CommandHandler("unmute", unmute_cmd, filters=g),
        CallbackQueryHandler(mod_cb, pattern=r"^mod:"),
        MessageHandler(g & CustomReply(), custom_reply),
        MessageHandler(g & LinkOrForward(), violation),
    ]
