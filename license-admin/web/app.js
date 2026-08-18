const app = document.querySelector("#app");

const state = {
  authenticated: false,
  demo: false,
  customers: [],
  selectedId: "",
  loading: false,
  message: "",
  generatedLicense: "",
  r2Record: "",
  r2Checks: {},
};

const demoCustomers = [
  {
    id: "cus_demo_001",
    name: "演示客户 A",
    contact: "Telegram: @demo",
    remark: "第一版后台演示数据",
    status: "active",
    updated_at: new Date().toISOString(),
    machines: [
      {
        machine_id: "E67F37167AD7B0BF",
        expiry: "20260916",
        enabled: true,
        accounts: ["ccff11", "ccff22", "ccff33"],
        remark: "当前电脑机器码示例",
        last_synced_at: "2026-08-08T20:27:02+00:00",
      },
    ],
  },
];

async function api(path, options = {}) {
  const res = await fetch(path, {
    credentials: "include",
    headers: { "content-type": "application/json" },
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401 && path !== "/api/login") {
    state.authenticated = false;
    state.customers = [];
    state.selectedId = "";
    state.message = data.message === "Login required"
      ? "\u767b\u5f55\u5df2\u8fc7\u671f\uff0c\u8bf7\u91cd\u65b0\u767b\u5f55"
      : data.message || "\u767b\u5f55\u5df2\u8fc7\u671f\uff0c\u8bf7\u91cd\u65b0\u767b\u5f55";
    render();
    throw new Error(state.message);
  }
  if (!res.ok || data.ok === false) throw new Error(data.message || `HTTP ${res.status}`);
  return data;
}

async function bootstrap() {
  try {
    const session = await api("/api/session");
    state.authenticated = !!session.authenticated;
    if (state.authenticated) await loadCustomers();
  } catch {
    state.demo = true;
    state.authenticated = true;
    state.customers = demoCustomers;
    state.selectedId = demoCustomers[0].id;
  }
  render();
}

async function loadCustomers() {
  if (state.demo) {
    state.customers = demoCustomers;
    state.selectedId ||= demoCustomers[0]?.id || "";
    return;
  }
  const data = await api("/api/customers");
  state.customers = data.customers || [];
  state.selectedId ||= state.customers[0]?.id || "";
}

function selectedCustomer() {
  return state.customers.find((c) => c.id === state.selectedId) || state.customers[0] || null;
}

function setMessage(message) {
  state.message = message;
  render();
}

function rememberR2(machineId, result) {
  const verification = result?.verification || result?.r2?.verification || null;
  if (!verification) return null;
  state.r2Checks[machineId] = verification;
  const record = verification.record || result?.record || null;
  state.r2Record = record ? JSON.stringify(record, null, 2) : "";
  return verification;
}

function r2ResultMessage(result, syncedMessage, syncFailedMessage) {
  const verification = result?.verification || null;
  if (!result) return `${syncedMessage}，但没有返回公开 R2 验证结果`;
  if (result.synced === false) {
    return `${syncFailedMessage}：${verification?.message || "未知原因"}`;
  }
  if (!verification) return `${syncedMessage}，但没有返回公开 R2 验证结果`;
  return verification.ok
    ? `${syncedMessage}，公开 R2 已验证`
    : `${syncedMessage}，公开 R2 验证失败：${verification.message || "未知原因"}`;
}

function renderR2Status(machine) {
  const verification = state.r2Checks[machine.machine_id];
  if (verification?.ok) {
    const time = verification.checked_at ? ` · ${shortTime(verification.checked_at)}` : "";
    return `<div class="muted small">R2：<span class="tag ok">公开 R2 已验证</span>${time}</div>`;
  }
  if (verification) {
    return `<div class="muted small">R2：<span class="tag off">${escapeHtml(verification.message || "公开 R2 验证失败")}</span></div>`;
  }
  if (machine.last_synced_at) {
    return `<div class="muted small">R2：上次同步 ${escapeHtml(shortTime(machine.last_synced_at))}，还未公开验证</div>`;
  }
  return `<div class="muted small">R2：未同步验证</div>`;
}

function shortTime(value) {
  return String(value || "").replace("T", " ").slice(0, 19);
}
function expiryDisplay(expiry) {
  const raw = String(expiry || "");
  if (!/^\d{8}$/.test(raw)) return "未设置";
  return `${raw.slice(0, 4)}-${raw.slice(4, 6)}-${raw.slice(6, 8)}`;
}

function compactExpiry(dateValue) {
  return String(dateValue || "").replaceAll("-", "");
}

function splitAccounts(value) {
  return String(value || "")
    .split(/[\s,，]+/)
    .map((x) => x.trim().toLowerCase())
    .filter(Boolean);
}

function render() {
  if (!state.authenticated) {
    renderLogin();
    return;
  }

  const customer = selectedCustomer();
  app.innerHTML = `
    <div class="shell">
      <aside class="sidebar">
        <div class="brand">
          <div class="brand-mark">JA</div>
          <div>
            <div class="brand-title">Jianada 授权后台</div>
            <div class="brand-subtitle">${state.demo ? "演示模式" : "生产 API"}</div>
          </div>
        </div>
        <div class="nav">
          <button class="active">客户与机器码</button>
          <button disabled>支付订单</button>
          <button disabled>通知记录</button>
          <button disabled>系统设置</button>
        </div>
      </aside>
      <section class="main">
        <div class="topbar">
          <div>
            <h1>授权运营台</h1>
            <div class="muted small">管理客户、机器码、到期时间和账号白名单，生成授权码后同步到 R2。</div>
          </div>
          <button class="btn" data-action="logout">${state.demo ? "退出演示" : "退出"}</button>
        </div>
        ${state.message ? `<div class="notice">${escapeHtml(state.message)}</div>` : ""}
        <div class="grid">
          <div class="panel">
            <div class="panel-header">
              <strong>客户列表</strong>
              <button class="btn primary" data-action="new-customer">新增客户</button>
            </div>
            <div class="panel-body">
              ${renderCustomerList()}
            </div>
          </div>
          <div class="panel">
            <div class="panel-header">
              <strong>${customer ? escapeHtml(customer.name) : "客户详情"}</strong>
              ${customer ? `<span class="tag ${customer.status === "active" ? "ok" : "off"}">${customer.status === "active" ? "启用" : "停用"}</span>` : ""}
            </div>
            <div class="panel-body">
              ${customer ? renderCustomerDetail(customer) : `<div class="empty">还没有客户，先新增一个。</div>`}
            </div>
          </div>
        </div>
      </section>
    </div>
  `;
}

function renderLogin() {
  app.innerHTML = `
    <div class="login">
      <div class="login-card">
        <h1>Jianada 授权后台</h1>
        <div class="muted">管理员登录后管理客户授权和 R2 白名单。</div>
        <form id="login-form">
          <label>用户名<input name="username" value="admin" autocomplete="username"></label>
          <label>密码<input name="password" type="password" autocomplete="current-password"></label>
          <button class="btn primary" type="submit">登录</button>
        </form>
      </div>
    </div>
  `;
}

function renderCustomerList() {
  if (!state.customers.length) return `<div class="empty">暂无客户</div>`;
  return `
    <div class="list">
      ${state.customers.map((c) => `
        <button class="customer-row ${c.id === state.selectedId ? "active" : ""}" data-action="select-customer" data-id="${c.id}">
          <div class="row-title">
            <span>${escapeHtml(c.name)}</span>
            <span class="tag">${c.machines?.length || 0} 台</span>
          </div>
          <div class="muted small">${escapeHtml(c.contact || "无联系方式")}</div>
          <div class="muted small">更新 ${escapeHtml((c.updated_at || "").slice(0, 10))}</div>
        </button>
      `).join("")}
    </div>
  `;
}

function renderCustomerDetail(customer) {
  return `
    <form id="customer-form" class="form-grid">
      <input type="hidden" name="id" value="${escapeAttr(customer.id)}">
      <label>客户名称<input name="name" value="${escapeAttr(customer.name)}"></label>
      <label>状态
        <select name="status">
          <option value="active" ${customer.status === "active" ? "selected" : ""}>启用</option>
          <option value="disabled" ${customer.status === "disabled" ? "selected" : ""}>停用</option>
        </select>
      </label>
      <label class="full">联系方式<input name="contact" value="${escapeAttr(customer.contact || "")}"></label>
      <label class="full">备注<textarea name="remark">${escapeHtml(customer.remark || "")}</textarea></label>
      <div class="actions full">
        <button class="btn primary" type="submit">保存客户</button>
        <button class="btn" type="button" data-action="add-machine">绑定机器码</button>
      </div>
    </form>
    <div style="height:14px"></div>
    <strong>机器码与白名单</strong>
    ${(customer.machines || []).map(renderMachineCard).join("") || `<div class="empty">还没有绑定机器码。</div>`}
    ${state.generatedLicense ? `
      <div style="height:14px"></div>
      <strong>最近生成的授权码</strong>
      <pre class="code">${escapeHtml(state.generatedLicense)}</pre>
    ` : ""}
    ${state.r2Record ? `
      <div style="height:14px"></div>
      <strong>公开 R2 实际 JSON</strong>
      <pre class="code">${escapeHtml(state.r2Record)}</pre>
    ` : ""}
  `;
}

function renderMachineCard(machine) {
  const accountsText = (machine.accounts || []).join(" ");
  const dateValue = /^\d{8}$/.test(machine.expiry || "")
    ? `${machine.expiry.slice(0, 4)}-${machine.expiry.slice(4, 6)}-${machine.expiry.slice(6, 8)}`
    : "";
  return `
    <div class="machine-card">
      <div class="machine-top">
        <div>
          <strong>${escapeHtml(machine.machine_id)}</strong>
          <div class="muted small">到期 ${expiryDisplay(machine.expiry)} · ${machine.accounts?.length || 0} 个账号</div>
        </div>
        <span class="tag ${machine.enabled ? "ok" : "off"}">${machine.enabled ? "启用" : "停用"}</span>
      </div>
      ${renderR2Status(machine)}
      <form class="machine-form form-grid" data-machine="${machine.machine_id}">
        <label>到期日期<input name="expiry" type="date" value="${escapeAttr(dateValue)}"></label>
        <label>状态
          <select name="enabled">
            <option value="true" ${machine.enabled ? "selected" : ""}>启用</option>
            <option value="false" ${!machine.enabled ? "selected" : ""}>停用</option>
          </select>
        </label>
        <label class="full">白名单账号<textarea name="accounts" placeholder="多个账号用空格分开">${escapeHtml(accountsText)}</textarea></label>
        <label class="full">备注<input name="remark" value="${escapeAttr(machine.remark || "")}"></label>
        <div class="actions full">
          <button class="btn primary" type="submit">保存机器码</button>
          <button class="btn" type="button" data-action="license" data-machine="${machine.machine_id}">生成授权码</button>
          <button class="btn" type="button" data-action="sync-r2" data-machine="${machine.machine_id}">同步到 R2</button>
          <button class="btn" type="button" data-action="view-r2" data-machine="${machine.machine_id}">验证公开 R2</button>
        </div>
      </form>
    </div>
  `;
}

document.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (event.target.id === "login-form") {
    const data = Object.fromEntries(new FormData(event.target).entries());
    try {
      await api("/api/login", { method: "POST", body: JSON.stringify(data) });
      state.authenticated = true;
      await loadCustomers();
      setMessage("登录成功");
    } catch (error) {
      setMessage(error.message);
    }
    return;
  }

  if (event.target.id === "customer-form") {
    const data = Object.fromEntries(new FormData(event.target).entries());
    if (state.demo) {
      const c = selectedCustomer();
      Object.assign(c, data, { updated_at: new Date().toISOString() });
      setMessage("演示模式：客户信息已更新");
      return;
    }
    try {
      await api(`/api/customers/${data.id}`, { method: "PUT", body: JSON.stringify(data) });
      await loadCustomers();
      setMessage("客户已保存");
    } catch (error) {
      setMessage(error.message);
    }
    return;
  }

  if (event.target.classList.contains("machine-form")) {
    const machineId = event.target.dataset.machine;
    const form = Object.fromEntries(new FormData(event.target).entries());
    const payload = {
      expiry: compactExpiry(form.expiry),
      enabled: form.enabled === "true",
      accounts: splitAccounts(form.accounts),
      remark: form.remark,
    };
    if (state.demo) {
      const machine = selectedCustomer().machines.find((m) => m.machine_id === machineId);
      Object.assign(machine, payload);
      setMessage("演示模式：机器码已保存");
      return;
    }
    try {
      const data = await api(`/api/machines/${machineId}`, { method: "PUT", body: JSON.stringify(payload) });
      rememberR2(machineId, data.r2);
      await loadCustomers();
      setMessage(r2ResultMessage(data.r2, "机器码已保存并已同步 R2", "机器码已保存，但同步 R2 失败"));
    } catch (error) {
      setMessage(error.message);
    }
  }
});

