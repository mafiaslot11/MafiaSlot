from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram.ext import CallbackQueryHandler, CommandHandler

import db
from common import (BET_MAX, BET_MIN, OWNER_ID, btn, edit_msg, ensure, esc, fmt, kb,
                    log, mention, new_id, parse_amount)

MMT = ZoneInfo("Asia/Yangon")
DRAW_HOURS = (12, 18)

HELP = (
    "🎮 <b>Game Bot</b>\n\n"
    "💰 /coin – လက်ကျန်ကြည့်\n"
    "🎁 /give ပမာဏ – သူများစာကို reply ထောက်ပြီး coin ပေး\n"
    "🏆 /top – coin အများဆုံး ၁၀ ယောက်\n\n"
    "🃏 /bj ပမာဏ\n🎰 /slot ပမာဏ\n💣 /mine ပမာဏ\n"
    "💥 /gmine – group mine\n🎲 /sic – Sic Bo\n🎱 /bingo\n🔪 /mafia\n"
    "🎟 /lottery နံပါတ် ပမာဏ – နေ့စဉ်ထီ"
)


def owner_only(fn):
    async def wrapper(update, context):
        if update.effective_user.id != OWNER_ID:
            return
        await fn(update, context)

    return wrapper


async def start(update, context):
    await ensure(update)
    await update.message.reply_text(HELP)


async def coin_cmd(update, context):
    u = await ensure(update)
    await update.message.reply_text(f"💰 {mention(u.id, u.full_name)} ရဲ့ coin: <b>{fmt(db.balance(u.id))}</b>")


async def give_cmd(update, context):
    u = await ensure(update)
    m = update.message
    rep = m.reply_to_message
    amt = parse_amount(context.args[0]) if context.args else None
    if not rep or amt is None:
        await m.reply_text("အသုံးပြုပုံ: ပေးချင်သူရဲ့ စာကို reply ထောက်ပြီး\n<code>/give 5000</code>")
        return
    to = rep.from_user
    if to.is_bot or to.id == u.id:
        await m.reply_text("⚠️ ဒီသူကို မပေးနိုင်ပါ။")
        return
    db.ensure_user(to.id, to.full_name, to.username)
    if db.transfer(u.id, to.id, amt):
        await m.reply_text(f"✅ {mention(u.id, u.full_name)} → {mention(to.id, to.full_name)}  <b>{fmt(amt)}</b> coin ပေးပြီး")
    else:
        await m.reply_text(f"❌ coin မလောက်ပါ (လက်ကျန် {fmt(db.balance(u.id))})")


async def top_cmd(update, context):
    await ensure(update)
    rows = db.top(10, OWNER_ID)
    if not rows:
        await update.message.reply_text("မရှိသေးပါ")
        return
    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 <b>Global Leaderboard</b>\n"]
    for i, r in enumerate(rows):
        name = f"@{r['username']}" if r["username"] else r["name"]
        lines.append(f"{medals[i] if i < 3 else str(i + 1) + '.'} {esc(name)} — <b>{fmt(r['coin'])}</b>")
    await update.message.reply_text("\n".join(lines))


# ---------------- owner ----------------
def _target(update, context):
    m, a = update.message, context.args
    zero = True
    if m.reply_to_message and len(a) >= 1:
        t = m.reply_to_message.from_user
        db.ensure_user(t.id, t.full_name, t.username)
        return t.id, parse_amount(a[0], allow_zero=zero)
    if len(a) >= 2:
        row = db.find_user(a[0])
        if row:
            return row["id"], parse_amount(a[1], allow_zero=zero)
    return None


