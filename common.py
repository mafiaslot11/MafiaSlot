import html
import logging
import os
import uuid

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest

import db

log = logging.getLogger("bot")

OWNER_ID = int(os.getenv("OWNER_ID", "0"))
BET_MIN, BET_MAX = 1000, 100000

# Telegram's coloured inline buttons use the "style" field
# (primary=blue, success=green, danger=red). The emoji is a fallback
# for apps that don't show the colour yet.
COLORS = {"green": ("🟢", "success"), "red": ("🔴", "danger"), "blue": ("🔵", "primary")}


def btn(text, data, color=None, emoji=True):
    if not color:
        return InlineKeyboardButton(text, callback_data=data)
    e, style = COLORS[color]
    label = f"{e} {text}" if emoji else text
    return InlineKeyboardButton(label, callback_data=data, api_kwargs={"style": style})


def url_btn(text, url):
    return InlineKeyboardButton(text, url=url)


def kb(rows):
    return InlineKeyboardMarkup(rows)


def esc(s):
    return html.escape(str(s))


def mention(uid, name):
    return f'<a href="tg://user?id={uid}">{esc(name)}</a>'


def fmt(n):
    return f"{int(n):,}"


def new_id():
    return uuid.uuid4().hex[:8]


def parse_amount(s, allow_zero=False):
    try:
        n = int(str(s).replace(",", ""))
    except ValueError:
        return None
    if n < 0 or (n == 0 and not allow_zero):
        return None
    return n


async def ensure(update, notify=True):
    u = update.effective_user
    new = db.ensure_user(u.id, u.full_name, u.username)
    if new and notify and update.message:
        await update.message.reply_text(f"🎁 Welcome bonus {fmt(db.WELCOME)} coin ရပါပြီ!")
    return u


async def read_bet(update, context, usage):
    m = update.message
    amt = parse_amount(context.args[0]) if context.args else None
    if amt is None:
        await m.reply_text(usage)
        return None
    if not BET_MIN <= amt <= BET_MAX:
        await m.reply_text(f"⚠️ လောင်းကြေး {fmt(BET_MIN)} ကနေ {fmt(BET_MAX)} ကြား ဖြစ်ရမယ်။")
        return None
    return amt


async def edit_msg(bot, chat, mid, text, markup=None):
    try:
        await bot.edit_message_text(text, chat_id=chat, message_id=mid, reply_markup=markup)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            log.warning("edit failed: %s", e)
    except Exception:
        log.exception("edit failed")


def set_timer(context, name, seconds, cb, data=None):
    clear_timer(context, name)
    context.job_queue.run_once(cb, seconds, name=name, data=data)


def clear_timer(context, name):
    for j in context.job_queue.get_jobs_by_name(name):
        j.schedule_removal()


def refund_holds(hids):
    for h in hids:
        try:
            db.refund_hold(h)
        except Exception:
            log.exception("refund failed for hold %s", h)
