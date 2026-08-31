const BASE = '/api'

async function req(method, path, body) {
  const opts = {
    method,
    headers: { 'Content-Type': 'application/json' },
  }
  if (body) opts.body = JSON.stringify(body)

  try {
    const res = await fetch(BASE + path, opts)
    const text = await res.text()
    let data = {}
    try {
      data = text ? JSON.parse(text) : {}
    } catch {
      data = { ok: false, message: text || `请求失败：HTTP ${res.status}` }
    }
    if (!res.ok) {
      return { ok: false, message: data.message || `请求失败：HTTP ${res.status}` }
    }
    return data
  } catch (err) {
    return { ok: false, message: `请求失败：${err?.message || err}` }
  }
}

export const api = {
  // 授权
  getLicense: () => req('GET', '/license'),
  activate: (key) => req('POST', '/license/activate', { key }),
  getAccountWhitelist: () => req('GET', '/account-whitelist'),

  // 配置
  getAutoBetConfig: () => req('GET', '/config/autobet'),
  saveAutoBetConfig: (data) => req('POST', '/config/autobet', data),
  getRushBetConfig: () => req('GET', '/config/rushbet'),
  saveRushBetConfig: (data) => req('POST', '/config/rushbet', data),
  getPickBetConfig: () => req('GET', '/config/pickbet'),
  savePickBetConfig: (data) => req('POST', '/config/pickbet', data),
  getFollowBetConfig: () => req('GET', '/config/followbet'),
  saveFollowBetConfig: (data) => req('POST', '/config/followbet', data),
  getRotateBetConfig: () => req('GET', '/config/rotatebet'),
  saveRotateBetConfig: (data) => req('POST', '/config/rotatebet', data),
  getCustomRotateBetConfig: () => req('GET', '/config/custom-rotatebet'),
  saveCustomRotateBetConfig: (data) => req('POST', '/config/custom-rotatebet', data),

  // 状态
  getStatus: () => req('GET', '/status'),

  // 软件更新
  checkUpdate: () => req('GET', '/update/check'),
  installUpdate: () => req('POST', '/update/install'),
  getUpdateStatus: () => req('GET', '/update/status'),

  // 控制
  startAutoBet: () => req('POST', '/autobet/start'),
  stopAutoBet: () => req('POST', '/autobet/stop'),
  startRushBet: () => req('POST', '/rushbet/start'),
  stopRushBet: () => req('POST', '/rushbet/stop'),
  startPickBet: () => req('POST', '/pickbet/start'),
  stopPickBet: () => req('POST', '/pickbet/stop'),
  startFollowBet: () => req('POST', '/followbet/start'),
  stopFollowBet: () => req('POST', '/followbet/stop'),
  openFollowerBrowser: (port, entry_url) => req('POST', '/followbet/open-browser', { port: String(port), entry_url: entry_url || '' }),
  startRotateBet: () => req('POST', '/rotatebet/start'),
  stopRotateBet: () => req('POST', '/rotatebet/stop'),
  startCustomRotateBet: () => req('POST', '/custom-rotatebet/start'),
  stopCustomRotateBet: () => req('POST', '/custom-rotatebet/stop'),
  getCustomRotateBetAccountStatuses: () => req('GET', '/custom-rotatebet/accounts/status'),
  stopCustomRotateBetAccount: (key) => req('POST', '/custom-rotatebet/accounts/stop', { key }),

  // 投注流水
  getFlow: (account = '', mode = '', limit = 800) =>
    req('GET', `/flow?account=${encodeURIComponent(account)}&mode=${encodeURIComponent(mode)}&limit=${limit}`),
  getFlowAccounts: () => req('GET', '/flow/accounts'),
  clearFlow: (account = '', days = 0) => req('POST', '/flow/clear', { account, days }),
}

export function createLogSocket(taskId, onMessage) {
  const wsBase = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const ws = new WebSocket(`${wsBase}//${location.host}/ws/logs/${taskId}`)
  ws.onmessage = (e) => {
    try {
      onMessage(JSON.parse(e.data))
    } catch {}
  }
  const ping = setInterval(() => { if (ws.readyState === 1) ws.send('ping') }, 20000)
  ws.onclose = () => clearInterval(ping)
  return ws
}
