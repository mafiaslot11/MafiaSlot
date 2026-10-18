"""MAFIA MYSTIC - Telegram interface (all text in Burmese).

Flow:  /start -> [🔮 ဗေဒင်မေးရန်] -> category -> question -> birth date -> [Start] -> reading
The reading is a prewritten text chosen by fortune_core (no AI). Every step is stored in
SQLite (fx_state) so a bot restart never breaks a conversation."""
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram.error import BadRequest
from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler, filters

import fortune_core as fx
import zodiac as zd
from common import btn, esc, kb, log

TZ = ZoneInfo(fx.BOT_TZ)
CATS = fx.CATEGORIES
LABEL = {
    "love_single": "💖 အချစ်ရေး (လူလွတ်)", "love_relationship": "💑 အချစ်ရေး (ချစ်သူရှိ)",
    "wealth": "💰 ငွေကြေး", "career": "💼 အလုပ်အကိုင်", "personality": "🧠 ကိုယ်ရည်ကိုယ်သွေး",
    "luck": "🍀 ကံကြမ္မာ", "friendship": "🤝 မိတ်ဆွေ", "family": "👨‍👩‍👧 မိသားစု",
    "education": "📚 ပညာရေး", "daily": "🌞 နေ့စဉ်", "monthly": "🌙 လစဉ်", "yearly": "📅 နှစ်စဉ်",
}
HEADER = {  # title line inside a reading
    "love_single": "❤️ အချစ်ရေး", "love_relationship": "❤️ အချစ်ရေး", "wealth": "💰 ငွေကြေး",
    "career": "💼 အလုပ်အကိုင်", "personality": "🧠 ကိုယ်ရည်ကိုယ်သွေး", "luck": "🍀 ကံကြမ္မာ",
    "friendship": "🤝 မိတ်ဆွေဆက်ဆံရေး", "family": "👨‍👩‍👧 မိသားစု", "education": "📚 ပညာရေး",
    "daily": "🌞 နေ့စဉ်ကံကြမ္မာ", "monthly": "🌙 လစဉ်ကံကြမ္မာ", "yearly": "📅 နှစ်စဉ်ကံကြမ္မာ",
}
LINE = "━━━━━━━━━━━━━━"
DISCLAIMER = "<i>ရိုးရာအဓိပ္ပာယ်ဖွင့်ဆိုချက်ဖြစ်ပြီး ဖျော်ဖြေရေးအတွက်သာ ဖြစ်ပါတယ်။</i>"
PERIOD_NOTE = "<i>ဒီကာလအတွက် ရလဒ်တစ်ခုတည်းကိုသာ ပြပါတယ်။</i>"
STALE = "ဒီခလုတ်က သက်တမ်းကုန်သွားပါပြီ၊ /start ကနေ ပြန်စပါ။"


def zlabel(z):
    return f"{zd.ZODIAC_EMOJI.get(z, '')} {fx.zodiac_name(z)}"


def fmt_date(iso):
    y, m, d = iso.split("-")
    return f"{d}-{m}-{y}"


# ------------------------------------------------------------------ screen helpers
async def show(update, context, text, markup=None, new=False):
    """Edit the current message (or send a new one). Never raises."""
    q = update.callback_query
    if q and not new and not q.message.photo:
        try:
            await q.edit_message_text(text, reply_markup=markup)
            return
        except BadRequest as e:
            if "not modified" in str(e).lower():
                return
            log.warning("fortune edit failed: %s", e)
        except Exception:
            log.exception("fortune edit failed")
    try:
        await context.bot.send_message(update.effective_chat.id, text, reply_markup=markup)
    except Exception:
        log.exception("fortune send failed")


