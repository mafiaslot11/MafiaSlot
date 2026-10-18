"""MAFIA MYSTIC fortune logic (no Telegram code here, so it can be unit-tested).

All readings are prewritten (mystic_readings.json.gz). Nothing is generated at runtime
and no AI API is used. Python only SELECTS a reading.

Tables (same SQLite file as the coins, see db.py):
  fx_readings  prewritten readings          fx_compat    prewritten compatibility readings
  fx_questions question list per category   fx_meta      dataset version
  fx_history   which reading each user has been shown (survives restarts)
  fx_period    daily / monthly / yearly result already given to a user
  fx_state     the step a user is currently on (survives restarts)
  fx_profile   saved birth date + zodiac (optional convenience, can be cleared)
"""
import gzip
import hashlib
import json
import os
import random
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import db

CONTENT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mystic_readings.json.gz")
BOT_TZ = os.getenv("BOT_TZ", "Asia/Yangon")

CATEGORIES = ["love_single", "love_relationship", "wealth", "career", "personality", "luck",
              "friendship", "family", "education", "daily", "monthly", "yearly"]
PERIODIC = {"daily", "monthly", "yearly"}
COMPAT = "compat"

SCHEMA = """
CREATE TABLE IF NOT EXISTS fx_readings(
  rid TEXT PRIMARY KEY, category TEXT NOT NULL, zodiac TEXT NOT NULL,
  qid TEXT NOT NULL, n INTEGER NOT NULL, text TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_fx_readings_pool ON fx_readings(category, zodiac, qid);
CREATE TABLE IF NOT EXISTS fx_compat(
  rid TEXT PRIMARY KEY, pair_id TEXT NOT NULL, n INTEGER NOT NULL, text TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_fx_compat_pair ON fx_compat(pair_id);
CREATE TABLE IF NOT EXISTS fx_questions(
  category TEXT NOT NULL, qid TEXT NOT NULL, ord INTEGER NOT NULL, text TEXT NOT NULL,
  PRIMARY KEY(category, qid));
CREATE TABLE IF NOT EXISTS fx_meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS fx_history(
  id INTEGER PRIMARY KEY AUTOINCREMENT, uid INTEGER NOT NULL, category TEXT NOT NULL,
  zodiac TEXT NOT NULL, qid TEXT NOT NULL, rid TEXT NOT NULL,
  cycle INTEGER NOT NULL, ts REAL NOT NULL);
CREATE INDEX IF NOT EXISTS ix_fx_history_combo ON fx_history(uid, category, zodiac, qid);
CREATE TABLE IF NOT EXISTS fx_period(
  uid INTEGER NOT NULL, category TEXT NOT NULL, zodiac TEXT NOT NULL, qid TEXT NOT NULL,
  period TEXT NOT NULL, rid TEXT NOT NULL,
  PRIMARY KEY(uid, category, zodiac, qid, period));
CREATE TABLE IF NOT EXISTS fx_state(uid INTEGER PRIMARY KEY, data TEXT NOT NULL, ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS fx_profile(uid INTEGER PRIMARY KEY, birth TEXT NOT NULL, zodiac TEXT NOT NULL);
"""

_names = {"categories": {}, "zodiac": {}}


def init_schema():
    with db.tx() as c:
        for stmt in SCHEMA.strip().split(";"):
            if stmt.strip():
                c.execute(stmt)


