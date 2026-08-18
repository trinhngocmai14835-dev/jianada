import React, { useEffect, useState } from 'react'
import {
  Card, Form, Input, InputNumber, Button, Space, Typography, Divider,
  Row, Col, message, Tag, Collapse, Radio, Modal, Switch,
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

const BALL_LABELS = ['第一球', '第二球', '第三球']

// 与后端 _next_alarm 同一套规则：今天的 HH:MM，已过则顺延次日。
const nextAlarm = (hhmm) => {
  const m = /^\s*(\d{1,2})\s*:\s*(\d{1,2})\s*$/.exec(hhmm || '')
  if (!m) return null
  const h = Number(m[1])
  const mi = Number(m[2])
  if (h > 23 || mi > 59) return null
  const t = new Date()
  t.setHours(h, mi, 0, 0)
  if (t <= new Date()) t.setDate(t.getDate() + 1)
  return t
}

// 把后端数组格式 [0,1,3,5,8] 转成表单字符串 "0,1,3,5,8"
function cfgToForm(cfg) {
  const ns = (cfg.number_sets || []).map((s) => ({
    set_a: (s.set_a || []).join(','),
    set_b: (s.set_b || []).join(','),
  }))
  // 补齐三路
  while (ns.length < 3) ns.push({ set_a: '0,1,3,5,8', set_b: '2,4,6,7,9' })
  const enabledPositions = Array.from(
    { length: 3 },
    (_, i) => (cfg.enabled_positions || [true, true, true])[i] !== false,
  )
  return { ...cfg, number_sets: ns, enabled_positions: enabledPositions }
}

// 把表单字符串 "0,1,3,5,8" 转回数组，过滤非法字符
function parseNums(str) {
  return String(str || '')
    .split(',')
    .map((s) => s.trim())
    .filter((s) => s !== '')
    .map(Number)
    .filter((n) => !Number.isNaN(n) && n >= 0 && n <= 9)
}

function normalizeEnabledPositions(vals) {
  return Array.from(
    { length: 3 },
    (_, i) => (vals.enabled_positions || [true, true, true])[i] !== false,
  )
}

function formToCfg(vals) {
  return {
    ...vals,
    enabled_positions: normalizeEnabledPositions(vals),
    number_sets: (vals.number_sets || []).map((s) => ({
      set_a: parseNums(s.set_a),
      set_b: parseNums(s.set_b),
    })),
  }
}

export default function RotateBetPage() {
  const [form] = Form.useForm()
  const startMode = Form.useWatch('start_mode', form) || 'now'
  const [status, setStatus] = useState('stopped')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)

  const refresh = async () => {
    const s = await api.getStatus()
    setStatus(s.rotatebet || 'stopped')
  }

  useEffect(() => {
    api.getRotateBetConfig().then((cfg) => form.setFieldsValue(cfgToForm(cfg)))
    refresh()
    const t = setInterval(refresh, 3000)
    return () => clearInterval(t)
  }, [])

  const handleSave = async () => {
    try {
      const vals = await form.validateFields()
      const cfg = formToCfg(vals)
      if (!cfg.enabled_positions.some(Boolean)) {
        message.error('至少启用一路球')
        return false
      }
      setSaving(true)
      await api.saveRotateBetConfig(cfg)
      message.success('配置已保存')
      return true
    } catch {
      message.error('请检查表单填写')
      return false
    } finally {
      setSaving(false)
    }
  }

  const doStart = async () => {
    setLoading(true)
    const res = await api.startRotateBet()
    message.info(res.message)
    setLoading(false)
    if (res.ok) setStatus('running')
    refresh()
  }

  const handleStart = async () => {
    if (!(await handleSave())) return

    if ((form.getFieldValue('start_mode') || 'now') !== 'scheduled') {
      doStart()
      return
    }

    const t = nextAlarm(form.getFieldValue('start_time'))
    if (!t) {
      message.error('开跑时刻格式不对，应为 08:00')
      return
    }
    const mins = Math.round((t - new Date()) / 60000)
    const span = mins >= 60 ? `${Math.floor(mins / 60)} 小时 ${mins % 60} 分后` : `${mins} 分钟后`
    const day = t.getDate() === new Date().getDate() ? '今天' : `明天(${t.getMonth() + 1}-${t.getDate()})`
    const hhmm = `${String(t.getHours()).padStart(2, '0')}:${String(t.getMinutes()).padStart(2, '0')}`
    Modal.confirm({
      title: '确认闹钟定时启动？',
      content: (
        <div>
          <p style={{ marginBottom: 8 }}>
            将于 <b style={{ color: '#fa8c16' }}>{day} {hhmm}</b> 开始下注（约 {span}）。
          </p>
          <p style={{ margin: 0, color: '#888', fontSize: 12 }}>
            点确认后浏览器会立即打开并登录，登录完待机，到点自动开投。
          </p>
        </div>
      ),
      okText: '确认启动',
      cancelText: '取消',
      onOk: doStart,
    })
  }

  const handleStop = async () => {
    setLoading(true)
    const res = await api.stopRotateBet()
    message.info(res.message)
    setLoading(false)
    refresh()
  }

  const isRunning = status === 'running'

  return (
    <div style={{ padding: 24 }}>
      <Space style={{ marginBottom: 24, width: '100%', justifyContent: 'space-between' }} align="center">
        <Space>
          <Title level={4} style={{ margin: 0 }}>轮换追损 — 3路双组轮替</Title>
          {STATUS_TAG[status] ?? STATUS_TAG.stopped}
        </Space>
        <Space>
          <Button
            icon={<PlayCircleOutlined />}
            type="primary"
            onClick={handleStart}
            loading={loading}
            disabled={isRunning}
            style={{ background: '#722ed1', borderColor: '#722ed1' }}
          >
            {startMode === 'scheduled' ? '保存并定时启动' : '保存并启动'}
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
              <Collapse defaultActiveKey={['basic', 'start', 'accounts', 'numbers', 'chase', 'risk']} ghost forceRender>

                {/* ── 网站设置 ── */}
                <Panel header="🌐 网站设置" key="basic">
                  <Form.Item label="入口网址" name="entry_url" rules={[{ required: true }]}>
                    <Input placeholder="https://166.tt" />
                  </Form.Item>
                  <Form.Item label="安全码" name="safe_code" rules={[{ required: true }]}>
                    <Input placeholder="88361" />
                  </Form.Item>
                </Panel>

                {/* ── 账号列表 ── */}
                <Panel header="👤 账号列表" key="accounts">
                  <Form.List name="accounts">
                    {(fields, { add, remove }) => (
                      <>
                        {fields.map(({ key, name }) => (
                          <Card
                            size="small"
                            key={key}
                            style={{ marginBottom: 8, background: '#fafafa' }}
                            extra={
                              fields.length > 1 && (
                                <MinusCircleOutlined onClick={() => remove(name)} style={{ color: 'red' }} />
                              )
                            }
                          >
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
                        <Button
                          type="dashed"
                          onClick={() => {
                            const existing = form.getFieldValue('accounts') || []
                            const ports = existing.map((a) => Number(a?.port)).filter((p) => !Number.isNaN(p))
                            const nextPort = ports.length ? Math.max(...ports) + 1 : 9222
                            add({ account: '', password: '', port: nextPort })
                          }}
                          block
                          icon={<PlusOutlined />}
                        >
                          添加账号
                        </Button>
                      </>
                    )}
                  </Form.List>
                </Panel>

                {/* ── 轮换号码 ── */}
                <Panel header="🔄 轮换号码（每路两组，每把交替）" key="numbers">
                  <div style={{
                    background: '#f6f0ff', border: '1px solid #d3adf7', borderRadius: 8,
                    padding: '10px 14px', marginBottom: 12,
                  }}>
                    <Text style={{ fontSize: 12, color: '#531dab' }}>
                      每路填两组号码（0~9，逗号分隔，每把自动交替）。<br />
                      示例：A组填 <b>0,1,3,5,8</b>，B组填 <b>2,4,6,7,9</b>
                    </Text>
                  </div>
                  {BALL_LABELS.map((label, i) => (
                    <div key={i} style={{
                      background: '#fafafa', border: '1px solid #f0f0f0',
                      borderRadius: 8, padding: '10px 14px', marginBottom: 10,
                    }}>
                      <Text strong style={{ fontSize: 13 }}>{label}</Text>
                      <Row gutter={12} style={{ marginTop: 8 }}>
                        <Col span={12}>
                          <Form.Item
                            name={['number_sets', i, 'set_a']}
                            label={<Text style={{ color: '#1677ff' }}>A组号码</Text>}
                            rules={[{
                              required: true,
                              validator: (_, v) => {
                                const nums = parseNums(v)
                                return nums.length > 0 ? Promise.resolve() : Promise.reject('至少填1个有效号码(0~9)')
                              },
                            }]}
                            style={{ marginBottom: 0 }}
                          >
                            <Input placeholder="0,1,3,5,8" />
                          </Form.Item>
                        </Col>
                        <Col span={12}>
                          <Form.Item
                            name={['number_sets', i, 'set_b']}
                            label={<Text style={{ color: '#fa541c' }}>B组号码</Text>}
                            rules={[{
                              required: true,
                              validator: (_, v) => {
                                const nums = parseNums(v)
                                return nums.length > 0 ? Promise.resolve() : Promise.reject('至少填1个有效号码(0~9)')
                              },
                            }]}
                            style={{ marginBottom: 0 }}
                          >
                            <Input placeholder="2,4,6,7,9" />
                          </Form.Item>
                        </Col>
                      </Row>
                    </div>
                  ))}
                </Panel>

                {/* ── 追损参数 ── */}
                <Panel header="📈 追损参数" key="chase">
                  <div style={{
                    background: '#fff7e6', border: '1px solid #ffd591', borderRadius: 8,
                    padding: '10px 14px', marginBottom: 12, fontSize: 12, color: '#874d00',
                  }}>
                    启动后每一路先独立观察，达到设定的连续未中次数后才开始实投。命中→本路追损状态清零，下把回到底注。
                    实投未中→下把注码 = 本轮已投总额 × 追损倍率；连续未中满最大把数→重置到底注。
                  </div>
                  <Form.Item
                    label="入场触发条件"
                    name="entry_miss_trigger"
                    initialValue={1}
                    tooltip="三路球单独观察；选择直接开始则立即投注，否则某一路连续未中达到设置次数后才开始真实投注"
                  >
                    <Radio.Group optionType="button" buttonStyle="solid">
                      <Radio.Button value={0}>直接开始</Radio.Button>
                      <Radio.Button value={1}>一次不中</Radio.Button>
                      <Radio.Button value={2}>两次不中</Radio.Button>
                      <Radio.Button value={3}>三次不中</Radio.Button>
                    </Radio.Group>
                  </Form.Item>
                  <Form.Item
                    label="启用球路"
                    tooltip="关闭某一路后，该路不观察、不下注、不参与追损"
                    style={{ marginBottom: 12 }}
                  >
                    <Space wrap>
                      {BALL_LABELS.map((label, i) => (
                        <Space key={label} size={8} style={{ marginBottom: 8 }}>
                          <Text>{label}</Text>
                          <Form.Item
                            name={['enabled_positions', i]}
                            valuePropName="checked"
                            initialValue={true}
                            noStyle
                          >
                            <Switch checkedChildren="开" unCheckedChildren="关" />
                          </Form.Item>
                        </Space>
                      ))}
                    </Space>
                  </Form.Item>
                  <Row gutter={16}>
                    <Col span={8}>
                      <Form.Item label="一阶底注 (元)" name="base_bet_amount" rules={[{ required: true }]}>
                        <InputNumber style={{ width: '100%' }} min={1} />
                      </Form.Item>
                    </Col>
                    <Col span={8}>
                      <Form.Item
                        label="追损倍率"
                        name="loss_multiplier"
                        tooltip="未中后：下注额 = 本轮已投总额 × 此倍率"
                        rules={[{ required: true }]}
                      >
                        <InputNumber style={{ width: '100%' }} step={0.1} min={1.0} />
                      </Form.Item>
                    </Col>
                    <Col span={8}>
                      <Form.Item
                        label="最大追损把数"
                        name="max_losses"
                        tooltip="连续未中达此把数后重置为一阶底注"
                        rules={[{ required: true }]}
                      >
                        <InputNumber style={{ width: '100%' }} min={1} max={20} />
                      </Form.Item>
                    </Col>
                  </Row>
                </Panel>

                {/* ── 启动方式 ── */}
                <Panel header="⏰ 启动方式" key="start">
                  <Form.Item label="启动方式" name="start_mode" initialValue="now" style={{ marginBottom: 12 }}>
                    <Radio.Group optionType="button" buttonStyle="solid">
                      <Radio.Button value="now">随开随跑</Radio.Button>
                      <Radio.Button value="scheduled">闹钟定时</Radio.Button>
                    </Radio.Group>
                  </Form.Item>
                  {startMode === 'scheduled' ? (
                    <>
                      <Form.Item label="开跑时刻" name="start_time" style={{ marginBottom: 8 }}
                        rules={[{ required: true, pattern: /^([01]?\d|2[0-3]):[0-5]\d$/, message: '格式如 08:00' }]}>
                        <Input placeholder="08:00" style={{ width: 160 }} />
                      </Form.Item>
                      <div style={{ color: '#614700', fontSize: 12, lineHeight: 1.6, background: '#fffbe6',
                        border: '1px solid #ffe58f', borderRadius: 6, padding: '6px 10px' }}>
                        点「保存并定时启动」后浏览器立即打开并登录，登录完待机不投注，到点自动开投。<br />
                        若设定时刻今天已过，则等到<b>次日</b>该时刻（启动时会弹窗确认具体日期）。
                      </div>
                    </>
                  ) : (
                    <div style={{ color: '#888', fontSize: 12 }}>点「保存并启动」立刻登录并开始下注。</div>

                  )}
                </Panel>

                {/* ── 止盈止损 ── */}
                <Panel header="🛡️ 止盈止损" key="risk">
                  <Row gutter={16}>
                    <Col span={12}>
                      <Form.Item label="止盈 (元)" name="take_profit" rules={[{ required: true }]}>
                        <InputNumber style={{ width: '100%' }} min={0} />
                      </Form.Item>
                    </Col>
                    <Col span={12}>
                      <Form.Item label="止损 (元)" name="daily_stop_loss" rules={[{ required: true }]}>
                        <InputNumber style={{ width: '100%' }} min={0} />
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
            <LogViewer taskId="rotatebet" running={isRunning} />
          </Card>
        </Col>
      </Row>
    </div>
  )
}
