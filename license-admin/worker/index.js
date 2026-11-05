const JSON_HEADERS = {
  "content-type": "application/json; charset=utf-8",
  "cache-control": "no-store",
};

const HTML_HEADERS = {
  "content-type": "text/html; charset=utf-8",
  "cache-control": "no-store",
};

const SCHEMA_STATEMENTS = [
  `CREATE TABLE IF NOT EXISTS customers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    contact TEXT NOT NULL DEFAULT '',
    remark TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
  )`,
  `CREATE TABLE IF NOT EXISTS machines (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    machine_id TEXT NOT NULL UNIQUE,
    expiry TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    accounts_json TEXT NOT NULL DEFAULT '[]',
    bindings_json TEXT NOT NULL DEFAULT '[]',
    remark TEXT NOT NULL DEFAULT '',
    last_license_key TEXT NOT NULL DEFAULT '',
    last_synced_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (customer_id) REFERENCES customers(id)
  )`,
  `CREATE TABLE IF NOT EXISTS license_events (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    machine_id TEXT NOT NULL,
    expiry TEXT NOT NULL,
    days INTEGER,
    license_key TEXT NOT NULL,
    created_at TEXT NOT NULL
  )`,
  `CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    actor TEXT NOT NULL DEFAULT 'admin',
    action TEXT NOT NULL,
    target TEXT NOT NULL DEFAULT '',
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
  )`,
  "CREATE INDEX IF NOT EXISTS idx_machines_customer_id ON machines(customer_id)",
  "CREATE INDEX IF NOT EXISTS idx_license_events_machine_id ON license_events(machine_id)",
  "CREATE INDEX IF NOT EXISTS idx_audit_events_created_at ON audit_events(created_at)",
];

const encoder = new TextEncoder();

export default {
  async fetch(request, env) {
    try {
      const url = new URL(request.url);
      if (url.pathname.startsWith("/api/")) {
        return await handleApi(request, env, url);
      }
      if (env.ASSETS) {
        return env.ASSETS.fetch(request);
      }
      return new Response(INDEX_HTML, { headers: HTML_HEADERS });
    } catch (error) {
      return json({ ok: false, message: error.message || String(error) }, 500);
    }
  },
};

async function handleApi(request, env, url) {
  if (request.method === "OPTIONS") {
    return new Response(null, { headers: corsHeaders(request) });
  }

  const route = `${request.method} ${url.pathname}`;

  if (route === "POST /api/login") return login(request, env);
  if (route === "POST /api/logout") return logout(env);
  if (route === "GET /api/session") return session(request, env);
  // Public client verification endpoint. It returns only allow/deny; safety
  // codes and their salted verifiers never appear in public R2 JSON.
  if (route === "POST /api/client/account-binding-check") {
    await initDb(env);
    return verifyAccountBinding(request, env);
  }

  const auth = await requireAuth(request, env);
  if (!auth.ok) return auth.response;

  await initDb(env);

  if (route === "GET /api/customers") return listCustomers(env);
  if (route === "POST /api/customers") return createCustomer(request, env);

  const customerMatch = url.pathname.match(/^\/api\/customers\/([^/]+)$/);
  if (customerMatch && request.method === "GET") {
    return getCustomer(env, customerMatch[1]);
  }
  if (customerMatch && request.method === "PUT") {
    return updateCustomer(request, env, customerMatch[1]);
  }

  const customerMachineMatch = url.pathname.match(/^\/api\/customers\/([^/]+)\/machines$/);
  if (customerMachineMatch && request.method === "POST") {
    return addMachine(request, env, customerMachineMatch[1]);
  }

  const machineMatch = url.pathname.match(/^\/api\/machines\/([^/]+)$/);
  if (machineMatch && request.method === "PUT") {
    return updateMachine(request, env, machineMatch[1]);
  }

  const licenseMatch = url.pathname.match(/^\/api\/machines\/([^/]+)\/license$/);
  if (licenseMatch && request.method === "POST") {
    return generateLicense(request, env, licenseMatch[1]);
  }

  const syncMatch = url.pathname.match(/^\/api\/machines\/([^/]+)\/sync-r2$/);
  if (syncMatch && request.method === "POST") {
    return syncWhitelist(env, syncMatch[1]);
  }

  const r2Match = url.pathname.match(/^\/api\/machines\/([^/]+)\/r2$/);
  if (r2Match && request.method === "GET") {
    return getR2Record(env, r2Match[1]);
  }

  return json({ ok: false, message: "API route not found" }, 404);
}

