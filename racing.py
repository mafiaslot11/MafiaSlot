"""PCREW CAR RACING: group racing game for the Game Bot.

Coin ကို bot ရဲ့ db.py (hold / settle / refund_hold) နဲ့ပဲ ကိုင်တယ်၊ ဒါကြောင့်
- လောင်းကြေးဖြတ်တာ၊ ဆုပေးတာ၊ ပြန်အမ်းတာ အကုန် transaction တစ်ခုချင်းစီနဲ့
- bot crash / restart ဖြစ်ရင် main.py ရဲ့ db.refund_holds() က လောင်းကြေးတွေကို ပြန်အမ်းပေးတယ်
- Race ပြီးဆုံးတဲ့ history ကို DB ထဲ သိမ်း (/racecheck)

main.py မှာ: module စာရင်းထဲ racing ကို economy ရှေ့ဆုံးမှာ ထည့်ပြီး racing.schedule(app) ကို ခေါ်ပါ။
"""
import asyncio
import random
import re
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton
from telegram.error import BadRequest, TelegramError
from telegram.ext import CallbackQueryHandler, CommandHandler, filters

import db
from common import btn, clear_timer, edit_msg, ensure, esc, fmt, kb, log, refund_holds, set_timer

MMT = ZoneInfo("Asia/Yangon")

BETS = (10_000, 50_000, 100_000, 500_000)
PAYOUT_MULT = 2            # နိုင်ရင် လောင်းကြေးရဲ့ ၂ ဆ (မူလလောင်းကြေးပါ)
OPEN_SECS = 120            # START မနှိပ်ရင် ပယ်ဖျက်ချိန်
FRAMES = 5                 # animation frame အရေအတွက်
FRAME_DELAY = 2.0          # frame တစ်ခုကြား စက္ကန့် (၅ x ၂ = ၁၀ စက္ကန့်)
TRACK = 10                 # လမ်းကြောင်းအရှည်
GAME = "race"              # holds table ထဲက game နာမည်

_HERE = os.path.dirname(os.path.abspath(__file__))
# assets/ folder ထဲမှာ ဖြစ်ဖြစ်၊ racing.py နဲ့ folder တူတူမှာ ဖြစ်ဖြစ် ရှာတွေ့ရင်သုံး
BET_IMAGE = next((p for p in (os.path.join(_HERE, "assets", "race_bet.jpg"), os.path.join(_HERE, "race_bet.jpg"))
                  if os.path.exists(p)), os.path.join(_HERE, "assets", "race_bet.jpg"))

CARS = [
    ("🚙", "🔵", "BluebLion"),
    ("🚗", "🟢", "GreenDragon"),
    ("🏎️", "🔴", "RedSpeed"),
    ("🚕", "🟡", "YellowFlesh"),
    ("🚘", "⚫", "BlackDiamond"),
]

_rng = random.SystemRandom()          # winner ကို OS randomness နဲ့ ရွေး
RACES = {}                            # chat_id -> race (open / locked)
BY_ID = {}                            # race id -> race
PAYLOAD_RE = r"^/start(@\w+)?\s+race_\d+_[0-4]$"
PAYLOAD = re.compile(r"^race_(\d+)_([0-4])$")

with db.tx() as _c:
    _c.execute("""CREATE TABLE IF NOT EXISTS races(
        id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, message_id INTEGER,
        creator_id INTEGER, state TEXT, winner INTEGER,
        players INTEGER DEFAULT 0, total INTEGER DEFAULT 0,
        created_at INTEGER, ended_at INTEGER)""")


# ------------------------------------------------------------------ helpers
def k(n):
    return f"{n // 1000}k"


def car_label(i):
    ce, co, nm = CARS[i]
    return f"{ce} {co} {nm}"


def green_url(text, url):
    try:
        return InlineKeyboardButton(text, url=url, api_kwargs={"style": "success"})
    except TypeError:
        return InlineKeyboardButton(text, url=url)


def group_text(r):
    per = [0] * len(CARS)
    for b in r["bets"].values():
        per[b["car"]] += 1
    total = sum(b["amt"] for b in r["bets"].values())
    lines = [f"🏁 <b>PCREW CAR RACING</b> · Race #{r['id']}", f"👤 ဖွင့်သူ: {esc(r['cname'])}", "",
             "ကားကို နှိပ်ပြီး Bot private chat မှာ လောင်းကြေးရွေးပါ 👇", ""]
    lines += [f"{car_label(i)} — {per[i]} ယောက်" for i in range(len(CARS))]
    lines += ["", f"👥 လောင်းသူ {len(r['bets'])} ယောက် · 💰 စုစုပေါင်း {fmt(total)} coin", "",
              "⏳ ၂ မိနစ်အတွင်း ဖွင့်သူက START နှိပ်ပါ၊ မနှိပ်ရင် ပယ်ဖျက်ပြီး ပြန်အမ်းပါမယ်"]
    return "\n".join(lines)


