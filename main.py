import logging
import os

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, Defaults

import db
import economy
import group
import mafia
import racing
import welcome
import solo

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
log = logging.getLogger("bot")


def remove_fortune_data():
    """One-time cleanup: delete the fortune tables (readings, users' saved birth dates and
    history) and shrink the database file. Safe to run on every start."""
    names = ["fx_readings", "fx_compat", "fx_questions", "fx_meta", "fx_history",
             "fx_period", "fx_state", "fx_profile"]
    try:
        with db._lock:
            have = [r[0] for r in db._c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'fx\\_%' ESCAPE '\\'")]
        if not have:
            return
        with db.tx() as c:
            for n in names:
                c.execute(f"DROP TABLE IF EXISTS {n}")
        with db._lock:
            db._c.execute("VACUUM")
            db._c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        log.info("fortune data removed (%s tables)", len(have))
    except Exception:
        log.exception("fortune cleanup failed (harmless, the bot keeps running)")


async def post_init(app):
    remove_fortune_data()
    # Bot just (re)started: every unfinished game's bet is refunded.
    for uid, amount, game in db.refund_holds():
        log.info("refunded %s coin to %s (%s)", amount, uid, game)
        try:
            await app.bot.send_message(
                uid, f"♻️ Bot restart ဖြစ်လို့ မပြီးသေးတဲ့ {game} ပွဲရဲ့ လောင်းကြေး {amount:,} coin ပြန်ပေးပြီးပါပြီ")
        except Exception:
            pass
    app.job_queue.run_repeating(sweep, 300, first=300)


async def sweep(context):
    # Safety net: any bet held for more than 1 hour is returned.
    db.refund_holds(older=3600)


async def on_error(update, context):
    log.error("Unhandled error", exc_info=context.error)


def main():
    app = (
        Application.builder()
        .token(os.environ["BOT_TOKEN"])
        .defaults(Defaults(parse_mode=ParseMode.HTML))
        .post_init(post_init)
        .build()
    )
    # racing ကို economy ရှေ့မှာထားရမယ် (race ကားခလုတ်ကနေ ဝင်လာတဲ့ /start race_.. ကို economy ရဲ့ /start မတိုင်ခင် ဖမ်းဖို့)
    for mod in (racing, economy, solo, group, mafia, welcome):
        for h in mod.handlers():
            app.add_handler(h)
    economy.schedule(app)
    racing.schedule(app)
    app.add_error_handler(on_error)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
