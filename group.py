import asyncio
import random

from telegram import InlineKeyboardButton
from telegram.ext import CallbackQueryHandler, CommandHandler

import db
from common import (btn, clear_timer, edit_msg, ensure, esc, fmt, kb, log,
                    mention, new_id, refund_holds, set_timer)

GROUP_ONLY = "⚠️ ဒီဂိမ်းကို group ထဲမှာပဲ ကစားလို့ရပါတယ်။"


def _error_note():
    return "⚠️ Error ဖြစ်လို့ ပွဲပယ်ဖျက်ပြီး coin အားလုံး ပြန်ပေးပြီးပါပြီ"


# ======================= GROUP MINE =======================
GM, GM_CHAT = {}, {}
GM_ENTRY, GM_MAX = 10000, 10


def gm_lobby_text(g):
    names = "\n".join(f"• {esc(p['name'])}" for p in g["players"].values())
    return (f"💥 <b>Group Mine</b>\nဝင်ကြေး {fmt(GM_ENTRY)} | လူ ၂ ယောက်ကနေ {GM_MAX} ယောက်\n"
            f"နောက်ဆုံးကျန်သူ coin အကုန်ရမယ်!\n\nပါဝင်သူ ({len(g['players'])}):\n{names}")


def gm_lobby_kb(gid):
    return kb([[btn(f"Join ({fmt(GM_ENTRY)})", f"gm:{gid}:join", "green"),
                btn("Start", f"gm:{gid}:start", "green")]])


def gm_cleanup(g):
    GM.pop(g["id"], None)
    if GM_CHAT.get(g["chat"]) == g["id"]:
        GM_CHAT.pop(g["chat"], None)


async def gm_cmd(update, context):
    u = await ensure(update)
    m = update.message
    if m.chat.type == "private":
        await m.reply_text(GROUP_ONLY)
        return
    if m.chat_id in GM_CHAT:
        await m.reply_text("ဒီ group မှာ Group Mine ပွဲ ရှိနေပါပြီ")
        return
    hid = db.hold(u.id, GM_ENTRY, "gmine")
    if hid is None:
        await m.reply_text(f"❌ ဝင်ကြေး {fmt(GM_ENTRY)} မရှိပါ (လက်ကျန် {fmt(db.balance(u.id))})")
        return
    gid = new_id()
    g = {"id": gid, "chat": m.chat_id, "host": u.id, "stage": "lobby",
         "players": {u.id: {"name": u.full_name, "hid": hid}}}
    GM[gid] = g
    GM_CHAT[m.chat_id] = gid
    msg = await m.reply_text(gm_lobby_text(g), reply_markup=gm_lobby_kb(gid))
    g["mid"] = msg.message_id
    set_timer(context, "gml" + gid, 120, gm_lobby_timeout, gid)


async def gm_abort(context, g, note=None):
    clear_timer(context, "gml" + g["id"])
    clear_timer(context, "gmt" + g["id"])
    refund_holds([p["hid"] for p in g["players"].values()])
    gm_cleanup(g)
    try:
        await context.bot.send_message(g["chat"], note or _error_note())
    except Exception:
        pass


async def gm_lobby_timeout(context):
    g = GM.get(context.job.data)
    if not g or g["stage"] != "lobby":
        return
    try:
        if len(g["players"]) >= 2:
            await gm_start(context, g)
        else:
            await gm_abort(context, g, "⏰ လူမလောက်လို့ ပွဲပယ်ဖျက်ပါပြီ (coin ပြန်ပေးပြီး)")
    except Exception:
        log.exception("gm lobby timeout")
        await gm_abort(context, g)


def gm_board(g):
    rows = []
    for r in range(4):
        row = []
        for c in range(5):
            i = r * 5 + c
            if i in g["opened"]:
                row.append(InlineKeyboardButton("💣" if g["opened"][i] == "b" else "💎", callback_data="noop"))
            else:
                row.append(btn("🟩", f"gm:{g['id']}:t:{i}", "green", emoji=False))
        rows.append(row)
    return kb(rows)


