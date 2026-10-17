import asyncio
import random
from collections import Counter

from telegram.error import TelegramError
from telegram.ext import CallbackQueryHandler, CommandHandler

import db
from common import (btn, clear_timer, edit_msg, ensure, esc, fmt, kb, log,
                    new_id, refund_holds, set_timer)

MF, MF_CHAT = {}, {}
ENTRY, MIN_P, MAX_P = 10000, 4, 20
LOBBY_SECS, NIGHT_SECS, DAY_SECS, MAX_ROUNDS = 150, 45, 75, 8
ROLE = {"mafia": "🔪 Mafia", "doctor": "💉 Doctor", "detective": "🕵️ Detective", "villager": "👨‍🌾 ရွာသား"}


def lobby_text(g):
    names = "\n".join(f"• {esc(p['name'])}" for p in g["players"].values())
    return (f"🔪 <b>MAFIA</b>\nဝင်ကြေး {fmt(ENTRY)} | လူ {MIN_P}–{MAX_P} ယောက်\n"
            f"နိုင်တဲ့အဖွဲ့ထဲက အသက်ရှင်ကျန်သူတွေ pot ကို ညီမျှခွဲယူမယ်\n"
            f"⚠️ Bot ကို private မှာ /start နှိပ်ထားမှ Join လို့ရမယ် (role ကို DM ပို့လို့)\n\n"
            f"ပါဝင်သူ ({len(g['players'])}):\n{names}")


def lobby_kb(gid):
    return kb([[btn(f"Join ({fmt(ENTRY)})", f"mf:{gid}:join", "blue"),
                btn("Leave", f"mf:{gid}:leave", "blue"),
                btn("Start", f"mf:{gid}:start", "blue")]])


def cleanup(g):
    MF.pop(g["id"], None)
    if MF_CHAT.get(g["chat"]) == g["id"]:
        MF_CHAT.pop(g["chat"], None)


def nm(g, uid):
    return esc(g["players"][uid]["name"])


async def mafia_cmd(update, context):
    u = await ensure(update)
    m = update.message
    if m.chat.type == "private":
        await m.reply_text("⚠️ Group ထဲမှာပဲ ကစားလို့ရပါတယ်။")
        return
    if m.chat_id in MF_CHAT:
        await m.reply_text("ဒီ group မှာ Mafia ပွဲ ရှိနေပါပြီ")
        return
    if db.balance(u.id) < ENTRY:
        await m.reply_text(f"❌ ဝင်ကြေး {fmt(ENTRY)} မရှိပါ (လက်ကျန် {fmt(db.balance(u.id))})")
        return
    try:
        await context.bot.send_message(u.id, "✅ Mafia ပွဲမှာ ဝင်ပြီးပါပြီ။ role ကို ဒီမှာ ပို့ပေးပါမယ်။")
    except TelegramError:
        me = await context.bot.get_me()
        await m.reply_text(f"⚠️ Bot ကို private မှာ အရင် /start နှိပ်ပါ → https://t.me/{me.username}?start=1")
        return
    hid = db.hold(u.id, ENTRY, "mafia")
    if hid is None:
        await m.reply_text("❌ coin မလောက်ပါ")
        return
    gid = new_id()
    g = {"id": gid, "chat": m.chat_id, "host": u.id, "phase": "lobby",
         "players": {u.id: {"name": u.full_name, "hid": hid, "role": None, "alive": True}}}
    MF[gid] = g
    MF_CHAT[m.chat_id] = gid
    msg = await m.reply_text(lobby_text(g), reply_markup=lobby_kb(gid))
    g["mid"] = msg.message_id
    set_timer(context, "mfl" + gid, LOBBY_SECS, lobby_timeout, gid)


async def lobby_timeout(context):
    g = MF.get(context.job.data)
    if not g or g["phase"] != "lobby":
        return
    if len(g["players"]) >= MIN_P:
        begin(context, g)
    else:
        refund_holds([p["hid"] for p in g["players"].values()])
        cleanup(g)
        await edit_msg(context.bot, g["chat"], g["mid"], "🔪 Mafia — လူမလောက်လို့ ပယ်ဖျက်ပါပြီ (coin ပြန်ပေးပြီး)")


def begin(context, g):
    if g["phase"] != "lobby":
        return
    clear_timer(context, "mfl" + g["id"])
    g["phase"] = "running"
    g["task"] = asyncio.create_task(run(context.bot, g))


