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
  getRushBetConfig: () => req('GET', '/config/rushbet'),
  saveRushBetConfig: (data) => req('POST', '/config/rushbet', data),
  getFollowBetConfig: () => req('GET', '/config/followbet'),
  saveFollowBetConfig: (data) => req('POST', '/config/followbet', data),
  getRotateBetConfig: () => req('GET', '/config/rotatebet'),
  saveRotateBetConfig: (data) => req('POST', '/config/rotatebet', data),
  getCustomRotateBetConfig: () => req('GET', '/config/custom-rotatebet'),
  saveCustomRotateBetConfig: (data) => req('POST', '/config/custom-rotatebet', data),
  analyzeCustomRotateBet: (data) => req('POST', '/custom-rotatebet/analyze', data),
  getCustomWinBetConfig: () => req('GET', '/config/custom-winbet'),
  saveCustomWinBetConfig: (data) => req('POST', '/config/custom-winbet', data),
  analyzeCustomWinBet: (data) => req('POST', '/custom-winbet/analyze', data),
  getMainTrendBetConfig: () => req('GET', '/config/main-trend-bet'),
  saveMainTrendBetConfig: (data) => req('POST', '/config/main-trend-bet', data),
  getFourCodeWinBetConfig: () => req('GET', '/config/four-code-winbet'),
  saveFourCodeWinBetConfig: (data) => req('POST', '/config/four-code-winbet', data),
  analyzeFourCodeWinBet: (data) => req('POST', '/four-code-winbet/analyze', data),

  // 状态
  getStatus: () => req('GET', '/status'),
  analyzeDraws: (data) => req('POST', '/draw-analysis/analyze', data),
  captureDrawRecords: (data) => req('POST', '/draw-analysis/capture', data),
  openDrawAnalysisBrowser: (port, entry_url = '') => req('POST', '/draw-analysis/open-browser', { port: String(port), entry_url }),
  getSavedDrawRecords: (limit = 1000) => req('GET', '/draw-analysis/records?limit=' + encodeURIComponent(limit)),
  clearSavedDrawRecords: () => req('POST', '/draw-analysis/records/clear'),
  getDrawAnalysisSample: (limit = 160) => req('GET', '/draw-analysis/sample?limit=' + encodeURIComponent(limit)),

  // 软件更新
  checkUpdate: () => req('GET', '/update/check'),
  installUpdate: () => req('POST', '/update/install'),
  getUpdateStatus: () => req('GET', '/update/status'),

  // 控制
  startRushBet: () => req('POST', '/rushbet/start'),
  stopRushBet: () => req('POST', '/rushbet/stop'),
  startFollowBet: () => req('POST', '/followbet/start'),
  stopFollowBet: () => req('POST', '/followbet/stop'),
  openFollowerBrowser: (port, entry_url) => req('POST', '/followbet/open-browser', { port: String(port), entry_url: entry_url || '' }),
  startRotateBet: () => req('POST', '/rotatebet/start'),
  stopRotateBet: () => req('POST', '/rotatebet/stop'),
  startCustomRotateBet: () => req('POST', '/custom-rotatebet/start'),
  stopCustomRotateBet: () => req('POST', '/custom-rotatebet/stop'),
  getCustomRotateBetAccountStatuses: () => req('GET', '/custom-rotatebet/accounts/status'),
  stopCustomRotateBetAccount: (key) => req('POST', '/custom-rotatebet/accounts/stop', { key }),
  startCustomWinBet: () => req('POST', '/custom-winbet/start'),
  stopCustomWinBet: () => req('POST', '/custom-winbet/stop'),
  getCustomWinBetAccountStatuses: () => req('GET', '/custom-winbet/accounts/status'),
  stopCustomWinBetAccount: (key) => req('POST', '/custom-winbet/accounts/stop', { key }),
  startMainTrendBet: () => req('POST', '/main-trend-bet/start'),
  stopMainTrendBet: () => req('POST', '/main-trend-bet/stop'),
  getMainTrendBetAccountStatuses: () => req('GET', '/main-trend-bet/accounts/status'),
  stopMainTrendBetAccount: (key) => req('POST', '/main-trend-bet/accounts/stop', { key }),
  startFourCodeWinBet: () => req('POST', '/four-code-winbet/start'),
  stopFourCodeWinBet: () => req('POST', '/four-code-winbet/stop'),
  getFourCodeWinBetAccountStatuses: () => req('GET', '/four-code-winbet/accounts/status'),
  stopFourCodeWinBetAccount: (key) => req('POST', '/four-code-winbet/accounts/stop', { key }),

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
