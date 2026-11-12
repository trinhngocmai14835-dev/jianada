import React, { useEffect, useState } from 'react'
import { Alert, Button, Card, Checkbox, Col, Input, InputNumber, Row, Space, Typography, message } from 'antd'
import { api } from '../api/client'

const { Title, Text } = Typography
const SIDE_KEYS = ['大', '小', '单', '双', '极大', '极小', '大单', '大双', '小单', '小双']

export default function ComboBetPage() {
  const [cfg, setCfg] = useState({ entry_url: 'https://166.tt', safe_code: '', accounts: [{ account: '', password: '', port: 9222 }], main_sums: {}, main_sides: {}, positions: [[], [], []], position_amounts: [100, 100, 100] })
  const [running, setRunning] = useState(false)
  useEffect(() => { api.getComboBetConfig().then(v => v?.entry_url && setCfg(c => ({ ...c, ...v }))); api.getStatus().then(v => setRunning(v?.combo_bet === 'running')) }, [])
  const save = async () => { const r = await api.saveComboBetConfig(cfg); r.ok === false ? message.error(r.message) : message.success('组合投注配置已保存') }
  const start = async () => { await save(); const r = await api.startComboBet(); r.ok ? (setRunning(true), message.success(r.message || '已启动')) : message.error(r.message) }
  const stop = async () => { const r = await api.stopComboBet(); setRunning(false); message.info(r.message || '已停止') }
  const updateSide = (key, value) => setCfg(c => ({ ...c, main_sides: { ...(c.main_sides || {}), [key]: Number(value || 0) } }))
  const toggleSum = (n, checked) => setCfg(c => { const sums = { ...(c.main_sums || {}) }; if (checked) sums[n] = Number(sums[n] || 100); else delete sums[n]; return { ...c, main_sums: sums } })
  const updateSumAmount = (n, value) => setCfg(c => ({ ...c, main_sums: { ...(c.main_sums || {}), [n]: Number(value || 0) } }))
  const updatePos = (i, text) => { const positions = [...(cfg.positions || [[], [], []])]; positions[i] = String(text || '').split(/[,，\s]+/).filter(Boolean).map(Number).filter(n => Number.isInteger(n) && n >= 0 && n <= 9); setCfg(c => ({ ...c, positions })) }
  return <div style={{ padding: 24 }}>
    <Title level={3}>主势 + 1–3 组合投注</Title>
    <Alert type="info" showIcon message="独立玩法：每期先投主势盘和值/两面，再投单球1–3定位；不影响多账号跟投和主势追损。" style={{ marginBottom: 16 }} />
    <Card title="账号与入口" style={{ marginBottom: 16 }}>
      <Row gutter={12}><Col span={8}><Text>入口网址</Text><Input value={cfg.entry_url} onChange={e => setCfg({ ...cfg, entry_url: e.target.value })} /></Col><Col span={8}><Text>安全码</Text><Input value={cfg.safe_code} onChange={e => setCfg({ ...cfg, safe_code: e.target.value })} /></Col><Col span={4}><Text>账号</Text><Input value={cfg.accounts?.[0]?.account} onChange={e => setCfg({ ...cfg, accounts: [{ ...(cfg.accounts?.[0] || {}), account: e.target.value }] })} /></Col><Col span={4}><Text>端口</Text><InputNumber value={cfg.accounts?.[0]?.port} onChange={v => setCfg({ ...cfg, accounts: [{ ...(cfg.accounts?.[0] || {}), port: v }] })} style={{ width: '100%' }} /></Col></Row>
    </Card>
    <Card title="主势盘和值（逐个选择并设置金额）" style={{ marginBottom: 16 }}><Row gutter={[8, 8]}>{Array.from({ length: 28 }, (_, n) => { const selected = Object.prototype.hasOwnProperty.call(cfg.main_sums || {}, n); return <Col xs={12} sm={8} md={6} lg={4} key={n}><Space.Compact style={{ width: '100%' }}><Checkbox checked={selected} onChange={e => toggleSum(n, e.target.checked)} style={{ padding: '6px 8px', border: '1px solid #d9d9d9', borderRight: 0, borderRadius: '6px 0 0 6px' }}>{n}</Checkbox><InputNumber min={1} disabled={!selected} value={selected ? cfg.main_sums[n] : undefined} onChange={v => updateSumAmount(n, v)} style={{ width: '100%' }} /></Space.Compact></Col> })}</Row><Text type="secondary">可同时勾选任意和值；例如勾选 0–5 并分别设置 100 元。</Text></Card>
    <Card title="主势盘两面金额（0表示不投）" style={{ marginBottom: 16 }}><Row gutter={[12, 12]}>{SIDE_KEYS.map(k => <Col span={6} key={k}><Text>{k}</Text><InputNumber min={0} value={cfg.main_sides?.[k] || 0} onChange={v => updateSide(k, v)} style={{ width: '100%' }} /></Col>)}</Row></Card>
    <Card title="单球1–3定位（号码用逗号分隔，各球独立金额）" style={{ marginBottom: 16 }}><Row gutter={12}>{[0, 1, 2].map(i => <Col span={8} key={i}><Text>第{i + 1}球</Text><Input placeholder="例如：0,1,3" value={(cfg.positions?.[i] || []).join(',')} onChange={e => updatePos(i, e.target.value)} /><InputNumber addonBefore="每码" min={1} value={cfg.position_amounts?.[i] || 100} onChange={v => { const a = [...(cfg.position_amounts || [100, 100, 100])]; a[i] = v || 0; setCfg({ ...cfg, position_amounts: a }) }} style={{ width: '100%', marginTop: 8 }} /></Col>)}</Row></Card>
    <Space><Button type="primary" onClick={save}>保存配置</Button>{running ? <Button danger onClick={stop}>停止</Button> : <Button type="primary" onClick={start}>启动组合投注</Button>}</Space>
  </div>
}
