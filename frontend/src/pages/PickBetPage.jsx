import React, { useEffect, useState } from 'react'
import {
  Card, Form, Input, InputNumber, Button, Space, Typography, Divider,
  Row, Col, message, Tag, Collapse,
} from 'antd'
import { PlusOutlined, MinusCircleOutlined, PlayCircleOutlined, PauseCircleOutlined } from '@ant-design/icons'
import { api } from '../api/client'
import LogViewer from '../components/LogViewer'

const { Title, Text } = Typography
const { Panel } = Collapse

const STATUS_TAG = {
  running: <Tag color="green">运行中</Tag>,
  stopping: <Tag color="orange">停止中</Tag>,
  stopped: <Tag color="default">已停止</Tag>,
}

const MIN_PER_POS = 1   // 每路至少选 1 个号（勾几个用几个，上不封顶）
const ALL_NUMBERS = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
const DEFAULTS = {
  pos_numbers: [
    [0, 1, 2, 3, 4, 5],
    [0, 1, 2, 3, 4, 5],
    [0, 1, 2, 3, 4, 5],
  ],
  base_bet_amount: 500,
  rush_bet_amount: 700,
}

// 三路号码选择器：每路从 0~9 里任意勾选（勾几个用几个，至少 1 个）
function NumberPicker({ value = [], onChange }) {
  const toggle = (pos, n) => {
    const cur = Array.isArray(value[pos]) ? value[pos] : []
    let next
    if (cur.includes(n)) {
      next = cur.filter((x) => x !== n)
    } else {
      next = [...cur, n].sort((a, b) => a - b)
    }
    const all = [0, 1, 2].map((p) => (p === pos ? next : (Array.isArray(value[p]) ? value[p] : [])))
    onChange?.(all)
  }

  return (
    <div>
      {[0, 1, 2].map((pos) => {
        const cur = Array.isArray(value[pos]) ? value[pos] : []
        const ok = cur.length >= MIN_PER_POS
        return (
          <div key={pos} style={{ marginBottom: 12 }}>
            <Space style={{ marginBottom: 6 }}>
              <Text strong>球{pos + 1}</Text>
              <Text type={ok ? 'success' : 'danger'} style={{ fontSize: 12 }}>
                已选 {cur.length} 个{ok ? '' : '（至少选 1 个）'}
              </Text>
            </Space>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {ALL_NUMBERS.map((n) => {
                const sel = cur.includes(n)
                return (
                  <Button
                    key={n}
                    size="small"
                    type={sel ? 'primary' : 'default'}
                    onClick={() => toggle(pos, n)}
                    style={{
                      width: 36,
                      ...(sel ? { background: '#fa8c16', borderColor: '#fa8c16' } : {}),
                    }}
                  >
                    {n}
                  </Button>
                )
              })}
            </div>
          </div>
        )
      })}
    </div>
  )
}

