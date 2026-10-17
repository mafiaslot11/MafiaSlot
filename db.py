"""SQLite coin database.

Safety rules:
- Every coin change happens inside ONE transaction (all or nothing).
- When a game starts, the bet is moved into the `holds` table ("held").
  If the bot crashes/restarts, every leftover hold is refunded at startup.
- A hold can only be settled once, so coins can never be paid twice.
"""
import os
import random
import sqlite3
import threading
import time
from contextlib import contextmanager

DB_PATH = os.getenv("DB_PATH", "bot.db")
WELCOME = 10000


class Abort(Exception):
    pass


_d = os.path.dirname(DB_PATH)
if _d:
    os.makedirs(_d, exist_ok=True)

_lock = threading.RLock()
_c = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
_c.row_factory = sqlite3.Row
_c.execute("PRAGMA journal_mode=WAL")
_c.execute("PRAGMA synchronous=FULL")
_c.executescript(
    """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, name TEXT, username TEXT,
  coin INTEGER NOT NULL DEFAULT 0 CHECK(coin>=0));
CREATE TABLE IF NOT EXISTS log(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, uid INTEGER,
  delta INTEGER, reason TEXT);
CREATE TABLE IF NOT EXISTS holds(
  id INTEGER PRIMARY KEY AUTOINCREMENT, uid INTEGER, amount INTEGER,
  game TEXT, ts INTEGER);
CREATE TABLE IF NOT EXISTS bets(
  id INTEGER PRIMARY KEY AUTOINCREMENT, draw TEXT, uid INTEGER, chat INTEGER,
  number INTEGER, amount INTEGER, settled INTEGER DEFAULT 0,
  UNIQUE(draw, uid));
CREATE TABLE IF NOT EXISTS draws(draw TEXT PRIMARY KEY, number INTEGER);
CREATE TABLE IF NOT EXISTS claims(uid INTEGER, kind TEXT, ts INTEGER, PRIMARY KEY(uid, kind));
"""
)


@contextmanager
def tx():
    with _lock:
        _c.execute("BEGIN IMMEDIATE")
        try:
            yield _c
            _c.execute("COMMIT")
        except BaseException:
            _c.execute("ROLLBACK")
            raise


def _log(c, uid, delta, reason):
    c.execute(
        "INSERT INTO log(ts,uid,delta,reason) VALUES(?,?,?,?)",
        (int(time.time()), uid, delta, reason),
    )


# ---------- users ----------
def ensure_user(uid, name, username):
    """Create user (with welcome bonus) if new. Returns True if created."""
    with tx() as c:
        if c.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone():
            c.execute("UPDATE users SET name=?, username=? WHERE id=?", (name, username, uid))
            return False
        c.execute(
            "INSERT INTO users(id,name,username,coin) VALUES(?,?,?,?)",
            (uid, name, username, WELCOME),
        )
        _log(c, uid, WELCOME, "welcome")
        return True


def user_row(uid):
    with _lock:
        return _c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def balance(uid):
    r = user_row(uid)
    return r["coin"] if r else 0


def find_user(key):
    key = key.strip()
    with _lock:
        if key.startswith("@"):
            return _c.execute(
                "SELECT * FROM users WHERE lower(username)=?", (key[1:].lower(),)
            ).fetchone()
        if key.lstrip("-").isdigit():
            return _c.execute("SELECT * FROM users WHERE id=?", (int(key),)).fetchone()
    return None


def top(limit, exclude_id):
    with _lock:
        return _c.execute(
            "SELECT id,name,username,coin FROM users WHERE id!=? ORDER BY coin DESC LIMIT ?",
            (exclude_id, limit),
        ).fetchall()


# ---------- owner (unlimited coin creation) ----------
def owner_adjust(uid, delta=None, to=None, reason="owner"):
    """Add/remove (delta) or set (to). Never goes below 0. Returns new balance."""
    with tx() as c:
        r = c.execute("SELECT coin FROM users WHERE id=?", (uid,)).fetchone()
        if not r:
            return None
        new = max(0, to if to is not None else r["coin"] + delta)
        c.execute("UPDATE users SET coin=? WHERE id=?", (new, uid))
        _log(c, uid, new - r["coin"], reason)
        return new


def gift_all(amount):
    with tx() as c:
        ids = [r["id"] for r in c.execute("SELECT id FROM users")]
        c.execute("UPDATE users SET coin=coin+?", (amount,))
        for i in ids:
            _log(c, i, amount, "owner gift all")
        return len(ids)


# ---------- player to player ----------
def transfer(a, b, amount):
    with tx() as c:
        cur = c.execute(
            "UPDATE users SET coin=coin-? WHERE id=? AND coin>=?", (amount, a, amount)
        )
        if cur.rowcount == 0:
            return False
        c.execute("UPDATE users SET coin=coin+? WHERE id=?", (amount, b))
        _log(c, a, -amount, f"give to {b}")
        _log(c, b, amount, f"give from {a}")
        return True


