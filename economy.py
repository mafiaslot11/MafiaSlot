from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram.ext import CallbackQueryHandler, CommandHandler

import os

import db
from common import (BET_MAX, BET_MIN, OWNER_ID, btn, edit_msg, ensure, esc, fmt, kb,
                    log, mention, new_id, parse_amount, url_btn)

MMT = ZoneInfo("Asia/Yangon")
DRAW_HOURS = (12, 18)

GROUP_LINK = os.getenv("GROUP_LINK", "")          # e.g. https://t.me/yourgroup
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "")   # e.g. yourname (without @)
GROUP_ID = os.getenv("GROUP_ID", "")               # e.g. -1001234567890 (bot must be admin there)
GROUP_BONUS = 300000

START_TEXT = (
    "<b>🎮 Game Bot\n"
    "Coin အသုံးပြုပြီးကြိုက်နှစ်သက်ရာ Game ကိုရွေးဆော့လို့ရပါတယ်\n\n"
    "Game List</b>\n\n"
    "<blockquote>Blackjack🃏\n"
    "Slot🎰\n"
    "Mine💣\n"
    "Groupmine🔥\n"
    "Triple Dice🎲\n"
    "Bingo🎱\n"
    "Mafia🕵️\n"
    "Lottery🏆</blockquote>\n"
    "<blockquote>Game Group join ပြီး Bonus Coin 300000 ယူပါ</blockquote>\n\n"
    "<i>ဆော့နည်းကို အောက်က Help Button ကိုနှိပ်ကြည့်ပါ 👇👇👇👇</i>"
)

HELP = (
    "📖 <b>ဆော့နည်း</b>\n\n"
    "💰 /coin – လက်ကျန်ကြည့်\n"
    "🎁 /give ပမာဏ – ပေးချင်သူရဲ့စာကို reply ထောက်ပြီး ရိုက်\n"
    "🏆 /top – coin အများဆုံး ၁၀ ယောက်\n"
    "🎁 /bonus – Game Group join ပြီးရင် Bonus coin 300,000 ယူ (တစ်ကြိမ်ပဲ)\n\n"
    "🃏 <b>/bj ပမာဏ</b> – 21 နီးအောင်ယူ၊ Dealer ကိုနိုင်ရင် ×၂\n"
    "🎰 <b>/slot ပမာဏ</b> – ၂ ခုတူ ×1.5၊ ၃ ခုလုံးတူ ×၂\n"
    "💣 <b>/mine ပမာဏ</b> – ကွက်ဖွင့်၊ မိုင်းမနင်းမိရင် Cash out\n"
    "🔥 <b>/gmine</b> – ဝင်ကြေး 10,000၊ နောက်ဆုံးကျန်သူ အကုန်ရ\n"
    "🎲 <b>/sic</b> – အကြီး/အသေး/နံပါတ်တူ၊ 3,000 ပုံသေ၊ မှန်ရင် ×၂\n"
    "😆 <b>/bingo</b> – ဝင်ကြေး 10,000၊ တန်းအရင်ပြည့်သူ pot အကုန်ရ\n"
    "♥️ <b>/mafia</b> – ဝင်ကြေး 10,000၊ ၄–၂၀ ယောက်၊ နိုင်တဲ့အဖွဲ့ pot ခွဲယူ\n"
    "🏎️ <b>/race</b> – Group ထဲမှာဖွင့်၊ ကားရွေးပြီး 10k–500k လောင်း၊ နိုင်ရင် ×၂ (ရလဒ်ကြည့် /racecheck)\n"
    "🏆 <b>/lottery နံပါတ် ပမာဏ</b> – 000–999၊ မှန်ရင် ×၁၀၊ ၁၂:၀၀ နဲ့ ၁၈:၀၀ ထီဖွင့်\n\n"
    f"Bet: {BET_MIN:,} ကနေ {BET_MAX:,} (bj / slot / mine / lottery)"
)