async function initDb(env) {
  if (!env.DB) throw new Error("D1 binding DB is not configured");
  await env.DB.batch(SCHEMA_STATEMENTS.map((sql) => env.DB.prepare(sql)));
  // Existing production databases predate bindings_json.  Use a small,
  // idempotent migration so deploying this Worker does not require a manual
  // D1 command before the admin portal can be used.
  const columns = await env.DB.prepare("PRAGMA table_info(machines)").all();
  const names = new Set((columns.results || []).map((column) => column.name));
  if (!names.has("bindings_json")) {
    try {
      await env.DB.prepare("ALTER TABLE machines ADD COLUMN bindings_json TEXT NOT NULL DEFAULT '[]'").run();
    } catch (error) {
      // A concurrent first request may have completed the same migration.
      if (!/duplicate column name/i.test(String(error?.message || error))) throw error;
    }
  }
}

async function login(request, env) {
  assertAuthEnv(env);
  const body = await readJson(request);
  const username = String(body.username || "").trim();
  const password = String(body.password || "");
  const expectedUser = env.ADMIN_USERNAME || "admin";
  const digest = await sha256Hex(password);

  if (username !== expectedUser || digest !== String(env.ADMIN_PASSWORD_SHA256 || "").toLowerCase()) {
    return json({ ok: false, message: "Invalid username or password" }, 401);
  }

  const token = await makeSessionToken(env, username);
  const cookieName = env.SESSION_COOKIE_NAME || "ja_admin_session";
  return json(
    { ok: true, user: { username } },
    200,
    {
      "set-cookie": `${cookieName}=${token}; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=28800`,
    },
  );
}

function logout(env) {
  const cookieName = env.SESSION_COOKIE_NAME || "ja_admin_session";
  return json({ ok: true }, 200, {
    "set-cookie": `${cookieName}=; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=0`,
  });
}

async function session(request, env) {
  const auth = await requireAuth(request, env);
  if (!auth.ok) return json({ ok: true, authenticated: false });
  return json({ ok: true, authenticated: true, user: auth.user });
}

async function requireAuth(request, env) {
  try {
    assertAuthEnv(env);
    const cookieName = env.SESSION_COOKIE_NAME || "ja_admin_session";
    const token = getCookie(request, cookieName);
    const user = await verifySessionToken(env, token);
    if (!user) {
      return { ok: false, response: json({ ok: false, message: "Login required" }, 401) };
    }
    return { ok: true, user };
  } catch (error) {
    return { ok: false, response: json({ ok: false, message: error.message }, 500) };
  }
}

function assertAuthEnv(env) {
  if (!env.ADMIN_PASSWORD_SHA256) throw new Error("ADMIN_PASSWORD_SHA256 is not configured");
  if (!env.SESSION_SECRET) throw new Error("SESSION_SECRET is not configured");
}

async function listCustomers(env) {
  const customers = await env.DB.prepare(
    `SELECT id, name, contact, remark, status, created_at, updated_at
     FROM customers
     ORDER BY updated_at DESC`,
  ).all();
  const machines = await env.DB.prepare(
    `SELECT id, customer_id, machine_id, expiry, enabled, accounts_json, bindings_json, remark,
            last_license_key, last_synced_at, created_at, updated_at
     FROM machines
     ORDER BY updated_at DESC`,
  ).all();

  const grouped = new Map();
  for (const m of machines.results || []) {
    const item = normalizeMachineRow(m);
    if (!grouped.has(item.customer_id)) grouped.set(item.customer_id, []);
    grouped.get(item.customer_id).push(item);
  }

  return json({
    ok: true,
    customers: (customers.results || []).map((c) => ({
      ...c,
      machines: grouped.get(c.id) || [],
    })),
  });
}

