import React, { useEffect, useState } from 'react'
import {
  Card, Form, Input, InputNumber, Button, Space, Typography, Divider,
  Row, Col, message, Tag, Collapse, Radio, Modal, Switch, Table, Alert,
} from 'antd'
import { PlusOutlined, MinusCircleOutlined, PlayCircleOutlined, PauseCircleOutlined, ClockCircleOutlined, StopOutlined } from '@ant-design/icons'
import { api } from '../api/client'
import LogViewer from '../components/LogViewer'

const { Title, Text, Paragraph } = Typography
const { Panel } = Collapse

const STATUS_TAG = {
  running: <Tag color="green">运行中</Tag>,
  stopping: <Tag color="orange">停止中</Tag>,
  stopped: <Tag color="default">已停止</Tag>,
}

const ACCOUNT_STATUS_TAG = {
  starting: <Tag color="blue">启动中</Tag>,
  waiting: <Tag color="gold">等待中</Tag>,
  running: <Tag color="green">运行中</Tag>,
  stopping: <Tag color="orange">停止中</Tag>,
  stopped: <Tag color="default">已停止</Tag>,
  blocked: <Tag color="red">已阻止</Tag>,
  error: <Tag color="red">异常</Tag>,
}

const PATHS = [
  { label: '大小路', targets: '大 / 小' },
  { label: '单双路', targets: '单 / 双' },
]
const DEFAULT_STEPS = [100, 130, 299, 389, 506]

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

function parseAmountSteps(raw) {
  const vals = Array.isArray(raw) ? raw : []
  return vals
    .slice(0, 20)
    .map((v) => Number(v))
    .filter((v) => Number.isFinite(v) && v > 0)
    .map((v) => Math.round(v))
}

function cfgToForm(cfg) {
  const enabledPaths = Array.from(
    { length: 2 },
    (_, i) => (cfg.enabled_paths || [true, true])[i] !== false,
  )
  return {
    ...cfg,
    enabled_paths: enabledPaths,
    amount_steps: parseAmountSteps(cfg.amount_steps).length ? parseAmountSteps(cfg.amount_steps) : DEFAULT_STEPS,
  }
}

function formToCfg(vals) {
  return {
    ...vals,
    enabled_paths: Array.from(
      { length: 2 },
      (_, i) => (vals.enabled_paths || [true, true])[i] !== false,
    ),
    amount_steps: parseAmountSteps(vals.amount_steps),
  }
}

