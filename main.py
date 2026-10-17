import logging
import os

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, Defaults

import db
import economy
import group
import mafia
import solo

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
log = logging.getLogger("bot")


async def post_init(app):
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
    for mod in (economy, solo, group, mafia):
        for h in mod.handlers():
            app.add_handler(h)
    economy.schedule(app)
    app.add_error_handler(on_error)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