async function customerPayload(env, customerId) {
  const customer = await env.DB.prepare(
    "SELECT id, name, contact, remark, status, created_at, updated_at FROM customers WHERE id = ?",
  ).bind(customerId).first();
  if (!customer) return null;
  const machines = await env.DB.prepare(
    `SELECT id, customer_id, machine_id, expiry, enabled, accounts_json, bindings_json, remark,
            last_license_key, last_synced_at, created_at, updated_at
     FROM machines WHERE customer_id = ? ORDER BY updated_at DESC`,
  ).bind(customerId).all();
  return { ...customer, machines: (machines.results || []).map(normalizeMachineRow) };
}

async function getCustomer(env, customerId) {
  const customer = await customerPayload(env, customerId);
  if (!customer) return json({ ok: false, message: "Customer not found" }, 404);
  return json({ ok: true, customer });
}

async function createCustomer(request, env) {
  const body = await readJson(request);
  const now = nowIso();
  const id = makeId("cus");
  const name = cleanText(body.name);
  if (!name) return json({ ok: false, message: "Customer name is required" }, 400);

  await env.DB.prepare(
    `INSERT INTO customers (id, name, contact, remark, status, created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, ?)`,
  ).bind(
    id,
    name,
    cleanText(body.contact),
    cleanText(body.remark),
    cleanText(body.status || "active") || "active",
    now,
    now,
  ).run();

  await audit(env, "customer.create", id, { name });
  return getCustomer(env, id);
}

async function updateCustomer(request, env, customerId) {
  const body = await readJson(request);
  const current = await env.DB.prepare("SELECT id FROM customers WHERE id = ?").bind(customerId).first();
  if (!current) return json({ ok: false, message: "Customer not found" }, 404);
  const name = cleanText(body.name);
  if (!name) return json({ ok: false, message: "Customer name is required" }, 400);
  await env.DB.prepare(
    `UPDATE customers
     SET name = ?, contact = ?, remark = ?, status = ?, updated_at = ?
     WHERE id = ?`,
  ).bind(
    name,
    cleanText(body.contact),
    cleanText(body.remark),
    cleanText(body.status || "active") || "active",
    nowIso(),
    customerId,
  ).run();
  await audit(env, "customer.update", customerId, { name });
  return getCustomer(env, customerId);
}

async function addMachine(request, env, customerId) {
  const customer = await env.DB.prepare("SELECT id FROM customers WHERE id = ?").bind(customerId).first();
  if (!customer) return json({ ok: false, message: "Customer not found" }, 404);
  const body = await readJson(request);
  const machineId = normalizeMachineId(body.machine_id);
  if (!machineId) return json({ ok: false, message: "Machine ID must be 16-32 alphanumeric characters" }, 400);

  const existing = await env.DB.prepare(
    `SELECT m.machine_id, m.customer_id, c.name AS customer_name
     FROM machines m
     LEFT JOIN customers c ON c.id = m.customer_id
     WHERE m.machine_id = ?`,
  ).bind(machineId).first();
  if (existing) {
    const owner = existing.customer_id === customerId
      ? "当前客户"
      : `客户「${existing.customer_name || existing.customer_id}」`;
    return json({
      ok: false,
      message: `机器码 ${machineId} 已绑定到${owner}，不能重复绑定`,
      machine_id: machineId,
      customer_id: existing.customer_id,
    }, 409);
  }

  const now = nowIso();
  await env.DB.prepare(
    `INSERT INTO machines (
      id, customer_id, machine_id, expiry, enabled, accounts_json, bindings_json, remark,
      last_license_key, last_synced_at, created_at, updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, '[]', ?, '', '', ?, ?)`,
  ).bind(
    makeId("mac"),
    customerId,
    machineId,
    normalizeExpiry(body.expiry),
    body.enabled === false ? 0 : 1,
    JSON.stringify(cleanAccounts(body.accounts || [])),
    cleanText(body.remark),
    now,
    now,
  ).run();
  await audit(env, "machine.create", machineId, { customerId });
  return getCustomer(env, customerId);
}