async def _owner_op(update, context, mode):
    m = update.message
    t = _target(update, context)
    if not t or t[1] is None:
        await m.reply_text(
            "အသုံးပြုပုံ:\n• စာကို reply ထောက်ပြီး <code>/%s 5000</code>\n• <code>/%s @username 5000</code> (သို့) user id" % (mode, mode)
        )
        return
    uid, amt = t
    if mode == "addcoin":
        new = db.owner_adjust(uid, delta=amt, reason="owner add")
    elif mode == "removecoin":
        new = db.owner_adjust(uid, delta=-amt, reason="owner remove")
    else:
        new = db.owner_adjust(uid, to=amt, reason="owner set")
    if new is None:
        await m.reply_text("❌ ဒီ user ကို မတွေ့ပါ (bot ကို အရင်သုံးဖူးရမယ်)")
        return
    r = db.user_row(uid)
    await m.reply_text(f"✅ {mention(uid, r['name'])} ရဲ့ coin ယခု <b>{fmt(new)}</b>")


@owner_only
async def addcoin(update, context):
    await _owner_op(update, context, "addcoin")


@owner_only
async def removecoin(update, context):
    await _owner_op(update, context, "removecoin")


@owner_only
async def setcoin(update, context):
    await _owner_op(update, context, "setcoin")


@owner_only
async def giftall(update, context):
    amt = parse_amount(context.args[0]) if context.args else None
    if not amt:
        await update.message.reply_text("အသုံးပြုပုံ: <code>/giftall 5000</code>")
        return
    n = db.gift_all(amt)
    await update.message.reply_text(f"🎁 လူ {n} ယောက်ကို {fmt(amt)} coin စီ ပေးပြီး")


# ---------------- coin drop / giveaway ----------------
GD = {}


@owner_only
async def giveaway(update, context):
    amt = parse_amount(context.args[0]) if context.args else None
    if not amt:
        await update.message.reply_text("အသုံးပြုပုံ: <code>/giveaway 1000000</code>")
        return
    gid = new_id()
    msg = await update.message.reply_text(
        f"🎁 <b>GIVEAWAY {fmt(amt)} coin</b>\nအရင်နှိပ်တဲ့ ၃ ယောက်က ညီမျှခွဲယူမယ်!",
        reply_markup=kb([[btn("Claim", f"gd:{gid}", "green")]]),
    )
    GD[gid] = {"amt": amt, "got": [], "chat": msg.chat_id, "mid": msg.message_id}


async def giveaway_cb(update, context):
    q = update.callback_query
    gid = q.data.split(":")[1]
    g = GD.get(gid)
    if not g:
        await q.answer("ကုန်သွားပါပြီ", show_alert=True)
        return
    u = q.from_user
    db.ensure_user(u.id, u.full_name, u.username)
    if any(x == u.id for x, _ in g["got"]):
        await q.answer("ယူပြီးပါပြီ")
        return
    share = g["amt"] // 3
    db.owner_adjust(u.id, delta=share, reason="giveaway")
    g["got"].append((u.id, u.full_name))
    await q.answer(f"🎉 +{fmt(share)} coin ရပါပြီ", show_alert=True)
    who = "\n".join(f"✅ {mention(i, n)}  +{fmt(share)}" for i, n in g["got"])
    if len(g["got"]) >= 3:
        GD.pop(gid, None)
        await edit_msg(context.bot, g["chat"], g["mid"], f"🎁 <b>GIVEAWAY ပြီးပါပြီ</b>\n\n{who}")
    else:
        await edit_msg(
            context.bot, g["chat"], g["mid"],
            f"🎁 <b>GIVEAWAY {fmt(g['amt'])} coin</b>\nကျန်သေးတယ်: {3 - len(g['got'])} နေရာ\n\n{who}",
            kb([[btn("Claim", f"gd:{gid}", "green")]]),
        )


# ---------------- daily lottery (12:00 and 18:00 Myanmar time) ----------------
def next_draw(now=None):
    now = now or datetime.now(MMT)
    for h in DRAW_HOURS:
        t = now.replace(hour=h, minute=0, second=0, microsecond=0)
        if now < t:
            return t
    return (now + timedelta(days=1)).replace(hour=DRAW_HOURS[0], minute=0, second=0, microsecond=0)


