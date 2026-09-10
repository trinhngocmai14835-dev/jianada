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
        c.execute("""
            CREATE TABLE IF NOT EXISTS draw_records (
                issue TEXT PRIMARY KEY,
                draw_time TEXT,
                n1 INTEGER,
                n2 INTEGER,
                n3 INTEGER,
                total INTEGER,
                dx TEXT,
                ds TEXT,
                special INTEGER,
                source TEXT,
                captured_at TEXT
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_draw_records_issue ON draw_records(issue)")
        c.execute("""
            CREATE TABLE IF NOT EXISTS draw_record_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                captured_at TEXT,
                source TEXT,
                record_count INTEGER,
                first_issue TEXT,
                last_issue TEXT,
                records_json TEXT
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_draw_record_snapshots_id ON draw_record_snapshots(id)")


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

def _draw_label(total: int) -> tuple[str, str, int]:
    return ("小" if total <= 13 else "大", "单" if total % 2 else "双", 1 if total in (13, 14) else 0)


def _coerce_draw_numbers(record):
    nums = record.get("numbers") if isinstance(record, dict) else None
    if not isinstance(nums, (list, tuple)) or len(nums) < 3:
        return None
    try:
        values = [int(nums[0]), int(nums[1]), int(nums[2])]
    except (TypeError, ValueError):
        return None
    if any(v < 0 or v > 9 for v in values):
        return None
    return values


def add_draw_records(records, source: str = "browser") -> int:
    saved = 0
    captured_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _conn() as c:
        for record in records or []:
            if not isinstance(record, dict):
                continue
            issue = str(record.get("issue") or record.get("period") or "").strip()
            nums = _coerce_draw_numbers(record)
            if not issue or nums is None:
                continue
            total = nums[0] + nums[1] + nums[2]
            dx, ds, special = _draw_label(total)
            c.execute(
                """
                INSERT OR REPLACE INTO draw_records
                (issue, draw_time, n1, n2, n3, total, dx, ds, special, source, captured_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    issue,
                    str(record.get("draw_time") or record.get("drawTime") or ""),
                    nums[0],
                    nums[1],
                    nums[2],
                    total,
                    dx,
                    ds,
                    special,
                    source or "",
                    captured_at,
                ),
            )
            saved += 1
    return saved


def _normalize_snapshot_records(records, source: str, captured_at: str):
    normalized = []
    for record in records or []:
        if not isinstance(record, dict):
            continue
        issue = str(record.get("issue") or record.get("period") or "").strip()
        nums = _coerce_draw_numbers(record)
        if not issue or nums is None:
            continue
        total = nums[0] + nums[1] + nums[2]
        dx, ds, special = _draw_label(total)
        normalized.append({
            "issue": issue,
            "draw_time": str(record.get("draw_time") or record.get("drawTime") or ""),
            "numbers": nums,
            "total": total,
            "dx": dx,
            "ds": ds,
            "special": bool(special),
            "source": source or "",
            "captured_at": captured_at,
        })
    return normalized


def save_draw_record_snapshot(records, source: str = "browser"):
    captured_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    normalized = _normalize_snapshot_records(records, source, captured_at)
    if not normalized:
        return None
    ordered = sorted(normalized, key=lambda item: (0, int(item["issue"])) if str(item.get("issue", "")).isdigit() else (1, str(item.get("issue", ""))))
    with _conn() as c:
        cur = c.execute(
            """
            INSERT INTO draw_record_snapshots
            (captured_at, source, record_count, first_issue, last_issue, records_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                captured_at,
                source or "",
                len(ordered),
                ordered[0]["issue"],
                ordered[-1]["issue"],
                json.dumps(ordered, ensure_ascii=False),
            ),
        )
        snapshot_id = cur.lastrowid
    return {
        "id": snapshot_id,
        "captured_at": captured_at,
        "source": source or "",
        "record_count": len(ordered),
        "first_issue": ordered[0]["issue"],
        "last_issue": ordered[-1]["issue"],
        "records": ordered,
    }


def get_latest_draw_record_snapshot(limit: int = 1000):
    limit = min(5000, max(1, int(limit or 1000)))
    with _conn() as c:
        row = c.execute(
            """
            SELECT id, captured_at, source, record_count, first_issue, last_issue, records_json
            FROM draw_record_snapshots
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
    if not row:
        return None
    try:
        records = json.loads(row[6] or "[]")
    except Exception:
        records = []
    if limit and len(records) > limit:
        records = records[-limit:]
    first_issue = records[0].get("issue") if records else (row[4] or "")
    last_issue = records[-1].get("issue") if records else (row[5] or "")
    return {
        "id": row[0],
        "captured_at": row[1] or "",
        "source": row[2] or "",
        "record_count": len(records),
        "snapshot_record_count": row[3] or len(records),
        "first_issue": first_issue,
        "last_issue": last_issue,
        "records": records,
    }


def get_draw_records(limit: int = 1000):
    limit = min(5000, max(1, int(limit or 1000)))
    with _conn() as c:
        rows = c.execute(
            """
            SELECT issue, draw_time, n1, n2, n3, total, dx, ds, special, source, captured_at
            FROM draw_records
            ORDER BY CAST(issue AS INTEGER) DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [
        {
            "issue": r[0],
            "draw_time": r[1] or "",
            "numbers": [r[2], r[3], r[4]],
            "total": r[5],
            "dx": r[6],
            "ds": r[7],
            "special": bool(r[8]),
            "source": r[9] or "",
            "captured_at": r[10] or "",
        }
        for r in rows
    ]


def clear_draw_records() -> None:
    with _conn() as c:
        c.execute("DELETE FROM draw_records")
        c.execute("DELETE FROM draw_record_snapshots")
