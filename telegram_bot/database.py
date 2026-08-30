import sqlite3
import os
from contextlib import contextmanager

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "orders.db")


class Database:
    def __init__(self):
        self._init_db()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id     INTEGER NOT NULL,
                    username    TEXT,
                    machine_id  TEXT NOT NULL,
                    plan_id     TEXT NOT NULL,
                    days        INTEGER NOT NULL,
                    price       REAL NOT NULL,
                    txhash      TEXT,
                    status      TEXT DEFAULT 'pending',
                    license_key TEXT,
                    created_at  TEXT DEFAULT (datetime('now','localtime')),
                    updated_at  TEXT DEFAULT (datetime('now','localtime'))
                )
            """)
            # 简化充值记录：只记录客户提交的 TRC20-USDT 入账
            conn.execute("""
                CREATE TABLE IF NOT EXISTS recharges (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id         INTEGER NOT NULL,
                    username        TEXT,
                    txhash          TEXT UNIQUE NOT NULL,
                    from_address    TEXT,
                    to_address      TEXT,
                    amount          REAL NOT NULL,
                    status          TEXT DEFAULT 'confirmed',
                    block_timestamp INTEGER,
                    created_at      TEXT DEFAULT (datetime('now','localtime')),
                    updated_at      TEXT DEFAULT (datetime('now','localtime'))
                )
            """)
            # 到账通知去重表：记录已处理过的链上入账 TxHash
            conn.execute("""
                CREATE TABLE IF NOT EXISTS payment_notifications (
                    txhash          TEXT PRIMARY KEY,
                    from_address    TEXT,
                    to_address      TEXT,
                    amount          REAL NOT NULL,
                    block_timestamp INTEGER,
                    order_id        INTEGER,
                    notified        INTEGER DEFAULT 1,
                    created_at      TEXT DEFAULT (datetime('now','localtime'))
                )
            """)
            # 简单状态表：存储后台监听是否已完成首次启动初始化
            conn.execute("""
                CREATE TABLE IF NOT EXISTS app_state (
                    name       TEXT PRIMARY KEY,
                    value      TEXT NOT NULL,
                    updated_at TEXT DEFAULT (datetime('now','localtime'))
                )
            """)
            # 代理账户
            conn.execute("""
                CREATE TABLE IF NOT EXISTS agents (
                    user_id    INTEGER PRIMARY KEY,
                    username   TEXT,
                    note       TEXT,
                    created_at TEXT DEFAULT (datetime('now','localtime'))
                )
            """)
            # 代理各套餐卡库存
            conn.execute("""
                CREATE TABLE IF NOT EXISTS agent_inventory (
                    agent_id   INTEGER NOT NULL,
                    plan_id    TEXT NOT NULL,
                    balance    INTEGER DEFAULT 0,
                    PRIMARY KEY (agent_id, plan_id)
                )
            """)
            # 代理发卡流水
            conn.execute("""
                CREATE TABLE IF NOT EXISTS agent_logs (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id    INTEGER NOT NULL,
                    plan_id     TEXT NOT NULL,
                    machine_id  TEXT NOT NULL,
                    license_key TEXT NOT NULL,
                    created_at  TEXT DEFAULT (datetime('now','localtime'))
                )
            """)

    def create_order(self, user_id, username, machine_id, plan_id, days, price, txhash) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO orders (user_id,username,machine_id,plan_id,days,price,txhash,status) "
                "VALUES (?,?,?,?,?,?,?,'pending')",
                (user_id, username, machine_id, plan_id, days, price, txhash),
            )
            return cur.lastrowid

    def confirm_order(self, order_id: int, license_key: str):
        with self._conn() as conn:
            conn.execute(
                "UPDATE orders SET status='confirmed', license_key=?, "
                "updated_at=datetime('now','localtime') WHERE id=?",
                (license_key, order_id),
            )

    def set_reviewing(self, order_id: int):
        with self._conn() as conn:
            conn.execute(
                "UPDATE orders SET status='reviewing', updated_at=datetime('now','localtime') WHERE id=?",
                (order_id,),
            )

    def reject_order(self, order_id: int):
        with self._conn() as conn:
            conn.execute(
                "UPDATE orders SET status='rejected', updated_at=datetime('now','localtime') WHERE id=?",
                (order_id,),
            )

    def get_order(self, order_id: int):
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
            return dict(row) if row else None

    def get_order_by_txhash(self, txhash: str):
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM orders WHERE txhash=?", (txhash,)).fetchone()
            return dict(row) if row else None

    def get_user_orders(self, user_id: int):
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10", (user_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_pending_orders(self):
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM orders WHERE status IN ('pending','reviewing') ORDER BY id"
            ).fetchall()
            return [dict(r) for r in rows]

    # ── 简化充值记录 ──────────────────────────────────────────────────────────

    def recharge_txhash_exists(self, txhash: str) -> bool:
        with self._conn() as conn:
            row = conn.execute("SELECT 1 FROM recharges WHERE txhash=? LIMIT 1", (txhash,)).fetchone()
            return row is not None

    def create_recharge(
        self,
        user_id: int,
        username: str,
        txhash: str,
        from_address: str,
        to_address: str,
        amount: float,
        block_timestamp=None,
    ):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO recharges "
                "(user_id, username, txhash, from_address, to_address, amount, block_timestamp, status) "
                "VALUES (?,?,?,?,?,?,?,'confirmed')",
                (
                    user_id,
                    username or "",
                    txhash,
                    from_address or "",
                    to_address or "",
                    float(amount or 0),
                    block_timestamp,
                ),
            )
            row = conn.execute("SELECT * FROM recharges WHERE txhash=?", (txhash,)).fetchone()
            return dict(row) if row else None

    def get_recharge_by_txhash(self, txhash: str):
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM recharges WHERE txhash=?", (txhash,)).fetchone()
            return dict(row) if row else None

    def get_user_recharges(self, user_id: int, limit: int = 10):
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM recharges WHERE user_id=? ORDER BY id DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_recent_recharges(self, limit: int = 20):
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM recharges ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ── 代理管理 ──────────────────────────────────────────────────────────────

    def add_agent(self, user_id: int, username: str = "", note: str = ""):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO agents (user_id, username, note) VALUES (?,?,?)",
                (user_id, username, note),
            )

    def is_agent(self, user_id: int) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM agents WHERE user_id=?", (user_id,)
            ).fetchone()
            return row is not None

    def get_all_agents(self) -> list:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM agents ORDER BY created_at").fetchall()
            return [dict(r) for r in rows]

    def get_agent_balances(self, agent_id: int) -> dict:
        """返回 {plan_id: balance}，只含 balance > 0 的条目。"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT plan_id, balance FROM agent_inventory WHERE agent_id=? AND balance > 0",
                (agent_id,),
            ).fetchall()
            return {r["plan_id"]: r["balance"] for r in rows}

    def get_agent_plan_balance(self, agent_id: int, plan_id: str) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT balance FROM agent_inventory WHERE agent_id=? AND plan_id=?",
                (agent_id, plan_id),
            ).fetchone()
            return row["balance"] if row else 0

    def topup_agent(self, agent_id: int, plan_id: str, qty: int) -> int:
        """增加代理卡量，返回充值后的最新余量。"""
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO agent_inventory (agent_id, plan_id, balance) VALUES (?,?,?) "
                "ON CONFLICT(agent_id, plan_id) DO UPDATE SET balance = balance + excluded.balance",
                (agent_id, plan_id, qty),
            )
            row = conn.execute(
                "SELECT balance FROM agent_inventory WHERE agent_id=? AND plan_id=?",
                (agent_id, plan_id),
            ).fetchone()
            return row["balance"] if row else qty

    def deduct_agent_card(self, agent_id: int, plan_id: str, license_key: str, machine_id: str) -> bool:
        """扣减1张卡并写入发卡流水。返回 False 表示库存不足（并发竞争导致）。"""
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE agent_inventory SET balance = balance - 1 "
                "WHERE agent_id=? AND plan_id=? AND balance > 0",
                (agent_id, plan_id),
            )
            if cur.rowcount == 0:
                return False
            conn.execute(
                "INSERT INTO agent_logs (agent_id, plan_id, machine_id, license_key) VALUES (?,?,?,?)",
                (agent_id, plan_id, machine_id, license_key),
            )
            return True

    def get_agent_logs(self, agent_id: int, limit: int = 20) -> list:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM agent_logs WHERE agent_id=? ORDER BY id DESC LIMIT ?",
                (agent_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def txhash_exists(self, txhash: str) -> bool:
        """检查 TxHash 是否已被提交过（防止一笔付款多次使用）"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM orders WHERE txhash=? "
                "AND status IN ('pending','reviewing','confirmed') LIMIT 1",
                (txhash,),
            ).fetchone()
            return row is not None

    def payment_notification_exists(self, txhash: str) -> bool:
        """检查该链上入账是否已经提醒或标记过。"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM payment_notifications WHERE txhash=? LIMIT 1",
                (txhash,),
            ).fetchone()
            return row is not None

    def record_payment_notification(
        self,
        txhash: str,
        from_address: str = "",
        to_address: str = "",
        amount: float = 0,
        block_timestamp=None,
        order_id=None,
        notified: bool = True,
    ) -> bool:
        """记录已处理的到账通知。返回 True 表示本次新插入。"""
        txhash = (txhash or "").strip()
        if not txhash:
            return False
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO payment_notifications "
                "(txhash, from_address, to_address, amount, block_timestamp, order_id, notified) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    txhash,
                    from_address or "",
                    to_address or "",
                    float(amount or 0),
                    block_timestamp,
                    order_id,
                    1 if notified else 0,
                ),
            )
            return cur.rowcount == 1

    def link_payment_notification_order(self, txhash: str, order_id: int):
        """当客户后来提交 TxHash 时，把已监听到的到账记录关联到订单。"""
        with self._conn() as conn:
            conn.execute(
                "UPDATE payment_notifications SET order_id=COALESCE(order_id, ?) WHERE txhash=?",
                (order_id, txhash),
            )

    def get_app_state(self, name: str, default=None):
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM app_state WHERE name=?", (name,)).fetchone()
            return row["value"] if row else default

    def set_app_state(self, name: str, value):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO app_state (name, value, updated_at) VALUES (?,?,datetime('now','localtime')) "
                "ON CONFLICT(name) DO UPDATE SET value=excluded.value, updated_at=datetime('now','localtime')",
                (name, str(value)),
            )

    def has_used_trial(self, machine_id: str, plan_id: str = "trial7") -> bool:
        """检查该机器是否已经用过试用卡（跨所有代理）"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM agent_logs WHERE machine_id=? AND plan_id=? LIMIT 1",
                (machine_id.upper(), plan_id),
            ).fetchone()
            return row is not None

    def get_latest_license_expiry(self, machine_id: str):
        """返回该机器所有有效授权码中最晚的到期 datetime，全部过期或无记录则返回 None。"""
        from datetime import datetime, timedelta
        mid = machine_id.upper()
        keys = []
        with self._conn() as conn:
            # 取 orders 和 agent_logs 各自最新一条，再取两者中到期最晚的
            rows = conn.execute(
                "SELECT license_key FROM orders WHERE machine_id=? AND status='confirmed' "
                "ORDER BY id DESC LIMIT 20", (mid,),
            ).fetchall()
            keys.extend(r["license_key"] for r in rows)
            rows = conn.execute(
                "SELECT license_key FROM agent_logs WHERE machine_id=? ORDER BY id DESC LIMIT 20",
                (mid,),
            ).fetchall()
            keys.extend(r["license_key"] for r in rows)

        latest = None
        now = datetime.now()
        for key in keys:
            try:
                expiry = datetime.strptime(key[:8], "%Y%m%d")
                # 到期日当天 23:59 前仍有效，与 validate_license 保持一致
                if expiry + timedelta(days=1) > now and (latest is None or expiry > latest):
                    latest = expiry
            except Exception:
                pass
        return latest