async function updateMachine(request, env, machineIdParam) {
  const machineId = normalizeMachineId(machineIdParam);
  const body = await readJson(request);
  const current = await getMachine(env, machineId);
  if (!current) return json({ ok: false, message: "Machine not found" }, 404);
  const accounts = cleanAccounts(body.accounts || []);
  const bindings = Object.prototype.hasOwnProperty.call(body, "bindings")
    ? await createAccountBindings(body.bindings)
    : null;
  const effectiveAccounts = bindings
    ? cleanAccounts([...accounts, ...bindings.map((binding) => binding.account)])
    : accounts;
  await env.DB.prepare(
    `UPDATE machines
     SET expiry = ?, enabled = ?, accounts_json = ?, bindings_json = COALESCE(?, bindings_json), remark = ?, updated_at = ?
     WHERE machine_id = ?`,
  ).bind(
    normalizeExpiry(body.expiry),
    body.enabled === false ? 0 : 1,
    JSON.stringify(effectiveAccounts),
    bindings ? JSON.stringify(bindings) : null,
    cleanText(body.remark),
    nowIso(),
    machineId,
  ).run();
  await audit(env, "machine.update", machineId, {
    accounts: effectiveAccounts,
    safe_code_binding_accounts: bindings ? bindings.map((binding) => binding.account) : undefined,
  });
  const r2 = await publishWhitelist(env, machineId);
  const customer = await customerPayload(env, current.customer_id);
  if (!customer) return json({ ok: false, message: "Customer not found" }, 404);
  return json({ ok: true, customer, r2 });
}

async function generateLicense(request, env, machineIdParam) {
  if (!env.LICENSE_PRIVATE_KEY_PEM) {
    return json({ ok: false, message: "LICENSE_PRIVATE_KEY_PEM is not configured" }, 500);
  }
  const machineId = normalizeMachineId(machineIdParam);
  const body = await readJson(request);
  const machine = await getMachine(env, machineId);
  if (!machine) return json({ ok: false, message: "Machine not found" }, 404);

  const expiry = normalizeExpiry(body.expiry || machine.expiry || expiryFromDays(body.days || 30));
  if (!expiry) return json({ ok: false, message: "Expiry date is required" }, 400);

  const hasOwn = (name) => Object.prototype.hasOwnProperty.call(body, name);
  const accounts = hasOwn("accounts") ? cleanAccounts(body.accounts || []) : cleanAccounts(machine.accounts || []);
  const bindings = hasOwn("bindings")
    ? await createAccountBindings(body.bindings)
    : (machine._bindings || []);
  const effectiveAccounts = cleanAccounts([...accounts, ...bindings.map((binding) => binding.account)]);
  const enabled = hasOwn("enabled") ? body.enabled !== false : machine.enabled !== false;
  const remark = hasOwn("remark") ? cleanText(body.remark) : cleanText(machine.remark);

  const licenseKey = await signLicense(env.LICENSE_PRIVATE_KEY_PEM, machineId, expiry);
  const now = nowIso();
  await env.DB.batch([
    env.DB.prepare(
      `INSERT INTO license_events (id, customer_id, machine_id, expiry, days, license_key, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?)`,
    ).bind(makeId("lic"), machine.customer_id, machineId, expiry, Number(body.days || 0) || null, licenseKey, now),
    env.DB.prepare(
      `UPDATE machines
       SET expiry = ?, enabled = ?, accounts_json = ?, bindings_json = ?, remark = ?, last_license_key = ?, updated_at = ?
       WHERE machine_id = ?`,
    ).bind(expiry, enabled ? 1 : 0, JSON.stringify(effectiveAccounts), JSON.stringify(bindings), remark, licenseKey, now, machineId),
  ]);
  await audit(env, "license.generate", machineId, { expiry, accounts_count: effectiveAccounts.length, safe_code_binding_count: bindings.length });
  const r2 = await publishWhitelist(env, machineId);
  return json({ ok: true, license_key: licenseKey, expiry, r2 });
}
async function syncWhitelist(env, machineIdParam) {
  const machineId = normalizeMachineId(machineIdParam);
  if (!machineId) return json({ ok: false, message: "Invalid machine ID" }, 400);
  const r2 = await publishWhitelist(env, machineId);
  return json({ ok: true, ...r2 });
}