def main_menu():
    avail = [c for c in CATS if fx.category_available(c)]
    rows, row = [], []
    for c in avail:
        row.append(btn(LABEL[c], f"fx:c:{CATS.index(c)}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([btn("💞 လိုက်ဖက်မှု", "fx:cp"), btn("📜 မှတ်တမ်း", "fx:h")])
    rows.append([btn("❌ ပိတ်မယ်", "fx:close", "red", emoji=False)])
    text = "🔮 <b>MAFIA MYSTIC</b>\nChinese Zodiac ဗေဒင်\n\nမေးချင်တဲ့ အမျိုးအစားကို ရွေးပါ 👇"
    return text, kb(rows)


def question_menu(ci):
    cat = CATS[ci]
    qs = fx.questions(cat)
    lines = [f"{i}. {esc(t)}" for i, (_, t) in enumerate(qs, 1)]
    text = f"{LABEL[cat]}\n\nမေးချင်တဲ့ မေးခွန်းကို ရွေးပါ 👇\n\n" + "\n\n".join(lines)
    nums = [btn(str(i), f"fx:q:{ci}:{i - 1}") for i in range(1, len(qs) + 1)]
    rows = [nums[i:i + 5] for i in range(0, len(nums), 5)]
    rows.append([btn("🔙 နောက်သို့", "fx:bm"), btn("❌ ပယ်ဖျက်မယ်", "fx:x", "red", emoji=False)])
    return text, kb(rows)


def dob_prompt(uid, intro, who_saved=True):
    text = (f"{intro}\n\n📅 <b>မွေးနေ့ ထည့်ပါ</b>\nဥပမာ: <code>15-08-2000</code>\n"
            f"(ရက်-လ-ခုနှစ်၊ ၁၉၀၀ ကနေ ၂၀၃၅ အတွင်း)")
    rows = []
    prof = fx.get_profile(uid) if who_saved else None
    if prof:
        rows.append([btn(f"📅 {fmt_date(prof[0])} ကို သုံးမယ်", "fx:u")])
    rows.append([btn("🔙 နောက်သို့", "fx:bq"), btn("❌ ပယ်ဖျက်မယ်", "fx:x", "red", emoji=False)])
    return text, kb(rows)


def confirm_view(state):
    text = (f"{zd.ZODIAC_EMOJI[state['zodiac']]} သင့်ရဲ့ Chinese Zodiac — <b>{esc(fx.zodiac_name(state['zodiac']))}</b>\n"
            f"📅 မွေးနေ့: {fmt_date(state['dob'])}\n\nစတင်ဖတ်ရှုရန် <b>Start</b> ကို နှိပ်ပါ 👇")
    rows = [[btn("Start", "fx:go", "green")],
            [btn("✏️ မွေးနေ့ပြင်မယ်", "fx:ed"), btn("❌ ပယ်ဖျက်မယ်", "fx:x", "red", emoji=False)]]
    return text, kb(rows)


def reading_view(state, text_body, periodic):
    cat = state["cat"]
    qtext = fx.question_text(cat, state["qid"]) or ""
    text = (f"🔮 <b>MAFIA MYSTIC</b>\n\n"
            f"{zd.ZODIAC_EMOJI[state['zodiac']]} သင့်ရဲ့ Chinese Zodiac — <b>{esc(fx.zodiac_name(state['zodiac']))}</b>\n\n"
            f"{HEADER[cat]}\n\n❓ <b>မေးခွန်း</b> — {esc(qtext)}\n\n{LINE}\n\n{esc(text_body)}\n\n{LINE}\n"
            f"{PERIOD_NOTE if periodic else DISCLAIMER}")
    rows = []
    if not periodic:
        rows.append([btn("🔄 နောက်တစ်ကြိမ်ဖတ်မယ်", f"fx:again:{state['seq']}", "blue", emoji=False)])
    rows.append([btn("🔙 နောက်သို့", "fx:bq"), btn("❌ ပယ်ဖျက်မယ်", "fx:x", "red", emoji=False)])
    return text, kb(rows)


def compat_view(state, text_body):
    text = (f"🔮 <b>MAFIA MYSTIC</b>\n\n💞 <b>လိုက်ဖက်မှု ဗေဒင်</b>\n\n"
            f"{zlabel(state['z1'])}  ×  {zlabel(state['z2'])}\n\n{LINE}\n\n{esc(text_body)}\n\n{LINE}\n{DISCLAIMER}")
    rows = [[btn("🔄 နောက်တစ်ကြိမ်ဖတ်မယ်", f"fx:again:{state['seq']}", "blue", emoji=False)],
            [btn("🔙 နောက်သို့", "fx:bm"), btn("❌ ပယ်ဖျက်မယ်", "fx:x", "red", emoji=False)]]
    return text, kb(rows)


# ------------------------------------------------------------------ entry points
async def open_menu(update, context):
    """Send the main fortune menu as a NEW message (private chat only)."""
    chat = update.effective_chat
    if chat.type != "private":
        await context.bot.send_message(
            chat.id, f"🔮 ဗေဒင်ကို private chat မှာပဲ မေးလို့ရပါတယ်။ @{context.bot.username} ကို ဖွင့်ပြီး /start နှိပ်ပါ။")
        return
    if not fx.is_ready():
        await context.bot.send_message(chat.id, "🔮 ဗေဒင်ကို ခဏပြင်ဆင်နေပါတယ်၊ နောက်မှ ပြန်လာခဲ့ပါ။")
        return
    fx.clear_state(update.effective_user.id)
    text, markup = main_menu()
    await context.bot.send_message(chat.id, text, reply_markup=markup)


async def fortune_cmd(update, context):
    await open_menu(update, context)


# ------------------------------------------------------------------ callbacks
async def fx_cb(update, context):
    q = update.callback_query
    try:
        await _fx_cb(update, context, q)
    except Exception:
        log.exception("fortune callback failed")
        try:
            await q.answer("⚠️ ခဏနေမှ ပြန်စမ်းပါ", show_alert=True)
        except Exception:
            pass


async def _fx_cb(update, context, q):
    uid = q.from_user.id
    parts = q.data.split(":")
    act = parts[1] if len(parts) > 1 else ""

    if q.message.chat.type != "private":  # fortune is private only
        try:
            await q.answer(url=f"https://t.me/{context.bot.username}?start=fx")
        except Exception:
            await q.answer("🔮 ဗေဒင်ကို Bot ရဲ့ private chat မှာပဲ မေးလို့ရပါတယ်", show_alert=True)
        return
    if not fx.is_ready():
        await q.answer("🔮 ဗေဒင်ကို ခဏပြင်ဆင်နေပါတယ်", show_alert=True)
        return

    if act == "m":  # from /start -> brand new message
        await q.answer()
        fx.clear_state(uid)
        text, markup = main_menu()
        await show(update, context, text, markup, new=True)
    elif act == "bm":  # back to main menu
        await q.answer()
        fx.clear_state(uid)
        text, markup = main_menu()
        await show(update, context, text, markup)
    elif act == "close":
        await q.answer()
        fx.clear_state(uid)
        try:
            await q.message.delete()
        except Exception:
            await show(update, context, "🔮 ပိတ်လိုက်ပါပြီ။ /start နဲ့ ပြန်စနိုင်ပါတယ်။")
    elif act == "x":  # cancel: clear the active step
        await q.answer("ပယ်ဖျက်ပြီး")
        fx.clear_state(uid)
        await show(update, context, "❌ ပယ်ဖျက်ပြီးပါပြီ။", kb([[btn("🔮 ဗေဒင်မီနူး", "fx:bm")]]))
    elif act == "c":
        ci = int(parts[2])
        if not (0 <= ci < len(CATS)) or not fx.category_available(CATS[ci]):
            await q.answer(STALE, show_alert=True)
            return
        await q.answer()
        fx.set_state(uid, {"mode": "pick", "ci": ci, "cat": CATS[ci]})
        text, markup = question_menu(ci)
        await show(update, context, text, markup)
    elif act == "bq":  # back to the question list of the current category
        st = fx.get_state(uid)
        if not st or "ci" not in st:
            await q.answer()
            text, markup = main_menu()
            await show(update, context, text, markup)
            return
        await q.answer()
        fx.set_state(uid, {"mode": "pick", "ci": st["ci"], "cat": st["cat"]})
        text, markup = question_menu(st["ci"])
        await show(update, context, text, markup)
    elif act == "q":
        ci, qi = int(parts[2]), int(parts[3])
        qs = fx.questions(CATS[ci]) if 0 <= ci < len(CATS) else []
        if not (0 <= qi < len(qs)):
            await q.answer(STALE, show_alert=True)
            return
        await q.answer()
        state = {"mode": "dob", "ci": ci, "cat": CATS[ci], "qid": qs[qi][0], "seq": 0}
        fx.set_state(uid, state)
        text, markup = dob_prompt(uid, f"❓ <b>{esc(qs[qi][1])}</b>")
        await show(update, context, text, markup)
    elif act in ("u", "ed"):  # use saved birth date / edit birth date
        st = fx.get_state(uid)
        if not st or st.get("mode") not in ("dob", "confirm", "c1", "c2"):
            await q.answer(STALE, show_alert=True)
            return
        await q.answer()
        if act == "ed":
            st["mode"] = "dob" if st["mode"] == "confirm" else st["mode"]
            fx.set_state(uid, st)
            text, markup = dob_prompt(uid, "")
            await show(update, context, text, markup)
            return
        prof = fx.get_profile(uid)
        if not prof:
            await q.answer(STALE, show_alert=True)
            return
        await _accept_birth(update, context, uid, st, prof[0], via_callback=True)
    elif act == "go":
        st = fx.get_state(uid)
        if not st:
            await q.answer(STALE, show_alert=True)
            return
        if st.get("mode") == "confirm":
            await _deliver_reading(update, context, q, uid, st)
        elif st.get("mode") == "cconfirm":
            await _deliver_compat(update, context, q, uid, st)
        else:
            await q.answer(STALE, show_alert=True)
    elif act == "again":
        st = fx.get_state(uid)
        if not st or st.get("mode") not in ("result", "cresult"):
            await q.answer(STALE, show_alert=True)
            return
        if int(parts[2]) != st.get("seq"):  # an old / double-tapped button: do nothing
            await q.answer("✅")
            return
        if st["mode"] == "result":
            await _deliver_reading(update, context, q, uid, st)
        else:
            await _deliver_compat(update, context, q, uid, st)
    elif act == "cp":  # compatibility start
        await q.answer()
        fx.set_state(uid, {"mode": "c1", "seq": 0})
        text, markup = _compat_prompt(uid, 1)
        await show(update, context, text, markup)
    elif act == "h":
        await q.answer()
        await _show_history(update, context, uid)
    elif act == "hc":
        await q.answer()
        await show(update, context, "🗑 မှတ်တမ်းနဲ့ သိမ်းထားတဲ့ မွေးနေ့ကို ဖျက်မှာ သေချာပါသလား?",
                   kb([[btn("✅ ဖျက်မယ်", "fx:hcy", "red", emoji=False), btn("🔙 မဖျက်ဘူး", "fx:h")]]))
    elif act == "hcy":
        await q.answer("ဖျက်ပြီးပါပြီ")
        fx.clear_history(uid)
        await show(update, context, "🗑 မှတ်တမ်းကို ဖျက်ပြီးပါပြီ။", kb([[btn("🔮 ဗေဒင်မီနူး", "fx:bm")]]))
    else:
        await q.answer(STALE, show_alert=True)


def _compat_prompt(uid, which):
    who = "ပထမလူ" if which == 1 else "ဒုတိယလူ"
    text = (f"💞 <b>လိုက်ဖက်မှု ဗေဒင်</b>\n\n👤 <b>{who}ရဲ့ မွေးနေ့</b> ကို ထည့်ပါ\n"
            f"ဥပမာ: <code>15-08-2000</code>")
    rows = []
    prof = fx.get_profile(uid) if which == 1 else None
    if prof:
        rows.append([btn(f"📅 ကျွန်ုပ်မွေးနေ့ {fmt_date(prof[0])} ကို သုံးမယ်", "fx:u")])
    rows.append([btn("🔙 နောက်သို့", "fx:bm"), btn("❌ ပယ်ဖျက်မယ်", "fx:x", "red", emoji=False)])
    return text, kb(rows)


async def _show_history(update, context, uid):
    rows = fx.history(uid, 10)
    if not rows:
        text = "📜 <b>မှတ်တမ်း</b>\n\nဖတ်ပြီးသား ဗေဒင် မရှိသေးပါ။"
    else:
        lines = []
        for cat, z, qid, ts in rows:
            when = datetime.fromtimestamp(ts, TZ).strftime("%d/%m %H:%M")
            if cat == fx.COMPAT:
                a, b = z.split("_")
                lines.append(f"💞 {zlabel(a)} × {zlabel(b)}  <i>({when})</i>")
            else:
                qt = fx.question_text(cat, qid) or ""
                lines.append(f"{LABEL.get(cat, cat)} · {zlabel(z)}\n   ❓ {esc(qt)}  <i>({when})</i>")
        text = "📜 <b>မှတ်တမ်း</b> (နောက်ဆုံး ၁၀ ခု)\n\n" + "\n\n".join(lines)
    markup = kb([[btn("🗑 မှတ်တမ်းဖျက်မယ်", "fx:hc"), btn("🔙 နောက်သို့", "fx:bm")]])
    await show(update, context, text, markup)


# ------------------------------------------------------------------ readings
async def _deliver_reading(update, context, q, uid, st):
    cat, z, qid = st["cat"], st["zodiac"], st["qid"]
    periodic = cat in fx.PERIODIC
    got = fx.period_reading(uid, cat, z, qid) if periodic else fx.next_reading(uid, cat, z, qid)
    if not got:
        await q.answer("ဒီအပိုင်းကို မကြာခင် ထည့်ပါမယ်", show_alert=True)
        return
    await q.answer()
    st["mode"] = "result"
    st["seq"] = st.get("seq", 0) + 1
    fx.set_state(uid, st)
    text, markup = reading_view(st, got[1], periodic)
    await show(update, context, text, markup)


async def _deliver_compat(update, context, q, uid, st):
    got = fx.next_compat(uid, st["z1"], st["z2"])
    if not got:
        await q.answer("ဒီအပိုင်းကို မကြာခင် ထည့်ပါမယ်", show_alert=True)
        return
    await q.answer()
    st["mode"] = "cresult"
    st["seq"] = st.get("seq", 0) + 1
    fx.set_state(uid, st)
    text, markup = compat_view(st, got[1])
    await show(update, context, text, markup)


# ------------------------------------------------------------------ birth date input
ERR = {
    "format": "⚠️ ပုံစံမှားနေပါတယ်။ <code>ရက်-လ-ခုနှစ်</code> လို့ ရိုက်ပါ၊ ဥပမာ <code>15-08-2000</code>",
    "date": "⚠️ အဲ့ရက်စွဲ မရှိပါဘူး။ ရက်၊ လ ကို ပြန်စစ်ပြီး ထပ်ရိုက်ပါ။",
    "range": "⚠️ ၁၉၀၀ ကနေ ၂၀၃၅ ခုနှစ်အတွင်း မွေးနေ့ကိုပဲ လက်ခံပါတယ်။",
    "future": "⚠️ မွေးနေ့က အနာဂတ်ဖြစ်နေပါတယ်။ ပြန်စစ်ပါ။",
}


async def on_text(update, context):
    """Private-chat text: only used while the user is on a birth-date step."""
    m = update.message
    uid = update.effective_user.id
    st = fx.get_state(uid)
    if not st or st.get("mode") not in ("dob", "c1", "c2"):
        return  # not in a fortune step: ignore chatter
    try:
        d = zd.parse_birthdate(m.text, today=datetime.now(TZ).date())
    except ValueError as e:
        await m.reply_text(ERR.get(str(e), ERR["format"]))
        return
    await _accept_birth(update, context, uid, st, d.isoformat(), via_callback=False)


async def _accept_birth(update, context, uid, st, iso, via_callback):
    from datetime import date
    d = date.fromisoformat(iso)
    z = zd.zodiac_for(d)  # raises ValueError if unsupported; parse already checked the range
    mode = st["mode"]
    if mode == "dob":
        st.update(mode="confirm", dob=iso, zodiac=z)
        fx.save_profile(uid, iso, z)
        fx.set_state(uid, st)
        text, markup = confirm_view(st)
    elif mode == "c1":
        st.update(mode="c2", dob1=iso, z1=z)
        fx.set_state(uid, st)
        text, markup = _compat_prompt(uid, 2)
        text = f"✅ ပထမလူ: {zlabel(z)}\n\n" + text
    elif mode == "c2":
        st.update(mode="cconfirm", dob2=iso, z2=z)
        fx.set_state(uid, st)
        text = (f"💞 <b>လိုက်ဖက်မှု ဗေဒင်</b>\n\n👤 ပထမလူ: {zlabel(st['z1'])}\n👤 ဒုတိယလူ: {zlabel(st['z2'])}\n\n"
                f"စတင်ဖတ်ရှုရန် <b>Start</b> ကို နှိပ်ပါ 👇")
        markup = kb([[btn("Start", "fx:go", "green")],
                     [btn("🔙 နောက်သို့", "fx:bm"), btn("❌ ပယ်ဖျက်မယ်", "fx:x", "red", emoji=False)]])
    else:
        return
    if via_callback:
        await show(update, context, text, markup)
    else:
        await update.message.reply_text(text, reply_markup=markup)


def handlers():
    return [
        CommandHandler("bedin", fortune_cmd),
        CommandHandler("fortune", fortune_cmd),
        CallbackQueryHandler(fx_cb, pattern=r"^fx:"),
        MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, on_text),
    ]