async def mf_cb(update, context):
    q = update.callback_query
    p = q.data.split(":")
    g = MF.get(p[1])
    if not g:
        await q.answer("ပွဲပြီးသွားပါပြီ", show_alert=True)
        return
    u = q.from_user
    act = p[2]
    if act in ("join", "leave", "start"):
        await lobby_action(update, context, g, act)
        return
    # night actions (sent by DM): k = kill, s = save, i = investigate
    target = int(p[3])
    pl = g["players"].get(u.id)
    need = {"k": "mafia", "s": "doctor", "i": "detective"}.get(act)
    if g["phase"] != "night" or not pl or not pl["alive"] or pl["role"] != need:
        await q.answer("အချိန်မဟုတ်ပါ", show_alert=True)
        return
    if u.id in g["acted"]:
        await q.answer("ရွေးပြီးပါပြီ")
        return
    tp = g["players"].get(target)
    if not tp or not tp["alive"]:
        await q.answer("ရွေးလို့မရပါ", show_alert=True)
        return
    g["acted"].add(u.id)
    if act == "k":
        g["night"]["kill"][u.id] = target
    elif act == "s":
        g["night"]["save"] = target
    await q.answer("✅ ရွေးပြီး")
    try:
        await q.edit_message_text(f"✅ ရွေးပြီးပါပြီ: {esc(tp['name'])}")
    except Exception:
        pass
    if act == "i":
        res = "🔪 Mafia ဖြစ်ပါတယ်!" if tp["role"] == "mafia" else "✅ Mafia မဟုတ်ပါ"
        try:
            await context.bot.send_message(u.id, f"🔎 {esc(tp['name'])} — {res}")
        except Exception:
            pass


async def lobby_action(update, context, g, act):
    q = update.callback_query
    u = q.from_user
    if g["phase"] != "lobby":
        await q.answer("ပွဲစပြီးပါပြီ", show_alert=True)
        return
    if act == "join":
        if u.id in g["players"]:
            await q.answer("ပါပြီးသားပါ")
            return
        if len(g["players"]) >= MAX_P:
            await q.answer("လူပြည့်ပါပြီ", show_alert=True)
            return
        db.ensure_user(u.id, u.full_name, u.username)
        if db.balance(u.id) < ENTRY:
            await q.answer(f"coin မလောက်ပါ (ဝင်ကြေး {fmt(ENTRY)})", show_alert=True)
            return
        try:
            await context.bot.send_message(u.id, "✅ Mafia ပွဲမှာ ဝင်ပြီးပါပြီ။ role ကို ဒီမှာ ပို့ပေးပါမယ်။")
        except TelegramError:
            await q.answer("Bot ကို private chat မှာ အရင် /start နှိပ်ပြီးမှ ပြန် Join နှိပ်ပါ", show_alert=True)
            return
        hid = db.hold(u.id, ENTRY, "mafia")
        if hid is None:
            await q.answer("coin မလောက်ပါ", show_alert=True)
            return
        g["players"][u.id] = {"name": u.full_name, "hid": hid, "role": None, "alive": True}
        await q.answer("✅ ဝင်ပြီး")
    elif act == "leave":
        p = g["players"].get(u.id)
        if not p or u.id == g["host"]:
            await q.answer("ထွက်လို့မရပါ", show_alert=True)
            return
        db.refund_hold(p["hid"])
        del g["players"][u.id]
        await q.answer("ထွက်ပြီး၊ coin ပြန်ရပါပြီ")
    else:
        if u.id != g["host"]:
            await q.answer("ပွဲစသူပဲ စလို့ရပါတယ်", show_alert=True)
            return
        if len(g["players"]) < MIN_P:
            await q.answer(f"အနည်းဆုံး {MIN_P} ယောက် လိုပါတယ်", show_alert=True)
            return
        await q.answer()
        begin(context, g)
        return
    await edit_msg(context.bot, g["chat"], g["mid"], lobby_text(g), lobby_kb(g["id"]))