async function getR2Record(env, machineIdParam) {
  const machineId = normalizeMachineId(machineIdParam);
  if (!machineId) return json({ ok: false, message: "Invalid machine ID" }, 400);
  const verification = await verifyPublicWhitelist(env, machineId);
  return json({ ok: true, record: verification.record || null, verification });
}

async function verifyAccountBinding(request, env) {
  const body = await readJson(request);
  const machineId = normalizeMachineId(body.machine_id);
  const account = normalizeAccount(body.account);
  const safeCode = normalizeSafeCode(body.safe_code);
  if (!machineId || !account || !safeCode) {
    return json({ ok: true, allowed: false, message: "会员账号或安全码不匹配" }, 400);
  }

  const row = await env.DB.prepare(
    `SELECT m.machine_id, m.enabled, m.bindings_json, c.status AS customer_status
       FROM machines m
       LEFT JOIN customers c ON c.id = m.customer_id
      WHERE m.machine_id = ?`,
  ).bind(machineId).first();
  if (!row || row.enabled === 0 || row.customer_status !== "active") {
    return json({ ok: true, allowed: false, message: "该账号授权已停用，请联系管理员" }, 403);
  }

  const binding = safeJsonBindings(row.bindings_json).find((item) => item.account === account);
  if (!binding || !(await verifySafeCode(safeCode, binding.verifier))) {
    // Keep this response deliberately generic: it must not reveal whether a
    // particular account or safety code exists in another customer's record.
    return json({ ok: true, allowed: false, message: "会员账号或安全码不匹配" }, 403);
  }
  return json({ ok: true, allowed: true, message: "账号与安全码校验通过" });
}

async function publishWhitelist(env, machineIdParam) {
  const machineId = normalizeMachineId(machineIdParam);
  const key = machineId ? `account-whitelist/${machineId}.json` : "";
  if (!machineId) {
    return {
      synced: false,
      key,
      record: null,
      verification: verificationFailure("invalid_machine_id", "机器码格式无效", { machine_id: machineIdParam }),
    };
  }
  if (!env.ACCOUNT_WHITELIST_BUCKET) {
    return {
      synced: false,
      key,
      record: null,
      verification: verificationFailure(
        "r2_binding_missing",
        "R2 binding ACCOUNT_WHITELIST_BUCKET 未配置",
        { machine_id: machineId, key },
      ),
    };
  }

  const machine = await getMachine(env, machineId);
  if (!machine) {
    return {
      synced: false,
      key,
      record: null,
      verification: verificationFailure("machine_not_found", "Machine not found", { machine_id: machineId, key }),
    };
  }

  const syncedAt = nowIso();
  const record = whitelistRecord(machine, syncedAt);
  try {
    await env.ACCOUNT_WHITELIST_BUCKET.put(key, JSON.stringify(record, null, 2) + "\n", {
      httpMetadata: {
        contentType: "application/json; charset=utf-8",
        cacheControl: "no-store, max-age=0",
      },
    });
    await env.DB.prepare(
      "UPDATE machines SET last_synced_at = ?, updated_at = ? WHERE machine_id = ?",
    ).bind(syncedAt, syncedAt, machineId).run();
    await audit(env, "r2.sync", machineId, { key });
  } catch (error) {
    return {
      synced: false,
      key,
      record,
      verification: verificationFailure(
        "r2_write_failed",
        `R2 写入失败：${error?.message || String(error)}`,
        { machine_id: machineId, key },
      ),
    };
  }

  const verification = await verifyPublicWhitelist(env, machineId);
  return { synced: true, key, record, verification };
}

function verificationFailure(status, message, extra = {}) {
  return {
    ok: false,
    status,
    message,
    checked_at: nowIso(),
    ...extra,
  };
}