def _file_version(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_content(path=CONTENT_FILE, force=False):
    """Create tables and (re)import the dataset when the file changed.
    Returns the number of readings available. Never raises on a missing file:
    the fortune feature is then simply unavailable (see is_ready())."""
    init_schema()
    if not os.path.exists(path):
        return 0
    ver = _file_version(path)
    with db._lock:
        row = db._c.execute("SELECT v FROM fx_meta WHERE k='version'").fetchone()
        count = db._c.execute("SELECT COUNT(*) FROM fx_readings").fetchone()[0]
    if not force and row and row[0] == ver and count > 0:
        _remember_names()
        return count
    with gzip.open(path, "rt", encoding="utf-8") as f:
        data = json.load(f)
    with db.tx() as c:  # all-or-nothing: a failed import leaves the old content untouched
        c.execute("DELETE FROM fx_readings")
        c.execute("DELETE FROM fx_compat")
        c.execute("DELETE FROM fx_questions")
        c.executemany("INSERT INTO fx_readings(category,zodiac,qid,n,rid,text) VALUES(?,?,?,?,?,?)",
                      data["readings"])
        c.executemany("INSERT INTO fx_compat(pair_id,n,rid,text) VALUES(?,?,?,?)", data["compat"])
        for cat, qs in data["questions"].items():
            c.executemany("INSERT INTO fx_questions(category,qid,ord,text) VALUES(?,?,?,?)",
                          [(cat, q["id"], i, q["text"]) for i, q in enumerate(qs)])
        c.execute("INSERT OR REPLACE INTO fx_meta(k,v) VALUES('version',?)", (ver,))
        c.execute("INSERT OR REPLACE INTO fx_meta(k,v) VALUES('category_names',?)",
                  (json.dumps(data["categories"], ensure_ascii=False),))
        c.execute("INSERT OR REPLACE INTO fx_meta(k,v) VALUES('zodiac_names',?)",
                  (json.dumps(data["zodiac_names"], ensure_ascii=False),))
    _remember_names()
    return len(data["readings"])


def _remember_names():
    with db._lock:
        for k, store in (("category_names", "categories"), ("zodiac_names", "zodiac")):
            r = db._c.execute("SELECT v FROM fx_meta WHERE k=?", (k,)).fetchone()
            if r:
                _names[store] = json.loads(r[0])


def is_ready():
    try:
        with db._lock:
            return db._c.execute("SELECT COUNT(*) FROM fx_readings").fetchone()[0] > 0
    except Exception:  # table missing
        return False


def category_name(cat):
    return _names["categories"].get(cat, cat)


def zodiac_name(z):
    return _names["zodiac"].get(z, z)


def questions(cat):
    """[(qid, text)] in display order. Empty when the category has no content."""
    with db._lock:
        rows = db._c.execute("SELECT qid,text FROM fx_questions WHERE category=? ORDER BY ord", (cat,)).fetchall()
    return [(r["qid"], r["text"]) for r in rows]


def question_text(cat, qid):
    with db._lock:
        r = db._c.execute("SELECT text FROM fx_questions WHERE category=? AND qid=?", (cat, qid)).fetchone()
    return r["text"] if r else None


def pool_size(cat, zodiac, qid):
    with db._lock:
        return db._c.execute("SELECT COUNT(*) FROM fx_readings WHERE category=? AND zodiac=? AND qid=?",
                             (cat, zodiac, qid)).fetchone()[0]


def category_available(cat):
    """A category is offered only when every zodiac has a non-empty pool for every question."""
    qs = questions(cat)
    if not qs:
        return False
    with db._lock:
        n = db._c.execute("SELECT COUNT(DISTINCT zodiac||'/'||qid) FROM fx_readings WHERE category=?",
                          (cat,)).fetchone()[0]
    return n >= 12 * len(qs)


# ---------------------------------------------------------------- selection
def _pick(c, uid, cat, zodiac, qid, pool_sql, pool_args):
    """Pick one reading for this user. MUST run inside db.tx().
    - Only readings of the exact pool are candidates.
    - Readings already shown in the CURRENT cycle are excluded.
    - When the pool is used up a new cycle starts and the least recently shown wins.
    Returns (rid, text) or None if the pool is empty."""
    pool = c.execute(pool_sql, pool_args).fetchall()
    if not pool:
        return None
    texts = {r["rid"]: r["text"] for r in pool}
    cur = c.execute("SELECT COALESCE(MAX(cycle),0) FROM fx_history WHERE uid=? AND category=? AND zodiac=? AND qid=?",
                    (uid, cat, zodiac, qid)).fetchone()[0]
    cycle = max(cur, 1)
    used = {r["rid"] for r in c.execute(
        "SELECT rid FROM fx_history WHERE uid=? AND category=? AND zodiac=? AND qid=? AND cycle=?",
        (uid, cat, zodiac, qid, cycle))}
    unused = [rid for rid in texts if rid not in used]
    last = {r["rid"]: r["t"] for r in c.execute(
        "SELECT rid, MAX(ts) AS t FROM fx_history WHERE uid=? AND category=? AND zodiac=? AND qid=? GROUP BY rid",
        (uid, cat, zodiac, qid))}
    if not unused:  # pool used up -> start a new cycle with the whole pool
        cycle += 1
        unused = list(texts)
    if cycle == 1:
        rid = random.choice(unused)
    else:  # later cycles: least recently shown first
        oldest = min(last.get(r, 0) for r in unused)
        rid = random.choice([r for r in unused if last.get(r, 0) == oldest])
    c.execute("INSERT INTO fx_history(uid,category,zodiac,qid,rid,cycle,ts) VALUES(?,?,?,?,?,?,?)",
              (uid, cat, zodiac, qid, rid, cycle, time.time()))
    return rid, texts[rid]


def next_reading(uid, cat, zodiac, qid):
    """Normal categories: a new reading every call (no repeat until the pool is exhausted)."""
    with db.tx() as c:
        return _pick(c, uid, cat, zodiac, qid,
                     "SELECT rid,text FROM fx_readings WHERE category=? AND zodiac=? AND qid=?",
                     (cat, zodiac, qid))


def next_compat(uid, zodiac_a, zodiac_b):
    pair = pair_id(zodiac_a, zodiac_b)
    with db.tx() as c:
        return _pick(c, uid, COMPAT, pair, "-",
                     "SELECT rid,text FROM fx_compat WHERE pair_id=?", (pair,))


def pair_id(a, b):
    """Unordered pair id as stored in the dataset (animal order rat..pig)."""
    from zodiac import ZODIACS
    x, y = sorted((a, b), key=ZODIACS.index)
    return f"{x}_{y}"


def period_key(cat, now=None):
    now = now or datetime.now(ZoneInfo(BOT_TZ))
    if cat == "daily":
        return now.strftime("%Y-%m-%d")
    if cat == "monthly":
        return now.strftime("%Y-%m")
    return now.strftime("%Y")


def period_reading(uid, cat, zodiac, qid, now=None):
    """Daily / monthly / yearly policy: the SAME question on the SAME day (month / year)
    returns the SAME reading however many times the button is pressed; a new day (month,
    year) gives a new reading using the normal no-repeat history.
    Returns (rid, text, is_new)."""
    key = period_key(cat, now)
    with db.tx() as c:
        r = c.execute("SELECT rid FROM fx_period WHERE uid=? AND category=? AND zodiac=? AND qid=? AND period=?",
                      (uid, cat, zodiac, qid, key)).fetchone()
        if r:
            t = c.execute("SELECT text FROM fx_readings WHERE rid=?", (r["rid"],)).fetchone()
            if t:
                return r["rid"], t["text"], False
        got = _pick(c, uid, cat, zodiac, qid,
                    "SELECT rid,text FROM fx_readings WHERE category=? AND zodiac=? AND qid=?",
                    (cat, zodiac, qid))
        if not got:
            return None
        c.execute("INSERT OR REPLACE INTO fx_period(uid,category,zodiac,qid,period,rid) VALUES(?,?,?,?,?,?)",
                  (uid, cat, zodiac, qid, key, got[0]))
        return got[0], got[1], True


# ---------------------------------------------------------------- user data
def set_state(uid, state):
    with db.tx() as c:
        c.execute("INSERT OR REPLACE INTO fx_state(uid,data,ts) VALUES(?,?,?)",
                  (uid, json.dumps(state, ensure_ascii=False), time.time()))


def get_state(uid):
    with db._lock:
        r = db._c.execute("SELECT data FROM fx_state WHERE uid=?", (uid,)).fetchone()
    return json.loads(r["data"]) if r else None


def clear_state(uid):
    with db.tx() as c:
        c.execute("DELETE FROM fx_state WHERE uid=?", (uid,))


def save_profile(uid, birth_iso, zodiac):
    with db.tx() as c:
        c.execute("INSERT OR REPLACE INTO fx_profile(uid,birth,zodiac) VALUES(?,?,?)", (uid, birth_iso, zodiac))


def get_profile(uid):
    with db._lock:
        r = db._c.execute("SELECT birth,zodiac FROM fx_profile WHERE uid=?", (uid,)).fetchone()
    return (r["birth"], r["zodiac"]) if r else None


def history(uid, limit=10):
    """Latest readings the user received: [(category, zodiac, qid, ts)] - IDs are never shown."""
    with db._lock:
        rows = db._c.execute(
            "SELECT category,zodiac,qid,ts FROM fx_history WHERE uid=? ORDER BY id DESC LIMIT ?",
            (uid, limit)).fetchall()
    return [(r["category"], r["zodiac"], r["qid"], r["ts"]) for r in rows]


def clear_history(uid):
    """Deletes the user's reading history, saved birth date and any pending step."""
    with db.tx() as c:
        for t in ("fx_history", "fx_period", "fx_state", "fx_profile"):
            c.execute(f"DELETE FROM {t} WHERE uid=?", (uid,))
