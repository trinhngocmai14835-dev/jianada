import React, { useEffect, useState } from 'react'
import { Alert, Button, Card, Checkbox, Col, Input, InputNumber, Radio, Row, Space, Typography, message } from 'antd'
import { api } from '../api/client'
import LogViewer from '../components/LogViewer'

const { Title, Text } = Typography
const SIDE_KEYS = ['大', '小', '单', '双']

export default function ComboBetPage() {
  const [cfg, setCfg] = useState({ entry_url: 'https://166.tt', safe_code: '', accounts: [{ account: '', password: '', port: 9222 }], main_sums: {}, main_sides: {}, positions: [[], [], []], position_amounts: [100, 100, 100] })
  const [running, setRunning] = useState(false)
  useEffect(() => { api.getComboBetConfig().then(v => v?.entry_url && setCfg(c => ({ ...c, ...v }))); const refresh = () => api.getStatus().then(v => setRunning(v?.combo_bet === 'running')); refresh(); const timer = setInterval(refresh, 3000); return () => clearInterval(timer) }, [])
  const save = async () => { const r = await api.saveComboBetConfig(cfg); r.ok === false ? message.error(r.message) : message.success('组合投注配置已保存') }
  const start = async () => { await save(); const r = await api.startComboBet(); r.ok ? (setRunning(true), message.success(r.message || '已启动')) : message.error(r.message) }
  const stop = async () => { const r = await api.stopComboBet(); setRunning(false); message.info(r.message || '已停止') }
  const updateSide = (key, value) => setCfg(c => ({ ...c, main_sides: { ...(c.main_sides || {}), [key]: Number(value || 0) } }))
  const toggleSum = (n, checked) => setCfg(c => { const sums = { ...(c.main_sums || {}) }; if (checked) sums[n] = Number(sums[n] || 100); else delete sums[n]; return { ...c, main_sums: sums } })
  const updateSumAmount = (n, value) => setCfg(c => ({ ...c, main_sums: { ...(c.main_sums || {}), [n]: Number(value || 0) } }))
  const updatePos = (i, text) => { const positions = [...(cfg.positions || [[], [], []])]; positions[i] = String(text || '').split(/[,，\s]+/).filter(Boolean).map(Number).filter(n => Number.isInteger(n) && n >= 0 && n <= 9); setCfg(c => ({ ...c, positions })) }
  const updateAccount = (i, key, value) => setCfg(c => { const accounts = [...(c.accounts || [])]; accounts[i] = { ...(accounts[i] || {}), [key]: value }; return { ...c, accounts } })
  const addAccount = () => setCfg(c => ({ ...c, accounts: [...(c.accounts || []), { account: '', password: '', port: 9222 + (c.accounts || []).length }] }))
  const removeAccount = (i) => setCfg(c => ({ ...c, accounts: (c.accounts || []).filter((_, idx) => idx !== i) }))
  return <div style={{ padding: 24 }}>
    <Title level={3}>主势 + 1–3 组合投注</Title>
    <Alert type="info" showIcon message="独立玩法：每期先投主势盘和值/两面，再投单球1–3定位；不影响多账号跟投和主势追损。运行中保存配置后，下一期自动生效，不需要关闭账号或重新登录。" style={{ marginBottom: 16 }} />
    <Card title="账号与入口（支持多个账号同时运行）" style={{ marginBottom: 16 }}>
      <Row gutter={12} style={{ marginBottom: 12 }}><Col span={12}><Text>入口网址</Text><Input value={cfg.entry_url} onChange={e => setCfg({ ...cfg, entry_url: e.target.value })} /></Col><Col span={12}><Text>安全码</Text><Input value={cfg.safe_code} onChange={e => setCfg({ ...cfg, safe_code: e.target.value })} /></Col></Row>
      {(cfg.accounts || []).map((acc, i) => <Row gutter={8} key={i} align="middle" style={{ marginBottom: 8 }}><Col span={6}><Input addonBefore={`账号${i + 1}`} value={acc.account} onChange={e => updateAccount(i, 'account', e.target.value)} /></Col><Col span={6}><Input.Password placeholder="密码" value={acc.password} onChange={e => updateAccount(i, 'password', e.target.value)} /></Col><Col span={5}><InputNumber addonBefore="端口" min={1024} value={acc.port} onChange={v => updateAccount(i, 'port', v)} style={{ width: '100%' }} /></Col><Col span={4}><Text type="secondary">独立浏览器</Text></Col><Col span={3}><Button danger disabled={(cfg.accounts || []).length <= 1} onClick={() => removeAccount(i)}>删除</Button></Col></Row>)}
      <Button type="dashed" block onClick={addAccount}>添加账号</Button>
    </Card>
    <Card title="启动方式" style={{ marginBottom: 16 }}><Radio.Group value={cfg.start_mode || 'now'} onChange={e => setCfg({ ...cfg, start_mode: e.target.value })}><Radio value="now">立即启动</Radio><Radio value="scheduled">定时启动</Radio></Radio.Group>{cfg.start_mode === 'scheduled' && <Input style={{ width: 140, marginLeft: 16 }} placeholder="HH:MM，例如09:00" value={cfg.start_time || '09:00'} onChange={e => setCfg({ ...cfg, start_time: e.target.value })} />}<Text type="secondary" style={{ marginLeft: 16 }}>定时模式会先登录并保持待机，到指定时间同时开始下注。</Text></Card>
    <Card title="主势盘和值（逐个选择并设置金额）" style={{ marginBottom: 16 }}><Row gutter={[8, 8]}>{Array.from({ length: 28 }, (_, n) => { const selected = Object.prototype.hasOwnProperty.call(cfg.main_sums || {}, n); return <Col xs={12} sm={8} md={6} lg={4} key={n}><Space.Compact style={{ width: '100%' }}><Checkbox checked={selected} onChange={e => toggleSum(n, e.target.checked)} style={{ padding: '6px 8px', border: '1px solid #d9d9d9', borderRight: 0, borderRadius: '6px 0 0 6px' }}>{n}</Checkbox><InputNumber min={1} disabled={!selected} value={selected ? cfg.main_sums[n] : undefined} onChange={v => updateSumAmount(n, v)} style={{ width: '100%' }} /></Space.Compact></Col> })}</Row><Text type="secondary">可同时勾选任意和值；例如勾选 0–5 并分别设置 100 元。</Text></Card>
    <Card title="主势盘两面金额（0表示不投）" style={{ marginBottom: 16 }}><Row gutter={[12, 12]}>{SIDE_KEYS.map(k => <Col span={6} key={k}><Text>{k}</Text><InputNumber min={0} value={cfg.main_sides?.[k] || 0} onChange={v => updateSide(k, v)} style={{ width: '100%' }} /></Col>)}</Row></Card>
    <Card title="单球1–3定位（号码用逗号分隔，各球独立金额）" style={{ marginBottom: 16 }}><Row gutter={12}>{[0, 1, 2].map(i => <Col span={8} key={i}><Text>第{i + 1}球</Text><Input placeholder="例如：0,1,3" value={(cfg.positions?.[i] || []).join(',')} onChange={e => updatePos(i, e.target.value)} /><InputNumber addonBefore="每码" min={1} value={cfg.position_amounts?.[i] || 100} onChange={v => { const a = [...(cfg.position_amounts || [100, 100, 100])]; a[i] = v || 0; setCfg({ ...cfg, position_amounts: a }) }} style={{ width: '100%', marginTop: 8 }} /></Col>)}</Row></Card>
    <Space><Button type="primary" onClick={save}>保存配置</Button>{running ? <Button danger onClick={stop}>停止</Button> : <Button type="primary" onClick={start}>启动组合投注</Button>}</Space>
    <Card title="运行日志" style={{ marginTop: 16 }}><LogViewer taskId="combo_bet" running={running} /></Card>
  </div>
}