# ---------- holds (bets in play) ----------
def hold(uid, amount, game):
    """Take bet out of the balance into a hold. Returns hold id or None."""
    with tx() as c:
        cur = c.execute(
            "UPDATE users SET coin=coin-? WHERE id=? AND coin>=?", (amount, uid, amount)
        )
        if cur.rowcount == 0:
            return None
        _log(c, uid, -amount, f"bet:{game}")
        cur = c.execute(
            "INSERT INTO holds(uid,amount,game,ts) VALUES(?,?,?,?)",
            (uid, amount, game, int(time.time())),
        )
        return cur.lastrowid


def settle(hids, payouts, reason):
    """Close holds and pay out in ONE transaction.
    payouts = {uid: coin}. Returns False if any hold is already gone."""
    try:
        with tx() as c:
            q = ",".join("?" * len(hids))
            n = c.execute(f"DELETE FROM holds WHERE id IN ({q})", list(hids)).rowcount
            if n != len(hids):
                raise Abort()
            for uid, amt in payouts.items():
                if amt > 0:
                    c.execute("UPDATE users SET coin=coin+? WHERE id=?", (amt, uid))
                    _log(c, uid, amt, f"win:{reason}")
        return True
    except Abort:
        return False


def refund_hold(hid):
    with tx() as c:
        r = c.execute("SELECT * FROM holds WHERE id=?", (hid,)).fetchone()
        if not r:
            return False
        c.execute("UPDATE users SET coin=coin+? WHERE id=?", (r["amount"], r["uid"]))
        _log(c, r["uid"], r["amount"], f"refund:{r['game']}")
        c.execute("DELETE FROM holds WHERE id=?", (hid,))
        return True


def refund_holds(older=0):
    """Refund every hold older than `older` seconds. Returns [(uid, amount, game)]."""
    out = []
    with tx() as c:
        rows = c.execute(
            "SELECT * FROM holds WHERE ts<=?", (int(time.time()) - older,)
        ).fetchall()
        for r in rows:
            c.execute("UPDATE users SET coin=coin+? WHERE id=?", (r["amount"], r["uid"]))
            _log(c, r["uid"], r["amount"], f"refund:{r['game']}")
            c.execute("DELETE FROM holds WHERE id=?", (r["id"],))
            out.append((r["uid"], r["amount"], r["game"]))
    return out


# ---------- lottery ----------
def place_bet(uid, chat, draw, number, amount):
    with tx() as c:
        if c.execute("SELECT 1 FROM bets WHERE draw=? AND uid=?", (draw, uid)).fetchone():
            return "dup"
        cur = c.execute(
            "UPDATE users SET coin=coin-? WHERE id=? AND coin>=?", (amount, uid, amount)
        )
        if cur.rowcount == 0:
            return "poor"
        _log(c, uid, -amount, "bet:lottery")
        c.execute(
            "INSERT INTO bets(draw,uid,chat,number,amount) VALUES(?,?,?,?,?)",
            (draw, uid, chat, number, amount),
        )
        return "ok"


def pending_draws():
    with _lock:
        return [r[0] for r in _c.execute("SELECT DISTINCT draw FROM bets WHERE settled=0")]


def settle_draw(draw):
    """Pick the winning number (once) and pay winners 10x. Atomic."""
    with tx() as c:
        r = c.execute("SELECT number FROM draws WHERE draw=?", (draw,)).fetchone()
        if r:
            num = r["number"]
        else:
            num = random.randint(0, 999)
            c.execute("INSERT INTO draws(draw,number) VALUES(?,?)", (draw, num))
        winners, chats = [], set()
        for b in c.execute("SELECT * FROM bets WHERE draw=? AND settled=0", (draw,)).fetchall():
            chats.add(b["chat"])
            if b["number"] == num:
                prize = b["amount"] * 10
                c.execute("UPDATE users SET coin=coin+? WHERE id=?", (prize, b["uid"]))
                _log(c, b["uid"], prize, "win:lottery")
                winners.append((b["uid"], prize))
            c.execute("UPDATE bets SET settled=1 WHERE id=?", (b["id"],))
        return num, winners, chats


# ---------- one-time bonus ----------
def claim_once(uid, kind, amount):
    """Give `amount` coin once per (user, kind). Returns True only the first time."""
    with tx() as c:
        if c.execute("SELECT 1 FROM claims WHERE uid=? AND kind=?", (uid, kind)).fetchone():
            return False
        c.execute("INSERT INTO claims(uid,kind,ts) VALUES(?,?,?)", (uid, kind, int(time.time())))
        c.execute("UPDATE users SET coin=coin+? WHERE id=?", (amount, uid))
        _log(c, uid, amount, f"bonus:{kind}")
        return True