function publicWhitelistUrl(env, machineId) {
  const base = String(env.ACCOUNT_WHITELIST_PUBLIC_BASE_URL || "").replace(/\/+$/, "");
  return base ? `${base}/${machineId}.json` : "";
}

async function verifyPublicWhitelist(env, machineIdParam) {
  const machineId = normalizeMachineId(machineIdParam);
  if (!machineId) {
    return verificationFailure("invalid_machine_id", "机器码格式无效", { machine_id: machineIdParam });
  }
  const publicUrl = publicWhitelistUrl(env, machineId);
  if (!publicUrl) {
    return verificationFailure("public_url_missing", "公开 R2 URL 未配置", { machine_id: machineId });
  }

  let response;
  let text = "";
  try {
    response = await fetch(publicUrl, {
      headers: {
        accept: "application/json",
        "cache-control": "no-cache",
        "user-agent": "jianada-license-admin/1.0",
      },
      cf: { cacheTtl: 0 },
    });
    text = await response.text();
  } catch (error) {
    return verificationFailure(
      "network_error",
      `公开 R2 读取失败：${error?.message || String(error)}`,
      { machine_id: machineId, public_url: publicUrl },
    );
  }

  if (response.status === 404) {
    return verificationFailure("404", "404：公开 R2 JSON 不存在", {
      machine_id: machineId,
      public_url: publicUrl,
      http_status: response.status,
    });
  }
  if (!response.ok) {
    return verificationFailure("http_error", `公开 R2 返回 HTTP ${response.status}`, {
      machine_id: machineId,
      public_url: publicUrl,
      http_status: response.status,
    });
  }

  let record;
  try {
    record = JSON.parse(text);
  } catch (error) {
    return verificationFailure("json_parse_failed", `JSON 解析失败：${error?.message || String(error)}`, {
      machine_id: machineId,
      public_url: publicUrl,
      http_status: response.status,
    });
  }

  const recordMachineId = normalizeMachineId(record?.machine_id);
  if (recordMachineId !== machineId) {
    return verificationFailure("machine_id_mismatch", "JSON 机器码不匹配", {
      machine_id: machineId,
      public_url: publicUrl,
      http_status: response.status,
      record,
    });
  }

  const accounts = cleanAccounts(record?.accounts || []);
  if (record?.enabled !== true) {
    return verificationFailure("disabled", "未启用：enabled 不是 true", {
      machine_id: machineId,
      public_url: publicUrl,
      http_status: response.status,
      record: { ...record, accounts },
    });
  }
  if (!accounts.length) {
    return verificationFailure("no_accounts", "无账号：accounts 为空", {
      machine_id: machineId,
      public_url: publicUrl,
      http_status: response.status,
      record: { ...record, accounts },
    });
  }

  return {
    ok: true,
    status: "verified",
    message: "公开 R2 已验证",
    machine_id: machineId,
    public_url: publicUrl,
    http_status: response.status,
    accounts_count: accounts.length,
    checked_at: nowIso(),
    record: { ...record, accounts },
  };
}

async function getMachine(env, machineId) {
  const row = await env.DB.prepare(
    `SELECT id, customer_id, machine_id, expiry, enabled, accounts_json, bindings_json, remark,
            last_license_key, last_synced_at, created_at, updated_at
     FROM machines WHERE machine_id = ?`,
  ).bind(machineId).first();
  return row ? normalizeMachineRow(row, true) : null;
}

function normalizeMachineRow(row, includeBindingVerifiers = false) {
  const bindings = safeJsonBindings(row.bindings_json);
  const normalized = {
    ...row,
    enabled: row.enabled !== 0,
    accounts: safeJsonArray(row.accounts_json),
    binding_accounts: bindings.map((binding) => binding.account),
    safe_code_binding_required: bindings.length > 0,
  };
  // Verifiers must never be returned to the browser admin UI or public R2.
  if (includeBindingVerifiers) normalized._bindings = bindings;
  delete normalized.bindings_json;
  return normalized;
}