document.addEventListener("click", async (event) => {
  const target = event.target.closest("[data-action]");
  if (!target) return;
  const action = target.dataset.action;

  if (action === "select-customer") {
    state.selectedId = target.dataset.id;
    state.generatedLicense = "";
    state.r2Record = "";
    render();
  }

  if (action === "new-customer") {
    if (state.demo) {
      const id = `cus_demo_${Date.now()}`;
      state.customers.unshift({
        id,
        name: "新客户",
        contact: "",
        remark: "",
        status: "active",
        updated_at: new Date().toISOString(),
        machines: [],
      });
      state.selectedId = id;
      setMessage("演示模式：已新增客户");
      return;
    }
    const name = prompt("客户名称");
    if (!name) return;
    try {
      const data = await api("/api/customers", { method: "POST", body: JSON.stringify({ name }) });
      await loadCustomers();
      state.selectedId = data.customer.id;
      setMessage("客户已新增");
    } catch (error) {
      setMessage(error.message);
    }
  }

  if (action === "add-machine") {
    const customer = selectedCustomer();
    const machineId = prompt("输入机器码");
    if (!machineId) return;
    if (state.demo) {
      customer.machines.unshift({
        machine_id: machineId.trim().toUpperCase(),
        expiry: "",
        enabled: true,
        accounts: [],
        remark: "",
      });
      setMessage("演示模式：已绑定机器码");
      return;
    }
    try {
      await api(`/api/customers/${customer.id}/machines`, {
        method: "POST",
        body: JSON.stringify({ machine_id: machineId }),
      });
      await loadCustomers();
      setMessage("机器码已绑定");
    } catch (error) {
      setMessage(error.message);
    }
  }

  if (action === "license") {
    const machineId = target.dataset.machine;
    if (state.demo) {
      const machine = selectedCustomer().machines.find((m) => m.machine_id === machineId);
      const expiry = machine.expiry || "20260916";
      state.generatedLicense = `${expiry}.DEMO_SIGNATURE_ONLY_CONFIGURE_CLOUDFLARE_SECRET_FOR_REAL_SIGNING`;
      setMessage("演示模式：已生成示例授权码");
      render();
      return;
    }
    try {
      const data = await api(`/api/machines/${machineId}/license`, { method: "POST", body: JSON.stringify({}) });
      state.generatedLicense = data.license_key;
      setMessage("授权码已生成");
      render();
    } catch (error) {
      setMessage(error.message);
    }
  }

  if (action === "sync-r2") {
    const machineId = target.dataset.machine;
    if (state.demo) {
      setMessage("演示模式：这里会写入 R2 account-whitelist JSON");
      return;
    }
    try {
      const data = await api(`/api/machines/${machineId}/sync-r2`, { method: "POST", body: "{}" });
      rememberR2(machineId, data);
      await loadCustomers();
      setMessage(r2ResultMessage(data, "白名单已同步到 R2", "白名单同步 R2 失败"));
    } catch (error) {
      setMessage(error.message);
    }
  }

  if (action === "view-r2") {
    const machineId = target.dataset.machine;
    if (state.demo) {
      const machine = selectedCustomer().machines.find((m) => m.machine_id === machineId);
      state.r2Record = JSON.stringify({
        machine_id: machine.machine_id,
        customer_id: selectedCustomer().id,
        enabled: machine.enabled,
        accounts: machine.accounts,
        remark: machine.remark,
        updated_at: new Date().toISOString(),
      }, null, 2);
      render();
      return;
    }
    try {
      const data = await api(`/api/machines/${machineId}/r2`);
      const verification = rememberR2(machineId, data);
      setMessage(verification?.message || "公开 R2 已验证");
    } catch (error) {
      setMessage(error.message);
    }
  }

  if (action === "logout") {
    if (!state.demo) await api("/api/logout", { method: "POST", body: "{}" }).catch(() => {});
    state.authenticated = false;
    state.demo = false;
    state.customers = [];
    render();
  }
});

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttr(value) {
  return escapeHtml(value);
}

bootstrap();

