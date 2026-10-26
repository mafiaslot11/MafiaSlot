"""Premium emoji manager (racing.py / moderation.py ... အားလုံးအတွက် တစ်နေရာတည်း)

Owner ကပဲ သုံးလို့ရတယ်:
  /emojis      Bot ထဲက emoji စာရင်းပြ၊ ပြီးရင် ၁၀ မိနစ်အတွင်း စောင့်နေမယ်
               မကြိုက်တဲ့ emoji ရဲ့ ဘေးမှာ premium emoji ထည့်ပြီး ပို့ (ဥပမာ  🔨⭐ 🔇🌟)
  /setemoji slot... + premium emoji   slot နာမည်နဲ့ တိုက်ရိုက်ထည့်
  /resetemoji slot | all              မူလပြန်ထား

မှတ်ချက်: စာထဲက premium emoji ကို Telegram က bot ကို ခွင့်ပြုထားမှ ပြမယ်၊ မပြရင် ပုံမှန် emoji နဲ့ အလိုအလျောက် ပြန်ပို့တယ်။
"""
import re
import time

from telegram.error import BadRequest
from telegram.ext import CommandHandler, MessageHandler, filters

import db
from common import OWNER_ID

SLOTS = {}          # slot -> မူလ emoji
GROUPS = []         # [(label, [slot, ...])]  /emojis မှာ အုပ်စုလိုက်ပြဖို့
EMOJI = {}          # slot -> custom_emoji_id
TGEMOJI = re.compile(r'<tg-emoji emoji-id="\d+">(.*?)</tg-emoji>')
WAIT = {}           # owner id -> စောင့်နေချိန် (/emojis ပြီးနောက်)
WAIT_SECS = 600

with db.tx() as _c:
    _c.execute("CREATE TABLE IF NOT EXISTS emoji_slots(slot TEXT PRIMARY KEY, emoji_id TEXT)")
    # အရင် racing.py က သိမ်းထားတဲ့ premium emoji တွေကို ပြောင်းယူ
    if _c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='race_emoji'").fetchone():
        _c.execute("INSERT OR IGNORE INTO emoji_slots(slot,emoji_id) SELECT slot,emoji_id FROM race_emoji")
with db._lock:
    for _s, _e in db._c.execute("SELECT slot,emoji_id FROM emoji_slots").fetchall():
        EMOJI[_s] = _e


def register(label, slots):
    """module တစ်ခုက သူသုံးတဲ့ emoji slot တွေကို မှတ်ပုံတင်"""
    SLOTS.update(slots)
    GROUPS.append((label, list(slots)))


def E(slot):
    """premium ထည့်ထားရင် premium၊ မထည့်ရင် ပုံမှန် emoji"""
    eid = EMOJI.get(slot)
    d = SLOTS[slot]
    return f'<tg-emoji emoji-id="{eid}">{d}</tg-emoji>' if eid else d


def plain(text):
    """Telegram က premium emoji ကို လက်မခံရင် ပုံမှန် emoji အဖြစ် ပြန်ပြောင်း"""
    return TGEMOJI.sub(r"\1", text)


def _norm(x):
    return x.replace("\ufe0f", "")


def _u16(text, a, b):
    """Telegram entity offset က UTF-16 unit ဖြစ်လို့ ဒီလိုဖြတ်ရတယ်"""
    return text.encode("utf-16-le")[a * 2:b * 2].decode("utf-16-le", errors="ignore")


def slots_near(text, ent):
    """premium emoji ရဲ့ ဘေးက (အရင် သို့ နောက်) မူလ emoji ကို သုံးတဲ့ slot အားလုံး (module အများကြီးမှာ တူရင် အကုန်ပြောင်း)"""
    by_default = {}
    for s, d in SLOTS.items():
        by_default.setdefault(_norm(d), []).append(s)
    total = len(text.encode("utf-16-le")) // 2
    before = _norm(_u16(text, 0, ent.offset)).rstrip()
    after = _norm(_u16(text, ent.offset + ent.length, total)).lstrip()
    for d, ss in by_default.items():
        if before.endswith(d):
            return ss
    for d, ss in by_default.items():
        if after.startswith(d):
            return ss
    return []


def _save(slot, eid):
    with db.tx() as c:
        c.execute("INSERT OR REPLACE INTO emoji_slots(slot,emoji_id) VALUES(?,?)", (slot, eid))
    EMOJI[slot] = eid


class EmojiReply(filters.MessageFilter):
    """owner က /emojis ပြီးနောက် premium emoji ပါတဲ့ private စာပို့မှ ဖမ်းတယ် (တခြားစာတွေကို မထိဘူး)"""

    def filter(self, message):
        u = message.from_user
        return bool(u and u.id == OWNER_ID and message.chat.type == "private"
                    and WAIT.get(u.id, 0) > time.time()
                    and any(e.type == "custom_emoji" for e in (message.entities or ())))


async def _say(m, text):
    try:
        await m.reply_text(text)
    except BadRequest:
        await m.reply_text(plain(text))


