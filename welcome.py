"""Welcome message (image + quote-style caption) for new members of the official group."""
from pathlib import Path

from telegram.ext import MessageHandler, filters

from common import esc, log, mention
import os

GROUP_ID = os.getenv("GROUP_ID", "")
IMAGE = Path(__file__).with_name("welcome.jpg")
_file_id = None  # after the first upload Telegram gives an id we reuse


def _who(u):
    return f"@{u.username}" if u.username else mention(u.id, u.full_name)


async def welcome(update, context):
    global _file_id
    m = update.message
    if not GROUP_ID or str(m.chat_id) != GROUP_ID:
        return
    names = ", ".join(_who(u) for u in m.new_chat_members if not u.is_bot)
    if not names:
        return
    caption = (
        "<blockquote>"
        f"Game Group လေးကနေကြိုဆိုပါတယ် {names}။\n"
        "ပျော်ပျော်ရွှင်ရွှင်နဲ့ ဆော့လို့ရပါတယ်။\n"
        "Game ဆော့နည်းကို Pin ထောက်ပေးထားပါတယ်"
        "</blockquote>"
    )
    try:
        if _file_id:
            await context.bot.send_photo(m.chat_id, _file_id, caption=caption)
        elif IMAGE.exists():
            msg = await context.bot.send_photo(m.chat_id, IMAGE, caption=caption)
            _file_id = msg.photo[-1].file_id
        else:
            await context.bot.send_message(m.chat_id, caption)
    except Exception:
        log.exception("welcome failed")
        try:
            await context.bot.send_message(m.chat_id, caption)
        except Exception:
            pass


def handlers():
    return [MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome)]