def gm_text(g, note=""):
    cur = g["order"][g["ptr"]]
    alive = ", ".join(esc(g["players"][u]["name"]) for u in g["order"])
    pot = len(g["players"]) * GM_ENTRY
    return (f"💥 <b>Group Mine</b> | Pot {fmt(pot)}\n💣 မိုင်း {g['bombs']} လုံး / ၂၀ ကွက်\n\n"
            f"{note}\n\n👉 အလှည့်: {mention(cur, g['players'][cur]['name'])} (၃၀ စက္ကန့်)\n"
            f"အသက်ရှင်: {alive}")


async def gm_start(context, g):
    if g["stage"] != "lobby":
        return
    clear_timer(context, "gml" + g["id"])
    g["stage"] = "play"
    order = list(g["players"])
    random.shuffle(order)
    g.update(order=order, ptr=0, opened={}, bombs=max(5, len(order) + 2))
    g["mines"] = set(random.sample(range(20), g["bombs"]))
    await edit_msg(context.bot, g["chat"], g["mid"], gm_text(g, "ပွဲစပါပြီ!"), gm_board(g))
    set_timer(context, "gmt" + g["id"], 30, gm_turn_timeout, g["id"])


async def gm_pick(context, g, i):
    gid = g["id"]
    clear_timer(context, "gmt" + gid)
    cur = g["order"][g["ptr"]]
    who = mention(cur, g["players"][cur]["name"])
    if i in g["mines"]:
        g["opened"][i] = "b"
        g["order"].remove(cur)
        note = f"💥 {who} မိုင်းနင်းမိလို့ ထွက်ရပါပြီ!"
        if len(g["order"]) == 1:
            await gm_finish(context, g, note)
            return
        g["ptr"] %= len(g["order"])
    else:
        g["opened"][i] = "s"
        note = f"💎 {who} ဘေးကင်းတယ်"
        g["ptr"] = (g["ptr"] + 1) % len(g["order"])
    await edit_msg(context.bot, g["chat"], g["mid"], gm_text(g, note), gm_board(g))
    set_timer(context, "gmt" + gid, 30, gm_turn_timeout, gid)


async def gm_finish(context, g, note):
    winner = g["order"][0]
    pot = len(g["players"]) * GM_ENTRY
    ok = db.settle([p["hid"] for p in g["players"].values()], {winner: pot}, "gmine")
    gm_cleanup(g)
    if not ok:
        log.error("gmine settle failed")
        return
    await edit_msg(context.bot, g["chat"], g["mid"],
                   f"💥 <b>Group Mine ပြီးပါပြီ</b>\n\n{note}\n\n🏆 {mention(winner, g['players'][winner]['name'])} "
                   f"နိုင်ပါပြီ! +{fmt(pot)} coin")


async def gm_turn_timeout(context):
    g = GM.get(context.job.data)
    if not g or g["stage"] != "play":
        return
    try:
        free = [i for i in range(20) if i not in g["opened"]]
        await gm_pick(context, g, random.choice(free))
    except Exception:
        log.exception("gm timeout")
        await gm_abort(context, g)