async def emojis_cmd(update, context):
    if update.effective_user.id != OWNER_ID:
        return
    WAIT[update.effective_user.id] = time.time() + WAIT_SECS
    lines = ["🎨 <b>Bot ထဲက emoji များ</b>  (* = premium ထည့်ပြီး)"]
    for label, slots in GROUPS:
        lines.append(f"\n<b>{label}</b>")
        cells = [f"{SLOTS[s]}{'*' if s in EMOJI else ''}" for s in slots]
        for i in range(0, len(cells), 8):
            lines.append(" ".join(cells[i:i + 8]))
    lines.append("\n<b>ပြောင်းနည်း</b>: မကြိုက်တဲ့ emoji ရဲ့ <b>ဘေးမှာ</b> ကိုယ်ထားချင်တဲ့ premium emoji ကိုထည့်ပြီး "
                 "အခုပဲ ပို့လိုက်ပါ။ တစ်ခါတည်း အများကြီးလည်း ရတယ်။\n"
                 "ဥပမာ: <code>🔨⭐ 🔇🌟 🏁👑</code>  (⭐ 🌟 👑 နေရာမှာ premium emoji)\n"
                 "module နှစ်ခုမှာ emoji တူနေရင် (ဥပမာ 👤) နှစ်နေရာလုံး တစ်ပြိုင်တည်း ပြောင်းမယ်။\n\n"
                 "၁၀ မိနစ်အတွင်း စောင့်နေပါမယ်။ မူလပြန်ထားချင်ရင် <code>/resetemoji all</code>")
    await update.message.reply_text("\n".join(lines))


async def emoji_reply(update, context):
    m = update.message
    text = m.text or ""
    done, bad = [], 0
    for e in sorted((e for e in m.entities if e.type == "custom_emoji"), key=lambda e: e.offset):
        ss = slots_near(text, e)
        if not ss:
            bad += 1
            continue
        for s in ss:
            _save(s, e.custom_emoji_id)
        done.append(ss[0])
    WAIT[m.from_user.id] = time.time() + WAIT_SECS
    if not done:
        await m.reply_text("⚠️ premium emoji ရဲ့ ဘေးမှာ ပြောင်းချင်တဲ့ မူလ emoji မတွေ့ပါ၊ /emojis ကိုကြည့်ပြီး ပြန်ပို့ပါ")
        return
    msg = f"✅ ပြောင်းပြီး {len(done)} ခု: " + " ".join(E(s) for s in done)
    if bad:
        msg += f"\n⚠️ {bad} ခုက မူလ emoji မတွေ့လို့ ကျော်ထားတယ်"
    await _say(m, msg)


async def setemoji_cmd(update, context):
    """/setemoji slot1 slot2 ... + premium emoji တွေကို အစီအစဉ်အတိုင်း (သို့) premium emoji ပါတဲ့စာကို reply"""
    if update.effective_user.id != OWNER_ID:
        return
    m = update.message
    slots = [a.lower() for a in context.args if a.lower() in SLOTS]
    ids = []
    for msg in (m, m.reply_to_message):
        if msg:
            ents = sorted((e for e in (msg.entities or ()) if e.type == "custom_emoji"), key=lambda e: e.offset)
            ids += [e.custom_emoji_id for e in ents]
    if not slots:
        await m.reply_text("အသုံးပြုပုံ: <code>/setemoji mod_ban</code> + premium emoji ၁ ခု\n"
                           "အရိုးရှင်းဆုံးကတော့ /emojis ရိုက်ပြီး မူလ emoji ဘေးမှာ premium emoji ထည့်ပို့တာ။\n"
                           "Slot နာမည်များ: " + ", ".join(f"<code>{s}</code>" for s in SLOTS))
        return
    if len(ids) != len(slots):
        await m.reply_text(f"⚠️ slot {len(slots)} ခုအတွက် premium emoji {len(ids)} ခုပဲ တွေ့တယ်၊ အရေအတွက်တူရမယ်")
        return
    for s, eid in zip(slots, ids):
        _save(s, eid)
    await _say(m, f"✅ သိမ်းပြီး: {', '.join(slots)}\n" + " ".join(E(s) for s in slots))


async def resetemoji_cmd(update, context):
    if update.effective_user.id != OWNER_ID:
        return
    args = [a.lower() for a in context.args]
    targets = list(EMOJI) if args == ["all"] else [a for a in args if a in SLOTS]
    if not targets:
        await update.message.reply_text("အသုံးပြုပုံ: <code>/resetemoji mod_ban</code> (သို့) <code>/resetemoji all</code>")
        return
    with db.tx() as c:
        for s in targets:
            c.execute("DELETE FROM emoji_slots WHERE slot=?", (s,))
    for s in targets:
        EMOJI.pop(s, None)
    await update.message.reply_text(f"✅ မူလ emoji ပြန်ထားပြီး: {', '.join(targets)}")


def handlers():
    return [
        CommandHandler("emojis", emojis_cmd),
        CommandHandler("setemoji", setemoji_cmd),
        CommandHandler("resetemoji", resetemoji_cmd),
        MessageHandler(EmojiReply(), emoji_reply),
    ]