def group_kb(r, username):
    rows, row = [], []
    for i, (_, co, nm) in enumerate(CARS):
        row.append(green_url(f"{co} {nm}", f"https://t.me/{username}?start=race_{r['id']}_{i}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([btn("▶️ START", f"rc:s:{r['id']}", "green", emoji=False)])
    return kb(rows)


def panel_caption(rid, car, bal, amt):
    t = (f"🏁 <b>PCREW CAR RACING</b> · Race #{rid}\n"
         f"{car_label(car)} ကို ရွေးထားပါတယ်\n"
         f"💰 လက်ကျန်: {fmt(bal)} coin\n\nလောင်းကြေးရွေးပါ 👇")
    if amt:
        t += f"\n\n✅ ရွေးထားတာ: {fmt(amt)} coin\n🏆 နိုင်ရင် {fmt(amt * PAYOUT_MULT)} ပြန်ရမယ်"
    return t


def panel_kb(rid, car, amt):
    amts = [btn(("✅ " if a == amt else "🪙 ") + k(a), f"rc:a:{rid}:{car}:{a}", "green", emoji=False) for a in BETS]
    rows = [amts[:2], amts[2:]]
    if amt:
        rows.append([btn("✅ Confirm Bet 🏁", f"rc:ok:{rid}:{car}:{amt}", "green", emoji=False)])
    rows.append([btn("❌ Cancel", "rc:x", "red", emoji=False)])
    return kb(rows)


def make_frames(winner):
    """frame တစ်ခုချင်းစီရဲ့ ကားတွေ position၊ နောက်ဆုံး frame မှာ winner ပဲ ပန်းတိုင်ရောက်"""
    finals = [TRACK if i == winner else _rng.randint(TRACK - 6, TRACK - 1) for i in range(len(CARS))]
    prog = [sorted(_rng.random() for _ in range(FRAMES - 1)) for _ in CARS]
    frames = [[max(0, min(TRACK - 1, round(prog[i][f] * finals[i]))) for i in range(len(CARS))]
              for f in range(FRAMES - 1)]
    frames.append(finals)
    return frames


def track_text(rid, pos, note):
    lines = [f"🏁 <b>PCREW CAR RACING</b> · Race #{rid}", note, ""]
    for i, (ce, co, nm) in enumerate(CARS):
        lines.append(f"{co} {nm}")
        lines.append("·" * pos[i] + ce + "·" * (TRACK - pos[i]) + "🏁")
    return "\n".join(lines)


def result_text(r, paid=True):
    w = r["winner"]
    winners = [b for b in r["bets"].values() if b["car"] == w]
    total = sum(b["amt"] for b in r["bets"].values())
    lines = [f"🏆 <b>Race #{r['id']} ပြီးဆုံးပါပြီ!</b>", "", f"🥇 {car_label(w)} နိုင်ပါတယ်!", ""]
    if not paid:
        lines.append("⚠️ ဆုကြေးမပေးနိုင်ပါ၊ လောင်းကြေးတွေ ပြန်အမ်းပြီးသားဖြစ်နိုင်ပါတယ်")
    elif winners:
        lines.append(f"🎉 အနိုင်ရသူ {len(winners)} ယောက်")
        for b in winners[:10]:
            lines.append(f"✅ {esc(b['name'])} — {fmt(b['amt'])} → {fmt(b['amt'] * PAYOUT_MULT)}")
        if len(winners) > 10:
            lines.append(f"... နောက်ထပ် {len(winners) - 10} ယောက်")
    else:
        lines.append("😅 နိုင်တဲ့ကားကို ဘယ်သူမှ မလောင်းထားပါ")
    lines += ["", f"❌ ရှုံးသူ {len(r['bets']) - len(winners)} ယောက်", f"💰 စုစုပေါင်းလောင်းကြေး {fmt(total)}",
              "", "🔁 /race နဲ့ ပွဲသစ်ဖွင့်ပါ"]
    return "\n".join(lines)


_photo_id = None


async def send_panel(bot, chat_id, caption, markup):
    """လောင်းကြေးရွေးတဲ့ private message ကို ပုံနဲ့တွဲပို့ (ပထမတစ်ခါ upload၊ နောက်ဆို file_id)"""
    global _photo_id
    if _photo_id:
        try:
            return await bot.send_photo(chat_id, _photo_id, caption=caption, reply_markup=markup)
        except BadRequest:
            _photo_id = None
    try:
        with open(BET_IMAGE, "rb") as f:
            m = await bot.send_photo(chat_id, f, caption=caption, reply_markup=markup)
        _photo_id = m.photo[-1].file_id
        return m
    except FileNotFoundError:
        log.error("race image not found: %s", BET_IMAGE)
        return await bot.send_message(chat_id, caption, reply_markup=markup)


async def edit_panel(q, text, markup=None):
    try:
        if q.message.photo:
            await q.edit_message_caption(caption=text, reply_markup=markup)
        else:
            await q.edit_message_text(text, reply_markup=markup)
    except BadRequest:
        pass


def finish(r, state):
    """Race ကို memory ကနေဖယ်ပြီး history ကို DB ထဲသိမ်း"""
    RACES.pop(r["chat"], None)
    BY_ID.pop(r["id"], None)
    try:
        with db.tx() as c:
            c.execute("UPDATE races SET state=?, winner=?, players=?, total=?, ended_at=? WHERE id=?",
                      (state, r.get("winner"), len(r["bets"]), sum(b["amt"] for b in r["bets"].values()),
                       int(time.time()), r["id"]))
    except Exception:
        log.exception("race history save failed")


# ------------------------------------------------------------------ group: /race, /racecheck
async def race_cmd(update, context):
    m, chat = update.message, update.effective_chat
    if chat.type == "private":
        await m.reply_text("🏁 /race ကို Group ထဲမှာပဲ သုံးလို့ရပါတယ်")
        return
    u = await ensure(update)
    if chat.id in RACES:
        await m.reply_text("⚠️ ဒီ Group မှာ Race ကစားနေပါတယ်၊ ပြီးမှ ဖွင့်ပါ")
        return
    with db.tx() as c:
        rid = c.execute("INSERT INTO races(chat_id,creator_id,state,created_at) VALUES(?,?,?,?)",
                        (chat.id, u.id, "open", int(time.time()))).lastrowid
    r = {"id": rid, "chat": chat.id, "mid": None, "creator": u.id, "cname": u.full_name,
         "state": "open", "winner": None, "bets": {}}
    RACES[chat.id] = r
    BY_ID[rid] = r
    try:
        msg = await m.reply_text(group_text(r), reply_markup=group_kb(r, context.bot.username))
    except Exception:
        finish(r, "cancelled")
        raise
    r["mid"] = msg.message_id
    with db.tx() as c:
        c.execute("UPDATE races SET message_id=? WHERE id=?", (msg.message_id, rid))
    set_timer(context, f"race{rid}", OPEN_SECS, race_timeout, rid)


async def racecheck_cmd(update, context):
    with db._lock:
        rows = db._c.execute(
            "SELECT id,winner,ended_at FROM races WHERE state='done' ORDER BY id DESC LIMIT 10").fetchall()
    if not rows:
        await update.message.reply_text("🏁 ပြီးဆုံးတဲ့ Race မရှိသေးပါ")
        return
    lines = ["🏁 <b>နောက်ဆုံး Race ၁၀ ခု</b>\n"]
    for rid, w, ts in rows:
        when = datetime.fromtimestamp(ts or 0, MMT).strftime("%d/%m %H:%M")
        lines.append(f"#{rid} · {car_label(w)} · {when}")
    await update.message.reply_text("\n".join(lines))


# ------------------------------------------------------------------ private: bet panel
async def race_start_cmd(update, context):
    """group ထဲက ကားခလုတ်ကနေ ဝင်လာတဲ့ /start race_ID_CAR"""
    u = await ensure(update)
    m = PAYLOAD.match(context.args[0]) if context.args else None
    if not m:
        return
    rid, car = int(m.group(1)), int(m.group(2))
    msg = update.message
    r = BY_ID.get(rid)
    if not r or r["state"] != "open":
        await msg.reply_text("❌ ဒီ Race ပိတ်သွားပါပြီ၊ Group ထဲမှာ /race နဲ့ ပွဲသစ်ဖွင့်ပါ")
        return
    if u.id in r["bets"]:
        await msg.reply_text("✅ ဒီ Race မှာ လောင်းပြီးသားပါ၊ ကားပြောင်းလို့မရပါ")
        return
    bal = db.balance(u.id)
    if bal < BETS[0]:
        await msg.reply_text(f"❌ coin မလုံလောက်ပါ (အနည်းဆုံး {fmt(BETS[0])} လိုတယ်၊ လက်ကျန် {fmt(bal)})")
        return
    await send_panel(context.bot, update.effective_chat.id, panel_caption(rid, car, bal, None),
                     panel_kb(rid, car, None))


async def cb_amount(q, rid, car, amt):
    if amt not in BETS or not 0 <= car < len(CARS):
        await q.answer()
        return
    r = BY_ID.get(rid)
    if not r or r["state"] != "open":
        await q.answer("Race ပိတ်သွားပါပြီ", show_alert=True)
        await edit_panel(q, "❌ ဒီ Race ပိတ်သွားပါပြီ")
        return
    await q.answer()
    await edit_panel(q, panel_caption(rid, car, db.balance(q.from_user.id), amt), panel_kb(rid, car, amt))


async def cb_confirm(q, context, rid, car, amt):
    if amt not in BETS or not 0 <= car < len(CARS):
        await q.answer()
        return
    u = q.from_user
    r = BY_ID.get(rid)
    if not r or r["state"] != "open":
        await q.answer("Race ပိတ်သွားပါပြီ", show_alert=True)
        await edit_panel(q, "❌ ဒီ Race ပိတ်သွားပါပြီ၊ လောင်းကြေး မဖြတ်ပါ")
        return
    if u.id in r["bets"]:
        await q.answer("ဒီ Race မှာ လောင်းပြီးသားပါ", show_alert=True)
        await edit_panel(q, "✅ ဒီ Race မှာ လောင်းပြီးသားပါ")
        return
    hid = db.hold(u.id, amt, GAME)          # coin ဖြတ်ပြီး hold ထဲထည့် (atomic)
    if hid is None:
        await q.answer("❌ coin မလုံလောက်ပါ", show_alert=True)
        return
    r["bets"][u.id] = {"name": u.full_name, "car": car, "amt": amt, "hid": hid}
    await q.answer("✅ လောင်းပြီးပါပြီ!")
    await edit_panel(q, (f"✅ <b>လောင်းကြေး တင်ပြီးပါပြီ!</b>\n\n🏁 Race #{rid}\n{car_label(car)}\n"
                         f"💰 လောင်းကြေး: {fmt(amt)} coin\n🏆 နိုင်ရင်: {fmt(amt * PAYOUT_MULT)} coin\n"
                         f"💳 လက်ကျန်: {fmt(db.balance(u.id))} coin\n\nGroup ထဲမှာ ရလဒ်ကို စောင့်ကြည့်ပါ 🍀"))
    if not context.job_queue.get_jobs_by_name(f"rrf{rid}"):      # group message ကို ၁.၅ စက္ကန့်စုပြီး တစ်ခါပဲ edit
        context.job_queue.run_once(refresh_job, 1.5, name=f"rrf{rid}", data=rid)


async def cb_cancel(q):
    await q.answer("ပယ်ဖျက်ပါပြီ")
    try:
        await q.message.delete()
    except TelegramError:
        pass


async def cb_start(q, context, rid):
    r = BY_ID.get(rid)
    if not r or r["state"] != "open":
        await q.answer("ဒီ Race ပိတ်သွားပါပြီ", show_alert=True)
        return
    if q.from_user.id != r["creator"]:
        await q.answer("Race ဖွင့်သူပဲ START နှိပ်လို့ရပါတယ်", show_alert=True)
        return
    if not r["bets"]:
        await q.answer("လောင်းသူ မရှိသေးပါ၊ အနည်းဆုံး ၁ ယောက်လိုပါတယ်", show_alert=True)
        return
    r["state"] = "locked"                                   # ဒီနောက် bet မလက်ခံတော့ဘူး၊ START နှစ်ခါလည်း မရတော့ဘူး
    r["winner"] = _rng.randrange(len(CARS))                 # winner ကို ဒီမှာ တစ်ကြိမ်တည်းရွေး
    clear_timer(context, f"race{rid}")
    with db.tx() as c:
        c.execute("UPDATE races SET state='locked', winner=? WHERE id=?", (r["winner"], rid))
    await q.answer("🏁 Race စပါပြီ!")
    context.application.create_task(run_race(context.bot, r))


async def cb_router(update, context):
    q = update.callback_query
    d = q.data.split(":")
    try:
        if d[1] == "s":
            await cb_start(q, context, int(d[2]))
        elif d[1] == "a":
            await cb_amount(q, int(d[2]), int(d[3]), int(d[4]))
        elif d[1] == "ok":
            await cb_confirm(q, context, int(d[2]), int(d[3]), int(d[4]))
        elif d[1] == "x":
            await cb_cancel(q)
        else:
            await q.answer()
    except Exception:
        log.exception("race callback failed")


# ------------------------------------------------------------------ race lifecycle
async def run_race(bot, r):
    rid, chat, mid, winner = r["id"], r["chat"], r["mid"], r["winner"]
    try:
        await edit_msg(bot, chat, mid, f"🏁 <b>PCREW CAR RACING</b> · Race #{rid}\n\n🔒 လောင်းကြေးပိတ်ပါပြီ\n🚦 ပြင်ဆင်ပါ... ၃ ၂ ၁ ...")
        await asyncio.sleep(2)
        for frame in make_frames(winner):
            await edit_msg(bot, chat, mid, track_text(rid, frame, "🔥 ပြိုင်ပွဲ ဖြစ်နေပါပြီ!"))
            await asyncio.sleep(FRAME_DELAY)
    except Exception:
        log.exception("race animation failed")
    # animation ပျက်ပျက် ဆုကြေးကို ဒီမှာ တစ်ကြိမ်တည်းပေး (hold တွေကို ပိတ် + နိုင်သူတွေကို ပေး = transaction တစ်ခု)
    hids = [b["hid"] for b in r["bets"].values()]
    payouts = {uid: b["amt"] * PAYOUT_MULT for uid, b in r["bets"].items() if b["car"] == winner}
    paid = False
    try:
        paid = db.settle(hids, payouts, GAME)
    except Exception:
        log.exception("race settle failed")
    finish(r, "done" if paid else "cancelled")
    await edit_msg(bot, chat, mid, result_text(r, paid))


async def race_timeout(context):
    rid = context.job.data
    r = BY_ID.get(rid)
    if not r or r["state"] != "open":
        return
    r["state"] = "cancelled"
    refund_holds([b["hid"] for b in r["bets"].values()])
    n = len(r["bets"])
    finish(r, "cancelled")
    await edit_msg(context.bot, r["chat"], r["mid"],
                   f"⏱ Race #{rid} ကို ၂ မိနစ်အတွင်း START မနှိပ်လို့ ပယ်ဖျက်လိုက်ပါပြီ\n"
                   f"💸 လောင်းကြေး {n} ခုကို အကုန်ပြန်အမ်းပြီးပါပြီ\n\n🔁 /race နဲ့ ပွဲသစ်ဖွင့်ပါ")


async def refresh_job(context):
    r = BY_ID.get(context.job.data)
    if r and r["state"] == "open" and r["mid"]:
        await edit_msg(context.bot, r["chat"], r["mid"], group_text(r), group_kb(r, context.bot.username))


async def recover_job(context):
    """Restart ပြီးနောက် ကျန်နေတဲ့ Race မှတ်တမ်းတွေကို ရှင်း (လောင်းကြေးကို main.py က ပြန်အမ်းပြီးသား)"""
    with db._lock:
        rows = db._c.execute("SELECT id,chat_id,message_id FROM races WHERE state IN ('open','locked')").fetchall()
    for rid, chat, mid in rows:
        if rid in BY_ID:
            continue
        with db.tx() as c:
            c.execute("UPDATE races SET state='cancelled', ended_at=? WHERE id=?", (int(time.time()), rid))
        if mid:
            await edit_msg(context.bot, chat, mid,
                           f"⚠️ Bot restart ဖြစ်လို့ Race #{rid} ကို ပယ်ဖျက်ပြီး လောင်းကြေးအားလုံး ပြန်အမ်းပြီးပါပြီ")


def schedule(app):
    app.job_queue.run_once(recover_job, 8)


def handlers():
    return [
        CommandHandler("start", race_start_cmd, filters=filters.ChatType.PRIVATE & filters.Regex(PAYLOAD_RE)),
        CommandHandler("race", race_cmd),
        CommandHandler("racecheck", racecheck_cmd),
        CallbackQueryHandler(cb_router, pattern=r"^rc:"),
    ]