async def gm_cb(update, context):
    q = update.callback_query
    p = q.data.split(":")
    g = GM.get(p[1])
    if not g:
        await q.answer("ပွဲပြီးသွားပါပြီ", show_alert=True)
        return
    u = q.from_user
    act = p[2]
    try:
        if act == "join":
            if g["stage"] != "lobby":
                await q.answer("ပွဲစပြီးပါပြီ", show_alert=True)
            elif u.id in g["players"]:
                await q.answer("ပါပြီးသားပါ")
            elif len(g["players"]) >= GM_MAX:
                await q.answer("လူပြည့်ပါပြီ", show_alert=True)
            else:
                db.ensure_user(u.id, u.full_name, u.username)
                hid = db.hold(u.id, GM_ENTRY, "gmine")
                if hid is None:
                    await q.answer(f"coin မလောက်ပါ (ဝင်ကြေး {fmt(GM_ENTRY)})", show_alert=True)
                    return
                g["players"][u.id] = {"name": u.full_name, "hid": hid}
                await q.answer("✅ ဝင်ပြီး")
                await edit_msg(context.bot, g["chat"], g["mid"], gm_lobby_text(g), gm_lobby_kb(g["id"]))
        elif act == "start":
            if u.id != g["host"]:
                await q.answer("ပွဲစသူပဲ စလို့ရပါတယ်", show_alert=True)
            elif g["stage"] != "lobby":
                await q.answer()
            elif len(g["players"]) < 2:
                await q.answer("အနည်းဆုံး ၂ ယောက် လိုပါတယ်", show_alert=True)
            else:
                await q.answer()
                await gm_start(context, g)
        elif act == "t":
            if g["stage"] != "play":
                await q.answer()
            elif u.id != g["order"][g["ptr"]]:
                await q.answer("သင့်အလှည့် မဟုတ်သေးပါ")
            else:
                i = int(p[3])
                if i in g["opened"]:
                    await q.answer()
                    return
                await q.answer()
                await gm_pick(context, g, i)
    except Exception:
        log.exception("gm cb")
        await gm_abort(context, g)


# ======================= SIC BO =======================
SIC = {}
SIC_BET, SIC_WIN, SIC_MAX, SIC_SECS = 3000, 2, 30, 30
SIC_LABEL = {"big": "အကြီး", "small": "အသေး", "triple": "နံပါတ်တူ"}
KEYCAP = {1: "1️⃣", 2: "2️⃣", 3: "3️⃣", 4: "4️⃣", 5: "5️⃣", 6: "6️⃣"}


def sic_frame(vals, title, shown=3, total=False):
    cells = [KEYCAP[vals[i]] if i < shown else "❓" for i in range(3)]
    t = f"🎲 <b>Sic Bo</b> — {title}\n\n<b>[  " + "  |  ".join(cells) + "  ]</b>"
    if total:
        t += f"\n\nစုစုပေါင်း = <b>{sum(vals)}</b>"
    return t


async def sic_cmd(update, context):
    await ensure(update)
    m = update.message
    if m.chat_id in SIC:
        await m.reply_text("ဒီ chat မှာ Sic Bo ပွဲ ဖွင့်ထားပါပြီ")
        return
    gid = new_id()
    msg = await m.reply_text(
        f"🎲 <b>Sic Bo</b> — လောင်းကြေး {fmt(SIC_BET)} (ပုံသေ)\n"
        f"အကြီး (11-17) / အသေး (4-10) / နံပါတ်တူ (၃ တုံးလုံးတူ)\n"
        f"မှန်ရင် ×{SIC_WIN} | {SIC_SECS} စက္ကန့်အတွင်း လောင်းပါ | လူ {SIC_MAX} ယောက်အထိ\n"
        f"⚠️ နှိပ်တာနဲ့ coin ချက်ချင်းဖြတ်မယ်၊ ပြောင်းလို့မရ",
        reply_markup=kb([[btn("အကြီး", f"sc:{gid}:big", "red"),
                          btn("အသေး", f"sc:{gid}:small", "blue"),
                          btn("နံပါတ်တူ", f"sc:{gid}:triple", "green")]]),
    )
    SIC[m.chat_id] = {"id": gid, "chat": m.chat_id, "mid": msg.message_id, "bets": {}}
    set_timer(context, "sic" + gid, SIC_SECS, sic_close, m.chat_id)