def dkey(t):
    return t.strftime("%Y-%m-%d-%H")


def draw_time(key):
    return datetime.strptime(key, "%Y-%m-%d-%H").replace(tzinfo=MMT)


async def lottery_cmd(update, context):
    u = await ensure(update)
    m = update.message
    t = next_draw()
    a = context.args
    info = f"⏰ နောက်ထီဖွင့်ချိန်: <b>{t:%d/%m %H:%M}</b> (မြန်မာစံတော်ချိန်)"
    if len(a) != 2:
        await m.reply_text(
            "🎟 <b>Daily Lottery</b>\n"
            "<code>/lottery နံပါတ် ပမာဏ</code>  ဥပမာ <code>/lottery 527 5000</code>\n"
            f"နံပါတ် 000–999 | လောင်းကြေး {fmt(BET_MIN)}–{fmt(BET_MAX)}\n"
            "မှန်ရင် ၁၀ ဆ! တစ်ပွဲကို တစ်ကြိမ်၊ တစ်ရက် ၂ ပွဲ (12:00, 18:00)\n" + info
        )
        return
    if not (a[0].isdigit() and len(a[0]) <= 3):
        await m.reply_text("⚠️ နံပါတ်က 000 ကနေ 999 ကြား ဖြစ်ရမယ်")
        return
    num, amt = int(a[0]), parse_amount(a[1])
    if amt is None or not BET_MIN <= amt <= BET_MAX:
        await m.reply_text(f"⚠️ လောင်းကြေး {fmt(BET_MIN)} ကနေ {fmt(BET_MAX)} ကြား ဖြစ်ရမယ်")
        return
    r = db.place_bet(u.id, m.chat_id, dkey(t), num, amt)
    if r == "dup":
        await m.reply_text("⚠️ ဒီပွဲအတွက် ကံစမ်းပြီးပါပြီ။ နောက်ပွဲကို စောင့်ပါ။\n" + info)
    elif r == "poor":
        await m.reply_text(f"❌ coin မလောက်ပါ (လက်ကျန် {fmt(db.balance(u.id))})")
    else:
        await m.reply_text(f"🎟 နံပါတ် <b>{num:03d}</b> ကို {fmt(amt)} နဲ့ ကံစမ်းပြီး!\n" + info)


async def lottery_job(context):
    now = datetime.now(MMT)
    for key in db.pending_draws():
        if draw_time(key) > now:
            continue
        try:
            num, winners, chats = db.settle_draw(key)
        except Exception:
            log.exception("lottery settle failed")
            continue
        lines = [f"🎰 <b>ထီဖွင့်ပွဲ {draw_time(key):%d/%m %H:%M}</b>", f"🏆 ပေါက်ဂဏန်း: <b>{num:03d}</b>\n"]
        if winners:
            for uid, prize in winners:
                r = db.user_row(uid)
                lines.append(f"🎉 {mention(uid, r['name'])}  +{fmt(prize)}")
        else:
            lines.append("ဒီတစ်ကြိမ် ပေါက်သူ မရှိပါ")
        for ch in chats:
            try:
                await context.bot.send_message(ch, "\n".join(lines))
            except Exception:
                log.warning("lottery announce failed for chat %s", ch)


def schedule(app):
    app.job_queue.run_repeating(lottery_job, 30, first=10)


def handlers():
    return [
        CommandHandler("start", start),
        CommandHandler("coin", coin_cmd),
        CommandHandler("give", give_cmd),
        CommandHandler("top", top_cmd),
        CommandHandler("addcoin", addcoin),
        CommandHandler("removecoin", removecoin),
        CommandHandler("setcoin", setcoin),
        CommandHandler("giftall", giftall),
        CommandHandler("giveaway", giveaway),
        CommandHandler("lottery", lottery_cmd),
        CallbackQueryHandler(giveaway_cb, pattern=r"^gd:"),
    ]