function whitelistRecord(machine, updatedAt = nowIso()) {
  return {
    machine_id: machine.machine_id,
    customer_id: machine.customer_id,
    enabled: machine.enabled !== false,
    accounts: cleanAccounts(machine.accounts || []),
    // This tells the new client to contact the Worker for an account +
    // safety-code check.  No hash, salt, or safety code is exposed publicly.
    safe_code_binding_required: (machine._bindings || []).length > 0,
    bound_accounts: (machine._bindings || []).map((binding) => binding.account),
    remark: cleanText(machine.remark),
    // The code is RSA-signed and machine-bound.  Desktop clients verify it
    // before saving, so a public object cannot grant another machine access.
    license_key: String(machine.last_license_key || ""),
    license_expiry: String(machine.expiry || ""),
    updated_at: updatedAt,
  };
}

async function audit(env, action, target, details = {}) {
  await env.DB.prepare(
    `INSERT INTO audit_events (id, actor, action, target, details_json, created_at)
     VALUES (?, 'admin', ?, ?, ?, ?)`,
  ).bind(makeId("aud"), action, target, JSON.stringify(details), nowIso()).run();
}

async function signLicense(privateKeyPem, machineId, expiry) {
  const key = await importPrivateKey(privateKeyPem);
  const payload = `${machineId}|${expiry}`;
  const sig = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", key, encoder.encode(payload));
  return `${expiry}.${base64NoPadding(sig)}`;
}

async function importPrivateKey(pem) {
  const der = pemToArrayBuffer(pem);
  return crypto.subtle.importKey(
    "pkcs8",
    der,
    { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" },
    false,
    ["sign"],
  );
}

function pemToArrayBuffer(pem) {
  const clean = String(pem)
    .replace(/-----BEGIN [^-]+-----/g, "")
    .replace(/-----END [^-]+-----/g, "")
    .replace(/\s+/g, "");
  const binary = atob(clean);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}

async function makeSessionToken(env, username) {
  const payload = base64Url(JSON.stringify({
    u: username,
    exp: Math.floor(Date.now() / 1000) + 8 * 60 * 60,
  }));
  const sig = await hmac(env.SESSION_SECRET, payload);
  return `${payload}.${sig}`;
}

async function verifySessionToken(env, token) {
  if (!token || !token.includes(".")) return null;
  const [payload, sig] = token.split(".");
  const expected = await hmac(env.SESSION_SECRET, payload);
  if (sig !== expected) return null;
  try {
    const data = JSON.parse(new TextDecoder().decode(base64UrlToBytes(payload)));
    if (!data.u || Number(data.exp || 0) < Math.floor(Date.now() / 1000)) return null;
    return { username: data.u };
  } catch {
    return null;
  }
}

async function hmac(secret, value) {
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", key, encoder.encode(value));
  return base64Url(sig);
}

async function sha256Hex(value) {
  const buf = await crypto.subtle.digest("SHA-256", encoder.encode(value));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function base64Url(input) {
  const bytes = typeof input === "string" ? encoder.encode(input) : new Uint8Array(input);
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function base64NoPadding(input) {
  const bytes = new Uint8Array(input);
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary).replace(/=+$/g, "");
}

function base64UrlToBytes(value) {
  const padded = value.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (value.length % 4)) % 4);
  const binary = atob(padded);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

async function readJson(request) {
  try {
    return await request.json();
  } catch {
    return {};
  }
}

function json(data, status = 200, extraHeaders = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { ...JSON_HEADERS, ...extraHeaders },
  });
}

function getCookie(request, name) {
  const header = request.headers.get("cookie") || "";
  const pair = header.split(";").map((p) => p.trim()).find((p) => p.startsWith(`${name}=`));
  return pair ? decodeURIComponent(pair.slice(name.length + 1)) : "";
}

function corsHeaders() {
  return {
    ...JSON_HEADERS,
    "access-control-allow-origin": "*",
    "access-control-allow-methods": "GET,POST,PUT,OPTIONS",
    "access-control-allow-headers": "content-type",
  };
}

function makeId(prefix) {
  return `${prefix}_${crypto.randomUUID().replace(/-/g, "").slice(0, 18)}`;
}

function nowIso() {
  return new Date().toISOString();
}

function cleanText(value) {
  return String(value || "").trim();
}