async def sic_cb(update, context):
    q = update.callback_query
    _, gid, choice = q.data.split(":")
    g = SIC.get(q.message.chat_id)
    if not g or g["id"] != gid:
        await q.answer("လောင်းချိန်ပြီးပါပြီ", show_alert=True)
        return
    u = q.from_user
    db.ensure_user(u.id, u.full_name, u.username)
    if u.id in g["bets"]:
        await q.answer("လောင်းပြီးပါပြီ", show_alert=True)
    elif len(g["bets"]) >= SIC_MAX:
        await q.answer("လူပြည့်ပါပြီ", show_alert=True)
    else:
        hid = db.hold(u.id, SIC_BET, "sic")
        if hid is None:
            await q.answer(f"coin မလောက်ပါ ({fmt(SIC_BET)} လို)", show_alert=True)
            return
        g["bets"][u.id] = (choice, hid, u.full_name)
        await q.answer(f"✅ {SIC_LABEL[choice]} ကို {fmt(SIC_BET)} လောင်းပြီး")


async def sic_close(context):
    chat = context.job.data
    g = SIC.pop(chat, None)
    if not g:
        return
    bets = g["bets"]
    if not bets:
        await edit_msg(context.bot, chat, g["mid"], "🎲 Sic Bo — ဘယ်သူမှ မလောင်းလို့ ပိတ်လိုက်ပါပြီ")
        return
    settled = False
    try:
        vals = [random.randint(1, 6) for _ in range(3)]
        mid = g["mid"]
        await edit_msg(context.bot, chat, mid, f"🎲 <b>Sic Bo</b> — လောင်းချိန်ပြီး ({len(bets)} ယောက်)\nအံစာလှိမ့်နေပါပြီ...")
        await asyncio.sleep(1.2)
        for _ in range(3):  # rolling effect
            fake = [random.randint(1, 6) for _ in range(3)]
            await edit_msg(context.bot, chat, mid, sic_frame(fake, "လှိမ့်နေတယ်..."))
            await asyncio.sleep(0.9)
        for k in (1, 2, 3):  # reveal one die at a time
            await edit_msg(context.bot, chat, mid, sic_frame(vals, "ပွင့်နေပြီ...", shown=k))
            await asyncio.sleep(1.1)
        total = sum(vals)
        triple = len(set(vals)) == 1
        outcome = "triple" if triple else ("big" if total >= 11 else "small")
        payouts, winners = {}, []
        for uid, (ch, hid, name) in bets.items():
            if ch == outcome:
                payouts[uid] = SIC_BET * SIC_WIN
                winners.append(mention(uid, name))
        settled = db.settle([b[1] for b in bets.values()], payouts, "sic")
        if not settled:
            return
        await edit_msg(context.bot, chat, mid, sic_frame(vals, "ရလဒ်", total=True))
        res = f"{' + '.join(KEYCAP[v] for v in vals)} = <b>{total}</b> → <b>{SIC_LABEL[outcome]}</b>"
        w = ("🎉 နိုင်သူ: " + ", ".join(winners) + f"\n(+{fmt(SIC_BET * (SIC_WIN - 1))} စီ)") if winners else "😢 ဘယ်သူမှ မနိုင်ပါ"
        await context.bot.send_message(chat, f"🎲 <b>Sic Bo ရလဒ်</b>\n{res}\n\n{w}")
    except Exception:
        log.exception("sic")
        if not settled:
            refund_holds([b[1] for b in bets.values()])
            try:
                await context.bot.send_message(chat, _error_note())
            except Exception:
                pass


# ======================= BINGO =======================
BG = {}
BINGO_ENTRY, BINGO_MAX, BINGO_LOBBY, BINGO_DELAY = 10000, 30, 600, 3.5  # lobby 600s = safety timeout only


def has_line(card, called):
    lines = [list(r) for r in card]
    lines += [[card[r][c] for r in range(5)] for c in range(5)]
    lines.append([card[i][i] for i in range(5)])
    lines.append([card[i][4 - i] for i in range(5)])
    return any(all(n in called for n in line) for line in lines)