async def mfv_cb(update, context):
    q = update.callback_query
    p = q.data.split(":")
    g = MF.get(p[1])
    if not g or g["phase"] != "day":
        await q.answer("မဲပေးချိန် မဟုတ်ပါ", show_alert=True)
        return
    u = q.from_user
    target = int(p[2])
    pl = g["players"].get(u.id)
    if not pl or not pl["alive"]:
        await q.answer("မဲပေးခွင့် မရှိပါ", show_alert=True)
    elif u.id in g["votes"]:
        await q.answer("မဲပေးပြီးပါပြီ")
    elif target == u.id:
        await q.answer("ကိုယ့်ကိုယ်ကို မဲမပေးနိုင်ပါ")
    elif target not in g["players"] or not g["players"][target]["alive"]:
        await q.answer("ရွေးလို့မရပါ")
    else:
        g["votes"][u.id] = target
        await q.answer(f"🗳 {g['players'][target]['name']} ကို မဲပေးပြီး")


# ---------------- game flow ----------------
def alive_ids(g):
    return [u for u, p in g["players"].items() if p["alive"]]


def winner(g):
    alive = alive_ids(g)
    mafia = sum(1 for u in alive if g["players"][u]["role"] == "mafia")
    town = len(alive) - mafia
    if mafia == 0:
        return "town"
    if mafia >= town:
        return "mafia"
    return None


def top_vote(votes, tie_random):
    if not votes:
        return None
    c = Counter(votes).most_common()
    best = [t for t, n in c if n == c[0][1]]
    if len(best) == 1:
        return best[0]
    return random.choice(best) if tie_random else None


async def night(bot, g, rnd):
    gid, chat = g["id"], g["chat"]
    g["phase"] = "night"
    g["acted"] = set()
    g["night"] = {"kill": {}, "save": None}
    alive = alive_ids(g)
    await bot.send_message(chat, f"🌙 <b>ည {rnd}</b> — အားလုံး အိပ်ပါပြီ။ Mafia / Doctor / Detective တွေ "
                                 f"bot ရဲ့ private chat မှာ ရွေးပါ ({NIGHT_SECS} စက္ကန့်)")
    actors = set()
    for uid in alive:
        role = g["players"][uid]["role"]
        if role == "villager":
            continue
        act = {"mafia": "k", "doctor": "s", "detective": "i"}[role]
        targets = [t for t in alive
                   if not (t == uid and role != "doctor")
                   and not (role == "mafia" and g["players"][t]["role"] == "mafia")]
        rows = [[btn(g["players"][t]["name"], f"mf:{gid}:{act}:{t}", "blue", emoji=False)] for t in targets]
        title = {"k": "🔪 ဘယ်သူ့ကို သတ်မလဲ?", "s": "💉 ဘယ်သူ့ကို ကယ်မလဲ?", "i": "🕵️ ဘယ်သူ့ကို စစ်မလဲ?"}[act]
        try:
            await bot.send_message(uid, f"{title}\n(ညတစ်ည အတွက်)", reply_markup=kb(rows))
            actors.add(uid)
        except Exception:
            log.warning("night DM failed for %s", uid)
    for _ in range(NIGHT_SECS):
        await asyncio.sleep(1)
        if actors <= g["acted"]:
            break
    g["phase"] = "dawn"
    target = top_vote(list(g["night"]["kill"].values()), tie_random=True)
    if target is not None and target == g["night"]["save"]:
        target = None
    if target is not None:
        g["players"][target]["alive"] = False
    return target


async def day(bot, g):
    gid, chat = g["id"], g["chat"]
    g["phase"] = "day"
    g["votes"] = {}
    alive = alive_ids(g)
    rows = [[btn(g["players"][t]["name"], f"mfv:{gid}:{t}", "blue", emoji=False)] for t in alive]
    msg = await bot.send_message(
        chat, f"🗳 <b>မဲပေးချိန်</b> ({DAY_SECS} စက္ကန့်) — ဘယ်သူ့ကို ထုတ်မလဲ?\nအကုန်ဆွေးနွေးပြီး မဲပေးပါ။",
        reply_markup=kb(rows))
    for _ in range(DAY_SECS):
        await asyncio.sleep(1)
        if len(g["votes"]) >= len(alive):
            break
    g["phase"] = "resolve"
    out = top_vote(list(g["votes"].values()), tie_random=False)
    if out is None:
        text = "⚖️ မဲတူ (သို့) မဲမရှိလို့ ဘယ်သူ့ကိုမှ မထုတ်ပါ"
    else:
        g["players"][out]["alive"] = False
        text = f"⚖️ <b>{nm(g, out)}</b> ကို ထုတ်လိုက်ပါပြီ — {ROLE[g['players'][out]['role']]}"
    await edit_msg(bot, chat, msg.message_id, text)