export default function PickBetPage() {
  const [form] = Form.useForm()
  const [status, setStatus] = useState('stopped')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)

  const refresh = async () => {
    const s = await api.getStatus()
    setStatus(s.pickbet || 'stopped')
  }

  useEffect(() => {
    api.getPickBetConfig().then((cfg) => form.setFieldsValue({ ...DEFAULTS, ...cfg }))
    refresh()
    const t = setInterval(refresh, 3000)
    return () => clearInterval(t)
  }, [])

  // 校验三路各自至少选 1 个号（勾几个用几个）
  const validatePicks = () => {
    const picks = form.getFieldValue('pos_numbers') || []
    for (let i = 0; i < 3; i++) {
      if (!Array.isArray(picks[i]) || picks[i].length < MIN_PER_POS) {
        message.error(`球${i + 1} 至少选 1 个号`)
        return false
      }
    }
    return true
  }

  const handleSave = async () => {
    try {
      const vals = await form.validateFields()
      if (!validatePicks()) return false
      setSaving(true)
      await api.savePickBetConfig(vals)
      message.success('配置已保存')
      return true
    } catch {
      message.error('请检查表单填写')
      return false
    } finally {
      setSaving(false)
    }
  }

  const handleStart = async () => {
    const ok = await handleSave()
    if (!ok) return
    setLoading(true)
    const res = await api.startPickBet()
    message.info(res.message)
    setLoading(false)
    if (res.ok) setStatus('running')
    refresh()
  }

  const handleStop = async () => {
    setLoading(true)
    const res = await api.stopPickBet()
    message.info(res.message)
    setLoading(false)
    refresh()
  }

  const isRunning = status === 'running'

  return (
    <div style={{ padding: 24 }}>
      <Space style={{ marginBottom: 24, width: '100%', justifyContent: 'space-between' }} align="center">
        <Space>
          <Title level={4} style={{ margin: 0 }}>自选号·赢冲输缩 — 3路自选</Title>
          {STATUS_TAG[status]}
        </Space>
        <Space>
          <Button icon={<PlayCircleOutlined />} type="primary" onClick={handleStart} loading={loading} disabled={isRunning}
            style={{ background: '#fa8c16', borderColor: '#fa8c16' }}>
            保存并启动
          </Button>
          <Button icon={<PauseCircleOutlined />} danger onClick={handleStop} loading={loading} disabled={!isRunning}>
            停止
          </Button>
        </Space>
      </Space>

      <Row gutter={24}>
        <Col xs={24} lg={12}>
          <Card title="参数配置" style={{ borderRadius: 12, marginBottom: 24 }}>
            <Form form={form} layout="vertical">
              <Collapse defaultActiveKey={['basic', 'accounts', 'strategy']} ghost forceRender>
                <Panel header="🌐 网站设置" key="basic">
                  <Form.Item label="入口网址" name="entry_url" rules={[{ required: true }]}>
                    <Input placeholder="https://166.tt" />
                  </Form.Item>
                  <Form.Item label="安全码" name="safe_code" rules={[{ required: true }]}>
                    <Input placeholder="88361" />
                  </Form.Item>
                </Panel>

                <Panel header="👤 账号列表" key="accounts">
                  <Form.List name="accounts">
                    {(fields, { add, remove }) => (
                      <>
                        {fields.map(({ key, name }) => (
                          <Card size="small" key={key} style={{ marginBottom: 8, background: '#fafafa' }}
                            extra={fields.length > 1 && (
                              <MinusCircleOutlined onClick={() => remove(name)} style={{ color: 'red' }} />
                            )}>
                            <Row gutter={8}>
                              <Col span={8}>
                                <Form.Item name={[name, 'account']} label="账号" rules={[{ required: true }]}>
                                  <Input placeholder="luban001" />
                                </Form.Item>
                              </Col>
                              <Col span={8}>
                                <Form.Item name={[name, 'password']} label="密码" rules={[{ required: true }]}>
                                  <Input.Password placeholder="密码" />
                                </Form.Item>
                              </Col>
                              <Col span={8}>
                                <Form.Item name={[name, 'port']} label="端口" rules={[{ required: true }]}>
                                  <InputNumber style={{ width: '100%' }} placeholder="9222" />
                                </Form.Item>
                              </Col>
                            </Row>
                          </Card>
                        ))}
                        <Button type="dashed" onClick={() => {
                          const existing = form.getFieldValue('accounts') || []
                          const ports = existing.map((a) => Number(a?.port)).filter((p) => !Number.isNaN(p))
                          const nextPort = ports.length ? Math.max(...ports) + 1 : 9222
                          add({ account: '', password: '', port: nextPort })
                        }} block icon={<PlusOutlined />}>
                          添加账号
                        </Button>
                      </>
                    )}
                  </Form.List>
                </Panel>

                <Panel header="🎯 选号 & 注码" key="strategy">
                  <div style={{
                    background: '#fff7e6', border: '1px solid #ffd591', borderRadius: 8,
                    padding: '12px 14px', marginBottom: 16,
                  }}>
                    <Text strong style={{ color: '#d46b08' }}>三路自选号（每路点选号码，勾几个用几个，至少 1 个）</Text>
                    <div style={{ marginTop: 12 }}>
                      <Form.Item name="pos_numbers" noStyle rules={[{ required: true }]}>
                        <NumberPicker />
                      </Form.Item>
                    </div>
                  </div>

                  <Row gutter={16}>
                    <Col span={12}>
                      <Form.Item label="一阶底注 (元)" name="base_bet_amount" tooltip="首次或输后的注码"
                        rules={[{ required: true }]}>
                        <InputNumber style={{ width: '100%' }} min={1} />
                      </Form.Item>
                    </Col>
                    <Col span={12}>
                      <Form.Item label="二阶赢冲 (元)" name="rush_bet_amount" tooltip="命中一次后升阶使用的注码"
                        rules={[{ required: true }]}>
                        <InputNumber style={{ width: '100%' }} min={1} />
                      </Form.Item>
                    </Col>
                  </Row>
                  <div style={{ color: '#614700', fontSize: 12, marginBottom: 12, lineHeight: 1.5 }}>
                    每球独立赢冲输缩：中→升二阶 / 不中→回一阶
                  </div>

                  <Row gutter={16}>
                    <Col span={12}>
                      <Form.Item label="止盈 (元)" name="take_profit">
                        <InputNumber style={{ width: '100%' }} min={0} />
                      </Form.Item>
                    </Col>
                    <Col span={12}>
                      <Form.Item label="止损 (元)" name="daily_stop_loss">
                        <InputNumber style={{ width: '100%' }} min={0} />
                      </Form.Item>
                    </Col>
                    <Col span={12}>
                      <Form.Item label="赔率" name="odds">
                        <InputNumber style={{ width: '100%' }} step={0.01} />
                      </Form.Item>
                    </Col>
                    <Col span={12}>
                      <Form.Item label="退水比例" name="rebate_rate">
                        <InputNumber style={{ width: '100%' }} step={0.0001} />
                      </Form.Item>
                    </Col>
                  </Row>
                </Panel>
              </Collapse>

              <Divider />
              <Button onClick={handleSave} loading={saving} block>
                仅保存配置
              </Button>
            </Form>
          </Card>
        </Col>

        <Col xs={24} lg={12}>
          <Card title="实时日志" style={{ borderRadius: 12 }}>
            <LogViewer taskId="pickbet" running={isRunning} />
          </Card>
        </Col>
      </Row>
    </div>
  )
}
