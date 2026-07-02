const BASE = '/api'

async function req(method, path, body) {
  const opts = {
    method,
    headers: { 'Content-Type': 'application/json' },
  }
  if (body) opts.body = JSON.stringify(body)
  const res = await fetch(BASE + path, opts)
  return res.json()
}

export const api = {
  // 授权
  getLicense: () => req('GET', '/license'),
  activate: (key) => req('POST', '/license/activate', { key }),

  // 配置
  getAutoBetConfig: () => req('GET', '/config/autobet'),
  saveAutoBetConfig: (data) => req('POST', '/config/autobet', data),
  getRushBetConfig: () => req('GET', '/config/rushbet'),
  saveRushBetConfig: (data) => req('POST', '/config/rushbet', data),
  getPickBetConfig: () => req('GET', '/config/pickbet'),
  savePickBetConfig: (data) => req('POST', '/config/pickbet', data),
  getFollowBetConfig: () => req('GET', '/config/followbet'),
  saveFollowBetConfig: (data) => req('POST', '/config/followbet', data),

  // 状态
  getStatus: () => req('GET', '/status'),

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
  // keep-alive ping every 20s
  const ping = setInterval(() => { if (ws.readyState === 1) ws.send('ping') }, 20000)
  ws.onclose = () => clearInterval(ping)
  return ws
}
