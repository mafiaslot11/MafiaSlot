"""Owner log notifications:
  * the bot is added to / removed from a group
  * a user presses /start
Sent to LOG_CHAT_ID (a group/channel id) or, if not set, to the owner's private chat."""
import os
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from telegram.ext import ChatMemberHandler

from common import OWNER_ID, log

LOG_CHAT_ID = os.getenv("LOG_CHAT_ID", "").strip()
START_LOG_ALL = os.getenv("START_LOG_ALL", "").strip() in ("1", "true", "yes")  # default: new users only
TZ = ZoneInfo(os.getenv("BOT_TZ", "Asia/Yangon"))
LINE = "────────────────────────"
GROUPS = ("group", "supergroup")
IN = ("member", "administrator", "creator")
OUT = ("left", "kicked")


def _now():
    return datetime.now(TZ).strftime("%Y-%m-%d %I:%M:%S %p")


async def send_log(context, text):
    """Never raises: a failed log must not break the bot."""
    if LOG_CHAT_ID:  # numeric id (-100...) or a public channel like @mychannel
        target = LOG_CHAT_ID if LOG_CHAT_ID.startswith("@") else int(LOG_CHAT_ID)
    else:
        target = OWNER_ID
    if not target:
        return
    try:
        await context.bot.send_message(target, text)
    except Exception:
        log.warning("could not send log message to %s", target)


def group_text(added, title, chat_id):
    head = "🔔 ʙᴏᴛ ᴀᴅᴅᴇᴅ ᴛᴏ ɢʀᴏᴜᴘ ✅" if added else "🔔 ʙᴏᴛ ʀᴇᴍᴏᴠᴇᴅ ꜰʀᴏᴍ ɢʀᴏᴜᴘ 🚪"
    return (f"{head}\n{LINE}\n"
            f"📛 ɢʀᴏᴜᴘ ɴᴀᴍᴇ : {escape(title or '-')}\n"
            f"🆔 ɢʀᴏᴜᴘ ɪᴅ   : <code>{chat_id}</code>\n"
            f"{LINE}\n⏰ ᴛɪᴍᴇ       : {_now()}")


def start_text(user):
    who = escape(user.full_name or "-")
    name = f"@{escape(user.username)} | {who}" if user.username else who
    return f"ᴜꜱᴇʀ ꜱᴛᴀʀᴛ ʟᴏɢ\n\nɪᴅ: <code>{user.id}</code>\nɴᴀᴍᴇ: {name}"


async def log_start(context, user, is_new):
    if is_new or START_LOG_ALL:
        await send_log(context, start_text(user))


async def on_my_chat_member(update, context):
    ev = update.my_chat_member
    if not ev or ev.chat.type not in GROUPS:
        return
    old, new = ev.old_chat_member.status, ev.new_chat_member.status
    if new in IN and old in OUT:
        await send_log(context, group_text(True, ev.chat.title, ev.chat.id))
    elif new in OUT and old in IN + ("restricted",):
        await send_log(context, group_text(False, ev.chat.title, ev.chat.id))


def handlers():
    return [ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER)]