def card_text(card, called):
    return "\n".join("  ".join(f"[{x:02d}]" if x in called else f" {x:02d} " for x in row) for row in card)


def bingo_lobby_text(g):
    names = "\n".join(f"• {esc(p['name'])}" for p in g["players"].values())
    return (f"🎱 <b>BINGO</b>\nဝင်ကြေး {fmt(BINGO_ENTRY)} | အနည်းဆုံး ၂ ယောက်\n"
            f"တန်း (ဘေး/ဒေါင်/ထောင့်ဖြတ်) အရင်ပြည့်သူ pot အကုန်ရ\n"
            f"👉 Join နှိပ်ပါ။ ပွဲစသူက <b>Start</b> နှိပ်ရင် ချက်ချင်းစမယ်\n\nပါဝင်သူ ({len(g['players'])}):\n{names}")


def bingo_kb(gid, lobby):
    rows = []
    if lobby:
        rows.append([btn(f"Join ({fmt(BINGO_ENTRY)})", f"bg:{gid}:join", "green"),
                     btn("Start", f"bg:{gid}:start", "green")])
    rows.append([btn("ကဒ်ကြည့်မယ်", f"bg:{gid}:card", "green")])
    return kb(rows)


def bingo_cleanup(g):
    if BG.get(g["chat"]) is g:
        BG.pop(g["chat"], None)


async def bingo_cmd(update, context):
    u = await ensure(update)
    m = update.message
    if m.chat.type == "private":
        await m.reply_text(GROUP_ONLY)
        return
    if m.chat_id in BG:
        await m.reply_text("ဒီ group မှာ Bingo ပွဲ ရှိနေပါပြီ")
        return
    hid = db.hold(u.id, BINGO_ENTRY, "bingo")
    if hid is None:
        await m.reply_text(f"❌ ဝင်ကြေး {fmt(BINGO_ENTRY)} မရှိပါ (လက်ကျန် {fmt(db.balance(u.id))})")
        return
    gid = new_id()
    g = {"id": gid, "chat": m.chat_id, "host": u.id, "stage": "lobby", "called": set(),
         "players": {u.id: {"name": u.full_name, "hid": hid, "card": _new_card()}}}
    BG[m.chat_id] = g
    msg = await m.reply_text(bingo_lobby_text(g), reply_markup=bingo_kb(gid, True))
    g["mid"] = msg.message_id
    set_timer(context, "bgl" + gid, BINGO_LOBBY, bingo_lobby_end, m.chat_id)


def _new_card():
    nums = random.sample(range(1, 51), 25)
    return [nums[i * 5:(i + 1) * 5] for i in range(5)]


async def bingo_begin(context, g):
    if g["stage"] != "lobby":
        return
    clear_timer(context, "bgl" + g["id"])
    g["stage"] = "play"
    await edit_msg(context.bot, g["chat"], g["mid"],
                   f"🎱 <b>BINGO စပါပြီ!</b> Pot {fmt(len(g['players']) * BINGO_ENTRY)}\nနံပါတ်တွေ စခေါ်မယ်...",
                   bingo_kb(g["id"], False))
    g["task"] = asyncio.create_task(bingo_run(context.bot, g))


async def bingo_lobby_end(context):
    """Safety timeout: nobody pressed Start for a long time -> cancel and refund."""
    g = BG.get(context.job.data)
    if not g or g["stage"] != "lobby":
        return
    refund_holds([p["hid"] for p in g["players"].values()])
    bingo_cleanup(g)
    await edit_msg(context.bot, g["chat"], g["mid"], "🎱 Bingo — မစတာကြာလို့ ပယ်ဖျက်ပါပြီ (coin ပြန်ပေးပြီး)")