export default function MainTrendBetPage() {
  const [form] = Form.useForm()
  const startMode = Form.useWatch('start_mode', form) || 'now'
  const amountSteps = Form.useWatch('amount_steps', form) || []
  const accountList = Form.useWatch('accounts', form) || []
  const [status, setStatus] = useState('stopped')
  const [accounts, setAccounts] = useState([])
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)

  const showSaveError = (err) => {
    const firstError = err?.errorFields?.[0]
    if (firstError?.name) form.scrollToField(firstError.name, { block: 'center' })
    message.error(firstError?.errors?.[0] || err?.message || '请检查表单填写')
  }

  const refresh = async () => {
    const s = await api.getStatus()
    setStatus(s.main_trend_bet || 'stopped')
    const accountStatus = await api.getMainTrendBetAccountStatuses()
    setAccounts(accountStatus.accounts || [])
  }

  useEffect(() => {
    api.getMainTrendBetConfig().then((cfg) => form.setFieldsValue(cfgToForm(cfg)))
    refresh()
    const timer = setInterval(refresh, 3000)
    return () => clearInterval(timer)
  }, [])

  const saveConfig = async (override = {}) => {
    try {
      const patch = override && (override.nativeEvent || override.currentTarget || override.target) ? {} : override || {}
      const vals = await form.validateFields()
      const cfg = { ...formToCfg(vals), ...patch }
      if (!cfg.enabled_paths.some(Boolean)) {
        message.error('至少启用一路')
        return false
      }
      if (cfg.amount_steps.length < 1 || cfg.amount_steps.length > 20) {
        message.error('金额阶梯需要 1 到 20 阶')
        return false
      }
      setSaving(true)
      const res = await api.saveMainTrendBetConfig(cfg)
      if (res?.ok === false) {
        message.error(res.message || '保存失败')
        return false
      }
      message.success('配置已保存')
      return true
    } catch (err) {
      showSaveError(err)
      return false
    } finally {
      setSaving(false)
    }
  }

  const startRun = async () => {
    setLoading(true)
    const res = await api.startMainTrendBet()
    message.info(res.message)
    setLoading(false)
    if (res.ok) setStatus('running')
    refresh()
  }

  const saveAndRunNow = async () => {
    form.setFieldsValue({ start_mode: 'now' })
    if (await saveConfig({ start_mode: 'now' })) await startRun()
  }

  const saveAndSchedule = async () => {
    form.setFieldsValue({ start_mode: 'scheduled' })
    const target = nextAlarm(form.getFieldValue('start_time'))
    if (!target) {
      message.error('开跑时刻格式不对，应为 08:00')
      return
    }
    const minutes = Math.round((target - new Date()) / 60000)
    const waitText = minutes >= 60 ? `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分后` : `${minutes} 分钟后`
    const dayText = target.getDate() === new Date().getDate() ? '今天' : `明天(${target.getMonth() + 1}-${target.getDate()})`
    const timeText = `${String(target.getHours()).padStart(2, '0')}:${String(target.getMinutes()).padStart(2, '0')}`
    Modal.confirm({
      title: '确认闹钟定时启动？',
      content: <div>将于 <b style={{ color: '#fa8c16' }}>{dayText} {timeText}</b> 开始下注（约 {waitText}）。</div>,
      okText: '确认启动',
      cancelText: '取消',
      onOk: async () => {
        if (!(await saveConfig({ start_mode: 'scheduled' }))) return Promise.reject()
        await startRun()
      },
    })
  }

  const saveOrJoin = async () => {
    if (await saveConfig()) {
      if (isRunning) {
        message.success('新增账号已保存并加入当前会话')
        refresh()
        return
      }
      await startRun()
    }
  }

  const stopRun = async () => {
    setLoading(true)
    const res = await api.stopMainTrendBet()
    message.info(res.message)
    setLoading(false)
    refresh()
  }

  const stopAccount = async (key) => {
    const res = await api.stopMainTrendBetAccount(key)
    message.info(res.message)
    refresh()
  }

  const columns = [
    { title: '账号', dataIndex: 'account', render: (v) => v || '-' },
    { title: '端口', dataIndex: 'port', width: 80 },
    { title: '状态', dataIndex: 'status', width: 90, render: (v) => ACCOUNT_STATUS_TAG[v] || <Tag>{v || '-'}</Tag> },
    { title: '说明', dataIndex: 'message', ellipsis: true },
    {
      title: '操作',
      width: 90,
      render: (_, row) => (
        <Button
          size="small"
          danger
          icon={<StopOutlined />}
          disabled={!['starting', 'waiting', 'running'].includes(row.status)}
          onClick={() => stopAccount(row.key)}
        >
          停止
        </Button>
      ),
    },
  ]

  const isRunning = status === 'running'
  const joinMode = isRunning && accountList.length > 1
  const saveButtonText = joinMode ? '保存并运行新增账号' : '仅保存配置'

  return (
    <div style={{ padding: 24 }}>
      <Space style={{ marginBottom: 24, width: '100%', justifyContent: 'space-between' }} align="center">
        <Space>
          <Title level={4} style={{ margin: 0 }}>主势大小单双追损</Title>
          {STATUS_TAG[status] || STATUS_TAG.stopped}
        </Space>
        <Space>
          <Button icon={<PlayCircleOutlined />} type="primary" onClick={saveAndRunNow} loading={loading} disabled={isRunning}>保存并随开随跑</Button>
          <Button icon={<ClockCircleOutlined />} onClick={saveAndSchedule} loading={loading} disabled={isRunning}>保存并定时启动</Button>
          <Button icon={<PauseCircleOutlined />} danger onClick={stopRun} loading={loading} disabled={!isRunning}>停止</Button>
        </Space>
      </Space>

      <Row gutter={24}>
        <Col xs={24} lg={14}>
          <Card title="参数配置" style={{ borderRadius: 8, marginBottom: 24 }}>
            <Form form={form} layout="vertical">
              <Collapse defaultActiveKey={['basic', 'accounts', 'paths', 'amounts', 'start', 'risk']} ghost forceRender>
                <Panel header="网站设置" key="basic">
                  <Form.Item label="入口网址" name="entry_url" rules={[{ required: true }]}>
                    <Input placeholder="https://166.tt" />
                  </Form.Item>
                  <Form.Item label="安全码" name="safe_code">
                    <Input placeholder="没有则留空" />
                  </Form.Item>
                </Panel>

                <Panel header="账号列表" key="accounts">
                  <Form.List name="accounts">
                    {(fields, { add, remove }) => (
                      <>
                        {fields.map(({ key, name }) => (
                          <div key={key} style={{ border: '1px solid #f0f0f0', borderRadius: 8, padding: 12, marginBottom: 8, background: '#fafafa' }}>
                            <Row gutter={8} align="middle">
                              <Col xs={24} md={7}>
                                <Form.Item name={[name, 'account']} label="账号" rules={[{ required: true }]}>
                                  <Input placeholder="luban001" />
                                </Form.Item>
                              </Col>
                              <Col xs={24} md={7}>
                                <Form.Item name={[name, 'password']} label="密码" rules={[{ required: true }]}>
                                  <Input.Password placeholder="密码" />
                                </Form.Item>
                              </Col>
                              <Col xs={18} md={6}>
                                <Form.Item name={[name, 'port']} label="端口" rules={[{ required: true }]}>
                                  <InputNumber style={{ width: '100%' }} placeholder="9222" />
                                </Form.Item>
                              </Col>
                              <Col xs={6} md={4}>
                                {fields.length > 1 && <Button danger icon={<MinusCircleOutlined />} onClick={() => remove(name)} />}
                              </Col>
                            </Row>
                          </div>
                        ))}
                        <Button
                          type="dashed"
                          onClick={() => {
                            const ports = (form.getFieldValue('accounts') || []).map((a) => Number(a?.port)).filter((p) => !Number.isNaN(p))
                            add({ account: '', password: '', port: ports.length ? Math.max(...ports) + 1 : 9222 })
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

                <Panel header="轮换盘路" key="paths">
                  <Alert
                    type="info"
                    showIcon
                    style={{ marginBottom: 12 }}
                    message="和值13按小、单中奖且赔率1.6；和值14按大、双中奖且赔率1.6。"
                  />
                  <Form.Item label="启用盘路" style={{ marginBottom: 12 }}>
                    <Space wrap>
                      {PATHS.map((path, index) => (
                        <Space key={path.label} size={8}>
                          <Text>{path.label}</Text>
                          <Tag color="cyan">{path.targets}</Tag>
                          <Form.Item name={['enabled_paths', index]} valuePropName="checked" initialValue noStyle>
                            <Switch checkedChildren="开" unCheckedChildren="关" />
                          </Form.Item>
                        </Space>
                      ))}
                    </Space>
                  </Form.Item>
                </Panel>

                <Panel header="追损参数" key="amounts">
                  <Form.Item label="入场触发条件" name="entry_miss_trigger" initialValue={1}>
                    <Radio.Group optionType="button" buttonStyle="solid">
                      <Radio.Button value={0}>直接开始</Radio.Button>
                      <Radio.Button value={1}>一次不中</Radio.Button>
                      <Radio.Button value={2}>两次不中</Radio.Button>
                      <Radio.Button value={3}>三次不中</Radio.Button>
                    </Radio.Group>
                  </Form.Item>
                  <Form.List name="amount_steps">
                    {(fields, { add, remove }) => (
                      <>
                        <Row gutter={8}>
                          {fields.map(({ key, name }) => (
                            <Col xs={12} md={8} key={key}>
                              <Space.Compact style={{ width: '100%', marginBottom: 8 }}>
                                <Form.Item name={name} rules={[{ required: true, type: 'number', min: 1 }]} style={{ marginBottom: 0, flex: 1 }}>
                                  <InputNumber addonBefore={`${name + 1}阶`} min={1} precision={0} style={{ width: '100%' }} />
                                </Form.Item>
                                {fields.length > 1 && <Button danger icon={<MinusCircleOutlined />} onClick={() => remove(name)} />}
                              </Space.Compact>
                            </Col>
                          ))}
                        </Row>
                        <Button
                          type="dashed"
                          disabled={amountSteps.length >= 20}
                          onClick={() => {
                            const steps = parseAmountSteps(form.getFieldValue('amount_steps'))
                            const last = steps.length ? steps[steps.length - 1] : 100
                            add(Math.max(1, Math.round(last * 1.3)))
                          }}
                          block
                          icon={<PlusOutlined />}
                        >
                          添加阶梯
                        </Button>
                      </>
                    )}
                  </Form.List>
                </Panel>

                <Panel header="启动方式" key="start">
                  <Form.Item label="启动方式" name="start_mode" initialValue="now" style={{ marginBottom: 12 }}>
                    <Radio.Group optionType="button" buttonStyle="solid" disabled={isRunning}>
                      <Radio.Button value="now">随开随跑</Radio.Button>
                      <Radio.Button value="scheduled">闹钟定时</Radio.Button>
                    </Radio.Group>
                  </Form.Item>
                  {startMode === 'scheduled' && (
                    <Form.Item
                      label="开跑时刻"
                      name="start_time"
                      rules={[{ required: true, pattern: /^([01]?\d|2[0-3]):[0-5]\d$/, message: '格式如 08:00' }]}
                    >
                      <Input placeholder="08:00" style={{ width: 160 }} />
                    </Form.Item>
                  )}
                </Panel>

                <Panel header="止盈止损" key="risk">
                  <Row gutter={16}>
                    <Col xs={24} md={12}>
                      <Form.Item label="止盈 (元)" name="take_profit" rules={[{ required: true }]}>
                        <InputNumber style={{ width: '100%' }} min={0} />
                      </Form.Item>
                    </Col>
                    <Col xs={24} md={12}>
                      <Form.Item label="止损 (元)" name="daily_stop_loss" rules={[{ required: true }]}>
                        <InputNumber style={{ width: '100%' }} min={0} />
                      </Form.Item>
                    </Col>
                  </Row>
                </Panel>
              </Collapse>

              <Divider />
              <Button onClick={joinMode ? saveOrJoin : () => saveConfig()} loading={saving || (joinMode && loading)} block type={joinMode ? 'primary' : 'default'}>
                {saveButtonText}
              </Button>
              {joinMode && (
                <Paragraph type="secondary" style={{ margin: '10px 0 0', fontSize: 12 }}>
                  新账号会加入当前会话，沿用当前锁定策略和金额阶梯，从第 1 阶开始，等待下一完整投注周期再运行。
                </Paragraph>
              )}
            </Form>
          </Card>
        </Col>

        <Col xs={24} lg={10}>
          <Card title="账号运行状态" style={{ borderRadius: 8, marginBottom: 24 }}>
            <Table size="small" rowKey="key" columns={columns} dataSource={accounts} pagination={false} scroll={{ x: 640 }} />
          </Card>
          <Card title="实时日志" style={{ borderRadius: 8 }}>
            <LogViewer taskId="main_trend_bet" running={isRunning} />
          </Card>
        </Col>
      </Row>
    </div>
  )
}
