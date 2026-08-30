import sqlite3
import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.expanduser('~'), '.betting_platform', 'data.db')

@contextmanager
def _conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with _conn() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("""
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS license_info (
                id INTEGER PRIMARY KEY,
                key TEXT,
                expiry TEXT,
                activated_at TEXT
            )
        """)
        # 投注流水：只存有用信息（登录/下注/结算），啰嗦运行日志不入库
        c.execute("""
            CREATE TABLE IF NOT EXISTS bet_flow (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT,
                mode TEXT,
                account TEXT,
                msg TEXT
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_flow_account ON bet_flow(account)")


def get_config(key: str, default=None):
    with _conn() as c:
        row = c.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
    if row:
        try:
            return json.loads(row[0])
        except Exception:
            return row[0]
    return default


def set_config(key: str, value):
    serialized = json.dumps(value, ensure_ascii=False)
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, serialized))


def get_license():
    with _conn() as c:
        row = c.execute(
            "SELECT key, expiry FROM license_info ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if row:
        return {'key': row[0], 'expiry': row[1]}
    return None


def save_license(key: str, expiry: str):
    with _conn() as c:
        c.execute("DELETE FROM license_info")
        c.execute(
            "INSERT INTO license_info (key, expiry, activated_at) VALUES (?, ?, ?)",
            (key, expiry, datetime.now().isoformat()),
        )


# ─── 投注流水 ────────────────────────────────────────────────

def add_flow(mode: str, account: str, msg: str, ts: str = None):
    ts = ts or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _conn() as c:
        c.execute("INSERT INTO bet_flow (ts, mode, account, msg) VALUES (?,?,?,?)",
                  (ts, mode or '', account or '', msg or ''))
        # 软上限：超过 6 万条时删最旧的 1 万，防止无限增长
        cnt = c.execute("SELECT COUNT(*) FROM bet_flow").fetchone()[0]
        if cnt > 60000:
            c.execute("DELETE FROM bet_flow WHERE id IN "
                      "(SELECT id FROM bet_flow ORDER BY id ASC LIMIT 10000)")


def get_flow(account: str = None, mode: str = None, limit: int = 800):
    q = ("SELECT id, ts, mode, account, msg FROM bet_flow "
         "WHERE account != ? AND msg NOT LIKE ? AND msg NOT LIKE ? AND msg NOT LIKE ?")
    args = ["审计", "%等待开奖结算%", "%等待下注窗口%", "%观察到新开奖%"]
    if account:
        q += " AND account = ?"; args.append(account)
    if mode:
        q += " AND mode = ?"; args.append(mode)
    q += " ORDER BY id DESC LIMIT ?"; args.append(int(limit))
    with _conn() as c:
        rows = c.execute(q, args).fetchall()
    return [{"id": r[0], "ts": r[1], "mode": r[2], "account": r[3], "msg": r[4]} for r in rows]


def flow_accounts():
    """登录/投注过的账号清单 + 各自流水条数（最近活跃在前）。"""
    with _conn() as c:
        rows = c.execute(
            "SELECT account, COUNT(*) FROM bet_flow WHERE account != '' "
            "AND account != '审计' AND msg NOT LIKE '%等待开奖结算%' "
            "AND msg NOT LIKE '%等待下注窗口%' "
            "AND msg NOT LIKE '%观察到新开奖%' "
            "GROUP BY account ORDER BY MAX(id) DESC"
        ).fetchall()
    return [{"account": r[0], "count": r[1]} for r in rows]


def clear_flow(account: str = None, days: int = None):
    with _conn() as c:
        if account:
            c.execute("DELETE FROM bet_flow WHERE account = ?", (account,))
        elif days:
            cutoff = (datetime.now() - timedelta(days=int(days))).strftime("%Y-%m-%d %H:%M:%S")
            c.execute("DELETE FROM bet_flow WHERE ts < ?", (cutoff,))
        else:
            c.execute("DELETE FROM bet_flow")