async def bingo_run(bot, g):
    chat, pot = g["chat"], len(g["players"]) * BINGO_ENTRY
    settled = False
    try:
        pool = random.sample(range(1, 51), 50)
        for n in pool:
            await asyncio.sleep(BINGO_DELAY)
            g["called"].add(n)
            winners = [u for u, p in g["players"].items() if has_line(p["card"], g["called"])]
            if winners:
                share = pot // len(winners)
                pay = {u: share for u in winners}
                pay[winners[0]] += pot - share * len(winners)
                settled = db.settle([p["hid"] for p in g["players"].values()], pay, "bingo")
                if not settled:
                    return
                names = ", ".join(mention(u, g["players"][u]["name"]) for u in winners)
                await edit_msg(bot, chat, g["mid"],
                               f"🎱 <b>BINGO!</b> 🎉\nနောက်ဆုံးခေါ်: <b>{n}</b>\n\n🏆 {names}\n+{fmt(share)} coin စီ")
                return
            shown = " ".join(str(x) for x in sorted(g["called"]))
            await edit_msg(bot, chat, g["mid"],
                           f"🎱 <b>BINGO</b> | Pot {fmt(pot)}\nခေါ်လိုက်တဲ့နံပါတ်: <b>{n}</b>\n\nခေါ်ပြီး: {shown}",
                           bingo_kb(g["id"], False))
    except Exception:
        log.exception("bingo")
        if not settled:
            refund_holds([p["hid"] for p in g["players"].values()])
            try:
                await bot.send_message(chat, _error_note())
            except Exception:
                pass
    finally:
        bingo_cleanup(g)


async def bingo_cb(update, context):
    q = update.callback_query
    _, gid, act = q.data.split(":")
    g = BG.get(q.message.chat_id)
    if not g or g["id"] != gid:
        await q.answer("ပွဲပြီးသွားပါပြီ", show_alert=True)
        return
    u = q.from_user
    if act == "card":
        p = g["players"].get(u.id)
        if not p:
            await q.answer("သင် ပါဝင်မထားပါ", show_alert=True)
        else:
            await q.answer(card_text(p["card"], g["called"]), show_alert=True)
        return
    if act == "start":
        if u.id != g["host"]:
            await q.answer("ပွဲစသူပဲ Start နှိပ်လို့ရပါတယ်", show_alert=True)
        elif g["stage"] != "lobby":
            await q.answer()
        elif len(g["players"]) < 2:
            await q.answer("အနည်းဆုံး ၂ ယောက် လိုပါတယ်", show_alert=True)
        else:
            await q.answer()
            await bingo_begin(context, g)
        return
    if g["stage"] != "lobby":
        await q.answer("ပွဲစပြီးပါပြီ", show_alert=True)
    elif u.id in g["players"]:
        await q.answer("ပါပြီးသားပါ")
    elif len(g["players"]) >= BINGO_MAX:
        await q.answer("လူပြည့်ပါပြီ", show_alert=True)
    else:
        db.ensure_user(u.id, u.full_name, u.username)
        hid = db.hold(u.id, BINGO_ENTRY, "bingo")
        if hid is None:
            await q.answer(f"coin မလောက်ပါ (ဝင်ကြေး {fmt(BINGO_ENTRY)})", show_alert=True)
            return
        g["players"][u.id] = {"name": u.full_name, "hid": hid, "card": _new_card()}
        await q.answer("✅ ဝင်ပြီး — 'ကဒ်ကြည့်မယ်' နှိပ်ပါ")
        await edit_msg(context.bot, g["chat"], g["mid"], bingo_lobby_text(g), bingo_kb(gid, True))


def handlers():
    return [
        CommandHandler("gmine", gm_cmd),
        CommandHandler("sic", sic_cmd),
        CommandHandler("bingo", bingo_cmd),
        CallbackQueryHandler(gm_cb, pattern=r"^gm:"),
        CallbackQueryHandler(sic_cb, pattern=r"^sc:"),
        CallbackQueryHandler(bingo_cb, pattern=r"^bg:"),
    ]