function normalizeMachineId(value) {
  const mid = String(value || "").trim().toUpperCase();
  return /^[A-Z0-9]{16,32}$/.test(mid) ? mid : "";
}

function cleanAccounts(values) {
  const out = [];
  const seen = new Set();
  const input = Array.isArray(values) ? values : String(values || "").split(/[\s,;]+/);
  for (const item of input) {
    const account = String(item || "").trim().toLowerCase();
    if (!account || seen.has(account)) continue;
    seen.add(account);
    out.push(account);
  }
  return out;
}

function normalizeAccount(value) {
  return String(value || "").trim().toLowerCase();
}

function normalizeSafeCode(value) {
  return String(value || "").trim();
}

async function createAccountBindings(values) {
  if (!Array.isArray(values)) throw new Error("安全码绑定格式错误");
  const bindings = [];
  const seen = new Set();
  for (const item of values) {
    const account = normalizeAccount(item?.account);
    const safeCode = normalizeSafeCode(item?.safe_code);
    if (!account || !safeCode) throw new Error("每条安全码绑定都必须填写会员账号和安全码");
    if (safeCode.length > 128) throw new Error("安全码长度不能超过 128 个字符");
    if (seen.has(account)) throw new Error(`会员账号重复：${account}`);
    seen.add(account);
    bindings.push({ account, verifier: await createSafeCodeVerifier(safeCode) });
  }
  return bindings;
}

function safeJsonBindings(value) {
  try {
    const parsed = JSON.parse(value || "[]");
    if (!Array.isArray(parsed)) return [];
    const seen = new Set();
    return parsed.flatMap((item) => {
      const account = normalizeAccount(item?.account);
      const verifier = String(item?.verifier || "");
      if (!account || !verifier || seen.has(account)) return [];
      seen.add(account);
      return [{ account, verifier }];
    });
  } catch {
    return [];
  }
}

async function createSafeCodeVerifier(safeCode) {
  const saltBytes = crypto.getRandomValues(new Uint8Array(16));
  const salt = base64Url(saltBytes);
  const hash = await pbkdf2Bytes(safeCode, saltBytes, 120000);
  return `pbkdf2-sha256-120000.${salt}.${base64Url(hash)}`;
}

async function verifySafeCode(safeCode, verifier) {
  const parts = String(verifier || "").split(".");
  if (parts.length !== 3 || parts[0] !== "pbkdf2-sha256-120000") return false;
  try {
    const expected = base64UrlToBytes(parts[2]);
    const actual = new Uint8Array(await pbkdf2Bytes(safeCode, base64UrlToBytes(parts[1]), 120000));
    if (actual.length !== expected.length) return false;
    let difference = 0;
    for (let i = 0; i < actual.length; i += 1) difference |= actual[i] ^ expected[i];
    return difference === 0;
  } catch {
    return false;
  }
}

async function pbkdf2Bytes(value, salt, iterations) {
  const key = await crypto.subtle.importKey("raw", encoder.encode(value), "PBKDF2", false, ["deriveBits"]);
  return crypto.subtle.deriveBits(
    { name: "PBKDF2", hash: "SHA-256", salt, iterations },
    key,
    256,
  );
}

function normalizeExpiry(value) {
  const raw = String(value || "").trim();
  if (/^\d{8}$/.test(raw)) return raw;
  const m = raw.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  return m ? `${m[1]}${m[2]}${m[3]}` : "";
}

function expiryFromDays(days) {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() + Number(days || 30));
  return `${d.getUTCFullYear()}${String(d.getUTCMonth() + 1).padStart(2, "0")}${String(d.getUTCDate()).padStart(2, "0")}`;
}

function safeJsonArray(value) {
  try {
    const parsed = JSON.parse(value || "[]");
    return cleanAccounts(parsed);
  } catch {
    return [];
  }
}

const INDEX_HTML = `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Jianada 鎺堟潈鍚庡彴</title>
  <link rel="stylesheet" href="/styles.css">
</head>
<body>
  <main id="app"></main>
  <script type="module" src="/app.js"></script>
</body>
</html>`;

