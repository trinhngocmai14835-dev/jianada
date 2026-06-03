import sqlite3
import json
import os
from contextlib import contextmanager
from datetime import datetime

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