def roles_text(g):
    return "\n".join(f"{'💀' if not p['alive'] else '✅'} {esc(p['name'])} — {ROLE[p['role']]}"
                     for p in g["players"].values())


async def finish(bot, g, w):
    n = len(g["players"])
    pot = n * ENTRY
    winners = [u for u, p in g["players"].items() if p["alive"] and (p["role"] == "mafia") == (w == "mafia")]
    share = pot // len(winners)
    pay = {u: share for u in winners}
    pay[winners[0]] += pot - share * len(winners)
    ok = db.settle([p["hid"] for p in g["players"].values()], pay, "mafia")
    if not ok:
        raise RuntimeError("mafia settle failed")
    g["settled"] = True
    side = "🔪 Mafia အဖွဲ့" if w == "mafia" else "👨‍🌾 ရွာသားအဖွဲ့"
    names = ", ".join(nm(g, u) for u in winners)
    await bot.send_message(
        g["chat"], f"🏆 <b>{side} နိုင်ပါပြီ!</b>\n{names}\n+{fmt(share)} coin စီ (pot {fmt(pot)})\n\n{roles_text(g)}")


async def run(bot, g):
    chat = g["chat"]
    try:
        n = len(g["players"])
        ids = list(g["players"])
        random.shuffle(ids)
        mn = max(1, n // 4)
        roles = ["mafia"] * mn + ["doctor", "detective"] + ["villager"] * (n - mn - 2)
        for uid, r in zip(ids, roles):
            g["players"][uid]["role"] = r
        mafias = [u for u in ids if g["players"][u]["role"] == "mafia"]
        for uid in ids:
            r = g["players"][uid]["role"]
            t = f"🎭 သင့် role: <b>{ROLE[r]}</b>"
            if r == "mafia" and len(mafias) > 1:
                t += "\nMafia အဖော်: " + ", ".join(nm(g, m) for m in mafias if m != uid)
            await bot.send_message(uid, t)
        await bot.send_message(
            chat, f"🎭 <b>ပွဲစပါပြီ!</b> လူ {n} ယောက် | Pot {fmt(n * ENTRY)}\n"
                  f"Mafia {mn} | Doctor 1 | Detective 1 | ရွာသား {n - mn - 2}\nRole တွေကို DM ပို့ပြီးပါပြီ။")
        for rnd in range(1, MAX_ROUNDS + 1):
            died = await night(bot, g, rnd)
            if died is None:
                await bot.send_message(chat, "☀️ <b>မနက်ပါပြီ</b> — ညက ဘယ်သူမှ မသေပါ။")
            else:
                await bot.send_message(
                    chat, f"☀️ <b>မနက်ပါပြီ</b> — <b>{nm(g, died)}</b> သေဆုံးသွားပါပြီ ({ROLE[g['players'][died]['role']]})")
            w = winner(g)
            if w:
                await finish(bot, g, w)
                return
            await day(bot, g)
            w = winner(g)
            if w:
                await finish(bot, g, w)
                return
        # nobody won in time -> draw, everyone gets the entry back
        db.settle([p["hid"] for p in g["players"].values()],
                  {u: ENTRY for u in g["players"]}, "mafia-draw")
        g["settled"] = True
        await bot.send_message(chat, f"🤝 ပွဲ {MAX_ROUNDS} ပတ်ပြည့်လို့ သရေ — ဝင်ကြေး ပြန်ပေးပါပြီ\n\n{roles_text(g)}")
    except Exception:
        log.exception("mafia")
        if not g.get("settled"):
            refund_holds([p["hid"] for p in g["players"].values()])
            try:
                await bot.send_message(chat, "⚠️ Error ဖြစ်လို့ ပွဲပယ်ဖျက်ပြီး coin အားလုံး ပြန်ပေးပြီးပါပြီ")
            except Exception:
                pass
    finally:
        cleanup(g)


def handlers():
    return [
        CommandHandler("mafia", mafia_cmd),
        CallbackQueryHandler(mf_cb, pattern=r"^mf:"),
        CallbackQueryHandler(mfv_cb, pattern=r"^mfv:"),
    ]