def start_text(u):
    who = f"@{u.username}" if u.username else mention(u.id, u.full_name)
    now = datetime.now(MMT)
    head = (f"👋 <b>ကြိုဆိုပါတယ်</b> {who}\n"
            f"🕒 ဝင်လာချိန်: {now:%d/%m/%Y %I:%M %p} (မြန်မာစံတော်ချိန်)\n\n")
    return head + START_TEXT


def start_markup():
    rows = []
    if GROUP_LINK:
        rows.append([url_btn("🎮 Game Group Join", GROUP_LINK)])
    if GROUP_ID:
        rows.append([btn("🎁 Bonus ယူမယ်", "bonus", "green", emoji=False)])
    row = [btn("📖 Help", "help", "blue", emoji=False)]
    if OWNER_USERNAME:
        row.append(url_btn("👑 Owner", "https://t.me/" + OWNER_USERNAME.lstrip("@")))
    rows.append(row)
    return kb(rows)


def owner_only(fn):
    async def wrapper(update, context):
        if update.effective_user.id != OWNER_ID:
            return
        await fn(update, context)

    return wrapper


async def start(update, context):
    u = await ensure(update)
    m = update.message
    markup = start_markup()
    photo = None
    try:
        ph = await context.bot.get_user_profile_photos(u.id, limit=1)
        if ph.total_count:
            photo = ph.photos[0][-1].file_id
    except Exception:
        log.info("no profile photo for %s", u.id)
    if photo:
        try:
            await m.reply_photo(photo, caption=start_text(u), reply_markup=markup)
            return
        except Exception:
            log.exception("start photo failed")
    await m.reply_text(start_text(u), reply_markup=markup)


async def help_cb(update, context):
    q = update.callback_query
    await q.answer()
    await context.bot.send_message(q.message.chat_id, HELP)


async def _bonus(context, u):
    """Returns the message to show. Pays once, only if the user is in the Game Group."""
    if not GROUP_ID:
        return "⚠️ Bonus ကို မသတ်မှတ်ရသေးပါ"
    try:
        member = await context.bot.get_chat_member(int(GROUP_ID), u.id)
    except Exception:
        log.exception("bonus: membership check failed")
        return "⚠️ စစ်လို့မရပါ၊ ခဏနေမှ ပြန်စမ်းပါ"
    in_group = member.status in ("member", "administrator", "creator") or (
        member.status == "restricted" and getattr(member, "is_member", False))
    if not in_group:
        return "❌ Game Group ကို အရင် join ပါ၊ ပြီးမှ ပြန်နှိပ်ပါ"
    db.ensure_user(u.id, u.full_name, u.username)
    if db.claim_once(u.id, "group_bonus", GROUP_BONUS):
        return f"🎉 Bonus {fmt(GROUP_BONUS)} coin ရပါပြီ!\n💰 လက်ကျန်: {fmt(db.balance(u.id))}"
    return "✅ Bonus ကို ယူပြီးသားပါ"


async def bonus_cmd(update, context):
    u = await ensure(update, notify=False)
    await update.message.reply_text(await _bonus(context, u))


async def bonus_cb(update, context):
    q = update.callback_query
    await q.answer(await _bonus(context, q.from_user), show_alert=True)


@owner_only
async def id_cmd(update, context):
    await update.message.reply_text(f"Chat ID: <code>{update.effective_chat.id}</code>")


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
        CommandHandler("bonus", bonus_cmd),
        CommandHandler("id", id_cmd),
        CommandHandler("give", give_cmd),
        CommandHandler("top", top_cmd),
        CommandHandler("addcoin", addcoin),
        CommandHandler("removecoin", removecoin),
        CommandHandler("setcoin", setcoin),
        CommandHandler("giftall", giftall),
        CommandHandler("giveaway", giveaway),
        CommandHandler("lottery", lottery_cmd),
        CallbackQueryHandler(giveaway_cb, pattern=r"^gd:"),
        CallbackQueryHandler(help_cb, pattern=r"^help$"),
        CallbackQueryHandler(bonus_cb, pattern=r"^bonus$"),
    ]
