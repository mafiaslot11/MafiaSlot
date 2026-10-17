import asyncio
import random

from telegram import InlineKeyboardButton
from telegram.ext import CallbackQueryHandler, CommandHandler

import db
from common import (btn, clear_timer, edit_msg, ensure, fmt, kb, log, new_id,
                    read_bet, set_timer)

NOT_ENOUGH = "❌ coin မလောက်ပါ (လက်ကျန် {})"


# ======================= SLOT =======================
# Uses Telegram's own animated 🎰. Telegram picks the dice value itself (about 62% of
# values have 2+ alike), so to get EXACTLY the win chance we want, the bot decides
# win/lose first and re-sends the dice until the value matches (non-matching dice
# are deleted immediately).
SLOT_WIN_CHANCE = 0.40      # chance to win (2 or 3 alike). Never a draw.
SLOT_PAIR_MULT = 1.5        # 2 alike
SLOT_TRIPLE_MULT = 2        # 3 alike
SLOT_MAX_TRIES = 20


def slot_mult(v):
    """Telegram 🎰 value 1-64 -> payout multiplier (0 = lose)."""
    n = v - 1
    distinct = len({n % 4, (n // 4) % 4, n // 16})
    if distinct == 1:
        return SLOT_TRIPLE_MULT
    if distinct == 2:
        return SLOT_PAIR_MULT
    return 0


async def slot_cmd(update, context):
    u = await ensure(update)
    m = update.message
    amt = await read_bet(update, context, "🎰 <code>/slot 5000</code>\nအနည်းဆုံး 1,000 – အများဆုံး 100,000")
    if amt is None:
        return
    hid = db.hold(u.id, amt, "slot")
    if hid is None:
        await m.reply_text(NOT_ENOUGH.format(fmt(db.balance(u.id))))
        return
    settled = False
    try:
        want_win = random.random() < SLOT_WIN_CHANCE
        dice = None
        for _ in range(SLOT_MAX_TRIES):
            d = await m.reply_dice(emoji="🎰")
            if (slot_mult(d.dice.value) > 0) == want_win:
                dice = d
                break
            try:
                await d.delete()
            except Exception:
                pass
        if dice is None:  # extremely unlikely
            db.refund_hold(hid)
            settled = True
            await m.reply_text("⚠️ ပြန်စမ်းပါ၊ coin ပြန်ပေးပြီးပါပြီ")
            return
        mult = slot_mult(dice.dice.value)
        await asyncio.sleep(3)
        pay = int(amt * mult)
        settled = db.settle([hid], {u.id: pay}, "slot")
        if not settled:
            return
        if mult == SLOT_TRIPLE_MULT:
            t = f"🎉 ၃ ခုလုံးတူ! ×{mult} +{fmt(pay - amt)} coin"
        elif mult:
            t = f"🎉 ၂ ခုတူ! ×{mult} +{fmt(pay - amt)} coin"
        else:
            t = f"😢 မတူလို့ ရှုံးပါပြီ -{fmt(amt)} coin"
        await m.reply_text(f"{t}\n💰 လက်ကျန်: {fmt(db.balance(u.id))}")
    except Exception:
        log.exception("slot")
        if not settled and db.refund_hold(hid):
            try:
                await m.reply_text("⚠️ Error ဖြစ်လို့ coin ပြန်ပေးပြီးပါပြီ")
            except Exception:
                pass


# ======================= BLACKJACK =======================
BJ = {}


def val(h):
    t = sum(10 if r in "TJQK" else 11 if r == "A" else int(r) for r, _ in h)
    a = sum(1 for r, _ in h if r == "A")
    while t > 21 and a:
        t -= 10
        a -= 1
    return t


def hs(h):
    return " ".join(("10" if r == "T" else r) + s for r, s in h)


def bj_text(g, final=None):
    d = f"{hs(g['d'])} ({val(g['d'])})" if final else f"{hs(g['d'][:1])} ??"
    t = (f"🃏 <b>Blackjack</b> | လောင်းကြေး {fmt(g['bet'])}\n\n"
         f"Dealer: {d}\nသင်: {hs(g['p'])} ({val(g['p'])})")
    return t + ("\n\n" + final if final else "")


def bj_kb(gid):
    return kb([[btn("Hit", f"bj:{gid}:h", "green"), btn("Stand", f"bj:{gid}:s", "red")]])


async def bj_cmd(update, context):
    u = await ensure(update)
    m = update.message
    amt = await read_bet(update, context, "🃏 <code>/bj 5000</code>\nအနည်းဆုံး 1,000 – အများဆုံး 100,000")
    if amt is None:
        return
    hid = db.hold(u.id, amt, "bj")
    if hid is None:
        await m.reply_text(NOT_ENOUGH.format(fmt(db.balance(u.id))))
        return
    gid = new_id()
    deck = [(r, s) for r in "A23456789TJQK" for s in "♠♥♦♣"]
    random.shuffle(deck)
    g = {"id": gid, "uid": u.id, "hid": hid, "bet": amt, "deck": deck,
         "p": [deck.pop(), deck.pop()], "d": [deck.pop(), deck.pop()], "chat": m.chat_id}
    BJ[gid] = g
    try:
        msg = await m.reply_text(bj_text(g), reply_markup=bj_kb(gid))
        g["mid"] = msg.message_id
        set_timer(context, "bj" + gid, 120, bj_timeout, gid)
        if val(g["p"]) == 21:
            await bj_end(context, gid)
    except Exception:
        log.exception("bj start")
        await bj_abort(context, gid)


async def bj_end(context, gid):
    g = BJ.pop(gid, None)
    if not g:
        return
    clear_timer(context, "bj" + gid)
    p, d = g["p"], g["d"]
    nat = len(p) == 2 and val(p) == 21
    if not nat:
        while val(p) <= 21 and val(d) < 17:
            d.append(g["deck"].pop())
    pv, dv = val(p), val(d)
    dnat = len(d) == 2 and dv == 21
    if pv > 21:
        mult, res = 0, "💥 Bust! ရှုံးပါပြီ"
    elif nat and not dnat:
        mult, res = 2.5, "🎉 Blackjack!"
    elif dv > 21 or pv > dv:
        mult, res = 2, "🎉 နိုင်ပါပြီ"
    elif pv == dv:
        mult, res = 1, "🤝 သရေ — လောင်းကြေးပြန်ရ"
    else:
        mult, res = 0, "😢 ရှုံးပါပြီ"
    pay = int(g["bet"] * mult)
    if not db.settle([g["hid"]], {g["uid"]: pay}, "bj"):
        return
    net = pay - g["bet"]
    sign = f"+{fmt(net)}" if net > 0 else (f"-{fmt(-net)}" if net < 0 else "±0")
    text = bj_text(g, f"{res}\n{sign} coin | လက်ကျန် {fmt(db.balance(g['uid']))}")
    await edit_msg(context.bot, g["chat"], g["mid"], text)


async def bj_abort(context, gid):
    g = BJ.pop(gid, None)
    if not g:
        return
    clear_timer(context, "bj" + gid)
    if db.refund_hold(g["hid"]):
        try:
            await context.bot.send_message(g["chat"], "⚠️ Error ဖြစ်လို့ coin ပြန်ပေးပြီးပါပြီ")
        except Exception:
            pass


async def bj_timeout(context):
    try:
        await bj_end(context, context.job.data)
    except Exception:
        log.exception("bj timeout")
        await bj_abort(context, context.job.data)


async def bj_cb(update, context):
    q = update.callback_query
    _, gid, act = q.data.split(":")
    g = BJ.get(gid)
    if not g:
        await q.answer("ပွဲပြီးသွားပါပြီ", show_alert=True)
        return
    if q.from_user.id != g["uid"]:
        await q.answer("ဒါ သင့်ပွဲ မဟုတ်ပါ")
        return
    await q.answer()
    try:
        if act == "h":
            g["p"].append(g["deck"].pop())
            if val(g["p"]) >= 21:
                await bj_end(context, gid)
            else:
                await edit_msg(context.bot, g["chat"], g["mid"], bj_text(g), bj_kb(gid))
                set_timer(context, "bj" + gid, 120, bj_timeout, gid)
        else:
            await bj_end(context, gid)
    except Exception:
        log.exception("bj cb")
        await bj_abort(context, gid)


# ======================= MINE =======================
MINE = {}


def mine_mult(m, k):
    """Fair odds x0.97 (3% house edge). 20 tiles, m mines, k safe picks."""
    r = 0.97
    for i in range(k):
        r *= (20 - i) / (20 - m - i)
    return r


def mine_text(g):
    k = len(g["opened"])
    return (f"💣 <b>Mine</b> | မိုင်း {g['m']} လုံး | လောင်းကြေး {fmt(g['bet'])}\n"
            f"💎 {k} ကွက်ဖွင့်ပြီး | x{mine_mult(g['m'], k):.2f}")


def mine_board(g, reveal=False):
    rows = []
    for r in range(4):
        row = []
        for c in range(5):
            i = r * 5 + c
            if reveal or i in g["opened"]:
                row.append(InlineKeyboardButton("💣" if i in g["mines"] else "💎", callback_data="noop"))
            else:
                row.append(btn("🟩", f"mn:{g['id']}:t:{i}", "green", emoji=False))
        rows.append(row)
    if not reveal and g["opened"]:
        pay = int(g["bet"] * mine_mult(g["m"], len(g["opened"])))
        rows.append([btn(f"Cash out {fmt(pay)}", f"mn:{g['id']}:cash", "green")])
    return kb(rows)


async def mine_cmd(update, context):
    u = await ensure(update)
    m = update.message
    amt = await read_bet(update, context, "💣 <code>/mine 5000</code>\nအနည်းဆုံး 1,000 – အများဆုံး 100,000")
    if amt is None:
        return
    if db.balance(u.id) < amt:
        await m.reply_text(NOT_ENOUGH.format(fmt(db.balance(u.id))))
        return
    gid = new_id()
    g = {"id": gid, "uid": u.id, "bet": amt, "stage": "choose", "chat": m.chat_id}
    MINE[gid] = g
    msg = await m.reply_text(
        f"💣 <b>Mine</b> | လောင်းကြေး {fmt(amt)}\nမိုင်း ဘယ်နှစ်လုံး ထည့်မလဲ? (များရင် ဆများတယ်)",
        reply_markup=kb([[btn(str(n), f"mn:{gid}:c:{n}", "green") for n in (6, 10, 15)]]),
    )
    g["mid"] = msg.message_id
    set_timer(context, "mn" + gid, 180, mine_timeout, gid)


async def mine_end(context, gid, won):
    g = MINE.pop(gid, None)
    if not g:
        return
    clear_timer(context, "mn" + gid)
    k = len(g["opened"])
    pay = int(g["bet"] * mine_mult(g["m"], k)) if won else 0
    if not db.settle([g["hid"]], {g["uid"]: pay}, "mine"):
        return
    if won:
        res = f"💰 Cash out! +{fmt(pay - g['bet'])} coin"
    else:
        res = f"💥 မိုင်းနင်းမိပါပြီ -{fmt(g['bet'])} coin"
    await edit_msg(context.bot, g["chat"], g["mid"],
                   mine_text(g) + f"\n\n{res}\n💰 လက်ကျန် {fmt(db.balance(g['uid']))}",
                   mine_board(g, reveal=True))


async def mine_abort(context, gid):
    g = MINE.pop(gid, None)
    if not g:
        return
    clear_timer(context, "mn" + gid)
    if g.get("hid") and db.refund_hold(g["hid"]):
        try:
            await context.bot.send_message(g["chat"], "⚠️ Error ဖြစ်လို့ coin ပြန်ပေးပြီးပါပြီ")
        except Exception:
            pass


async def mine_timeout(context):
    gid = context.job.data
    g = MINE.get(gid)
    if not g:
        return
    try:
        if g["stage"] == "play" and g["opened"]:
            await mine_end(context, gid, True)
        else:
            MINE.pop(gid, None)
            if g.get("hid"):
                db.refund_hold(g["hid"])
            await edit_msg(context.bot, g["chat"], g["mid"], "⏰ အချိန်ကုန်လို့ ပယ်ဖျက်လိုက်ပါပြီ (coin ပြန်ရပါပြီ)")
    except Exception:
        log.exception("mine timeout")
        await mine_abort(context, gid)


async def mine_cb(update, context):
    q = update.callback_query
    p = q.data.split(":")
    gid, act = p[1], p[2]
    g = MINE.get(gid)
    if not g:
        await q.answer("ပွဲပြီးသွားပါပြီ", show_alert=True)
        return
    if q.from_user.id != g["uid"]:
        await q.answer("ဒါ သင့်ပွဲ မဟုတ်ပါ")
        return
    try:
        if act == "c" and g["stage"] == "choose":
            n = int(p[3])
            if n not in (6, 10, 15):
                return
            hid = db.hold(g["uid"], g["bet"], "mine")
            if hid is None:
                MINE.pop(gid, None)
                clear_timer(context, "mn" + gid)
                await q.answer("coin မလောက်ပါ", show_alert=True)
                await edit_msg(context.bot, g["chat"], g["mid"], "❌ coin မလောက်ပါ")
                return
            g.update(stage="play", hid=hid, m=n, mines=set(random.sample(range(20), n)), opened=set())
            set_timer(context, "mn" + gid, 180, mine_timeout, gid)
            await q.answer()
            await edit_msg(context.bot, g["chat"], g["mid"], mine_text(g), mine_board(g))
        elif act == "t" and g["stage"] == "play":
            i = int(p[3])
            if i in g["opened"]:
                await q.answer()
                return
            await q.answer()
            if i in g["mines"]:
                await mine_end(context, gid, False)
            else:
                g["opened"].add(i)
                if len(g["opened"]) == 20 - g["m"]:
                    await mine_end(context, gid, True)
                else:
                    await edit_msg(context.bot, g["chat"], g["mid"], mine_text(g), mine_board(g))
                    set_timer(context, "mn" + gid, 180, mine_timeout, gid)
        elif act == "cash" and g["stage"] == "play" and g["opened"]:
            await q.answer()
            await mine_end(context, gid, True)
        else:
            await q.answer()
    except Exception:
        log.exception("mine cb")
        await mine_abort(context, gid)


async def noop(update, context):
    await update.callback_query.answer()


def handlers():
    return [
        CommandHandler("slot", slot_cmd),
        CommandHandler("bj", bj_cmd),
        CommandHandler("mine", mine_cmd),
        CallbackQueryHandler(bj_cb, pattern=r"^bj:"),
        CallbackQueryHandler(mine_cb, pattern=r"^mn:"),
        CallbackQueryHandler(noop, pattern=r"^noop$"),
    ]
