CREATE TABLE IF NOT EXISTS customers (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  contact TEXT NOT NULL DEFAULT '',
  remark TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS machines (
  id TEXT PRIMARY KEY,
  customer_id TEXT NOT NULL,
  machine_id TEXT NOT NULL UNIQUE,
  expiry TEXT NOT NULL DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 1,
  accounts_json TEXT NOT NULL DEFAULT '[]',
  remark TEXT NOT NULL DEFAULT '',
  last_license_key TEXT NOT NULL DEFAULT '',
  last_synced_at TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (customer_id) REFERENCES customers(id)
);

CREATE TABLE IF NOT EXISTS license_events (
  id TEXT PRIMARY KEY,
  customer_id TEXT NOT NULL,
  machine_id TEXT NOT NULL,
  expiry TEXT NOT NULL,
  days INTEGER,
  license_key TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
  id TEXT PRIMARY KEY,
  actor TEXT NOT NULL DEFAULT 'admin',
  action TEXT NOT NULL,
  target TEXT NOT NULL DEFAULT '',
  details_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_machines_customer_id
ON machines(customer_id);

CREATE INDEX IF NOT EXISTS idx_license_events_machine_id
ON license_events(machine_id);

CREATE INDEX IF NOT EXISTS idx_audit_events_created_at
ON audit_events(created_at);

PRAGMA optimize;

