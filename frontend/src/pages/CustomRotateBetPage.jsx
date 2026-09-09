import React, { useEffect, useMemo, useState } from 'react'
import {
  Alert, Card, Empty, Form, Input, InputNumber, Button, Space, Statistic, Typography, Divider,
  Row, Col, message, Tag, Collapse, Radio, Modal, Switch, Table,
} from 'antd'
import { BarChartOutlined, PlusOutlined, MinusCircleOutlined, PlayCircleOutlined, PauseCircleOutlined, ClockCircleOutlined, StopOutlined } from '@ant-design/icons'
import { api } from '../api/client'
import LogViewer from '../components/LogViewer'

const { Title, Text } = Typography
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

const BALL_LABELS = ['第一球', '第二球', '第三球']
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

function parseNums(str) {
  return String(str || '')
    .split(/[，,\s]+/)
    .map((s) => s.trim())
    .filter(Boolean)
    .map(Number)
    .filter((n, i, arr) => !Number.isNaN(n) && n >= 0 && n <= 9 && arr.indexOf(n) === i)
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
  const ns = (cfg.number_sets || []).map((s) => ({
    set_a: (s.set_a || []).join(','),
    set_b: (s.set_b || []).join(','),
  }))
  while (ns.length < 3) ns.push({ set_a: '0,1,3,5,8', set_b: '2,4,6,7,9' })
  const enabledPositions = Array.from(
    { length: 3 },
    (_, i) => (cfg.enabled_positions || [true, true, true])[i] !== false,
  )
  return {
    ...cfg,
    number_sets: ns,
    enabled_positions: enabledPositions,
    amount_steps: parseAmountSteps(cfg.amount_steps).length ? parseAmountSteps(cfg.amount_steps) : DEFAULT_STEPS,
  }
}

function formToCfg(vals) {
  return {
    ...vals,
    enabled_positions: Array.from(
      { length: 3 },
      (_, i) => (vals.enabled_positions || [true, true, true])[i] !== false,
    ),
    number_sets: (vals.number_sets || []).map((s) => ({
      set_a: parseNums(s.set_a),
      set_b: parseNums(s.set_b),
    })),
    amount_steps: parseAmountSteps(vals.amount_steps),
  }
}

function pct(value) {
  return `${((Number(value) || 0) * 100).toFixed(1)}%`
}

function money(value) {
  const n = Number(value) || 0
  return n > 0 ? `+${n.toFixed(2)}` : n.toFixed(2)
}

function profitTag(value) {
  const n = Number(value) || 0
  return <Tag color={n > 0 ? 'green' : n < 0 ? 'red' : 'default'}>{money(n)}</Tag>
}

function numberTags(values, color = 'geekblue') {
  return (
    <Space size={[4, 4]} wrap>
      {(values || []).map((value) => <Tag color={color} key={value}>{value}</Tag>)}
    </Space>
  )
}

function recommendationSet(row, label) {
  const candidate = row.recommended || {}
  const nums = label === 'A' ? candidate.set_a : candidate.set_b
  return (
    <Space direction="vertical" size={2}>
      <Text>{label}组</Text>
      {numberTags(nums, label === 'A' ? 'blue' : 'purple')}
    </Space>
  )
}

function recommendationActionTag(value) {
  const color = value === '建议替换' ? 'blue' : value === '保持当前' ? 'green' : value === '暂不推荐' ? 'red' : 'default'
  return <Tag color={color}>{value || '-'}</Tag>
}

function enabledAdviceTag(value) {
  const color = value === '建议开启/保留' ? 'green' : value === '谨慎开启' ? 'gold' : 'red'
  return <Tag color={color}>{value || '-'}</Tag>
}

function riskColor(value) {
  if (value === '低') return 'green'
  if (value === '中' || value === '样本不足') return 'gold'
  return 'red'
}

function suggestionType(level) {
  if (level === 'error') return 'error'
  if (level === 'success') return 'success'
  if (level === 'warning') return 'warning'
  return 'info'
}

function setMetric(row, label) {
  const nums = label === 'A' ? row.set_a : row.set_b
  const metric = row.sets?.[label] || {}
  return (
    <Space direction="vertical" size={2}>
      <Text>{label}：{(nums || []).join(',')}</Text>
      <Space size={4} wrap>
        {profitTag(metric.profit)}
        <Tag>命中 {pct(metric.hit_rate)}</Tag>
        <Tag>下注 {metric.bets || 0}</Tag>
      </Space>
    </Space>
  )
}
export default function CustomRotateBetPage() {
  const [form] = Form.useForm()
  const startMode = Form.useWatch('start_mode', form) || 'now'
  const amountSteps = Form.useWatch('amount_steps', form) || []
  const accountList = Form.useWatch('accounts', form) || []
  const [status, setStatus] = useState('stopped')
  const [accounts, setAccounts] = useState([])
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [analyzing, setAnalyzing] = useState(false)
  const [analysis, setAnalysis] = useState(null)

  const showSaveError = (err) => {
    const firstError = err?.errorFields?.[0]
    if (firstError?.name) form.scrollToField(firstError.name, { block: 'center' })
    message.error(firstError?.errors?.[0] || err?.message || '请检查表单填写')
  }

  const refresh = async () => {
    const s = await api.getStatus()
    setStatus(s.custom_rotatebet || 'stopped')
    const accountStatus = await api.getCustomRotateBetAccountStatuses()
    setAccounts(accountStatus.accounts || [])
  }

  useEffect(() => {
    api.getCustomRotateBetConfig().then((cfg) => form.setFieldsValue(cfgToForm(cfg)))
    refresh()
    const t = setInterval(refresh, 3000)
    return () => clearInterval(t)
  }, [])

  const validateStrategyConfig = (cfg) => {
    if (!cfg.enabled_positions.some(Boolean)) {
      message.error('至少启用一路球')
      return false
    }
    if (cfg.amount_steps.length < 1 || cfg.amount_steps.length > 20) {
      message.error('金额阶梯需要 1 到 20 阶')
      return false
    }
    for (let i = 0; i < 3; i += 1) {
      const row = cfg.number_sets?.[i] || {}
      if (![4, 5].includes(row.set_a?.length) || ![4, 5].includes(row.set_b?.length)) {
        message.error(`${BALL_LABELS[i]} A/B 组各填 4 个或 5 个有效号码`)
        return false
      }
    }
    return true
  }
  const handleSave = async (overrides = {}) => {
    try {
      const saveOverrides = overrides && (overrides.nativeEvent || overrides.currentTarget || overrides.target) ? {} : (overrides || {})
      const vals = await form.validateFields()
      const cfg = { ...formToCfg(vals), ...saveOverrides }
      if (!validateStrategyConfig(cfg)) return false
      setSaving(true)
      const res = await api.saveCustomRotateBetConfig(cfg)
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

  const handleAnalyze = async () => {
    const vals = form.getFieldsValue(true)
    const cfg = formToCfg(vals)
    if (!validateStrategyConfig(cfg)) return
    setAnalyzing(true)
    try {
      const res = await api.analyzeCustomRotateBet({ config: cfg, limit: 1000 })
      setAnalysis(res)
      if (res?.ok === false) {
        message.warning(res.message || '分析失败')
      } else {
        message.success('追损回测推荐已生成')
      }
    } finally {
      setAnalyzing(false)
    }
  }
  const doStart = async () => {
    setLoading(true)
    const res = await api.startCustomRotateBet()
    message.info(res.message)
    setLoading(false)
    if (res.ok) setStatus('running')
    refresh()
  }

  const handleStartNow = async () => {
    form.setFieldsValue({ start_mode: 'now' })
    if (!(await handleSave({ start_mode: 'now' }))) return
    doStart()
  }

  const handleStartScheduled = async () => {
    form.setFieldsValue({ start_mode: 'scheduled' })
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
      content: <div>将于 <b style={{ color: '#fa8c16' }}>{day} {hhmm}</b> 开始下注（约 {span}）。</div>,
      okText: '确认启动',
      cancelText: '取消',
      onOk: async () => {
        if (!(await handleSave({ start_mode: 'scheduled' }))) return Promise.reject()
        await doStart()
      },
    })
  }

  const handleSaveAndRunAccounts = async () => {
    if (!(await handleSave())) return
    if (isRunning) {
      message.success('新增账号已保存并加入当前会话')
      refresh()
      return
    }
    await doStart()
  }

  const handleStop = async () => {
    setLoading(true)
    const res = await api.stopCustomRotateBet()
    message.info(res.message)
    setLoading(false)
    refresh()
  }

  const handleStopAccount = async (key) => {
    const res = await api.stopCustomRotateBetAccount(key)
    message.info(res.message)
    refresh()
  }

  const numberRule = (_, value) => {
    const nums = parseNums(value)
    return nums.length === 4 || nums.length === 5
      ? Promise.resolve()
      : Promise.reject(new Error('A/B 组各填 4 个或 5 个有效号码'))
  }

  const accountColumns = [
    { title: '账号', dataIndex: 'account', render: (v) => v || '-' },
    { title: '端口', dataIndex: 'port', width: 80 },
    { title: '状态', dataIndex: 'status', width: 90, render: (v) => ACCOUNT_STATUS_TAG[v] || <Tag>{v || '-'}</Tag> },
    { title: '说明', dataIndex: 'message', ellipsis: true },
    {
      title: '操作', width: 90,
      render: (_, row) => (
        <Button
          size="small"
          danger
          icon={<StopOutlined />}
          disabled={!['starting', 'waiting', 'running'].includes(row.status)}
          onClick={() => handleStopAccount(row.key)}
        >
          停止
        </Button>
      ),
    },
  ]

  const analysisColumns = useMemo(() => [
    { title: '球路', dataIndex: 'name', width: 72 },
    { title: '状态', dataIndex: 'enabled', width: 72, render: (v) => (v ? <Tag color="green">开启</Tag> : <Tag>关闭</Tag>) },
    { title: '利润', dataIndex: 'profit', width: 88, render: profitTag },
    { title: '命中', dataIndex: 'hit_rate', width: 76, render: pct },
    { title: '下注', dataIndex: 'bets', width: 68 },
    { title: '回撤', dataIndex: 'max_drawdown', width: 82, render: (v) => Number(v || 0).toFixed(2) },
    { title: '最长不中', dataIndex: 'max_miss_streak', width: 86 },
    { title: '最高阶', dataIndex: 'max_tier_reached', width: 76, render: (v) => `${v || 1}阶` },
    { title: 'A组', width: 190, render: (_, row) => setMetric(row, 'A') },
    { title: 'B组', width: 190, render: (_, row) => setMetric(row, 'B') },
    { title: '建议', dataIndex: 'advice', width: 190 },
  ], [])

  const applyRecommendation = (row) => {
    const candidate = row.recommended
    const pos = Number(row.position) - 1
    if (!candidate || pos < 0 || pos >= 3) return
    const nextSets = [...(form.getFieldValue('number_sets') || [])]
    while (nextSets.length < 3) nextSets.push({ set_a: '', set_b: '' })
    nextSets[pos] = {
      ...(nextSets[pos] || {}),
      set_a: (candidate.set_a || []).join(','),
      set_b: (candidate.set_b || []).join(','),
    }
    form.setFieldsValue({ number_sets: nextSets })
    message.success(`${row.name} 已应用追损推荐号码`)
  }

  const recommendationColumns = useMemo(() => [
    { title: '球路', dataIndex: 'name', width: 72 },
    { title: '动作', dataIndex: 'action', width: 96, render: recommendationActionTag },
    { title: '开关', dataIndex: 'enabled_advice', width: 116, render: enabledAdviceTag },
    { title: '推荐A组', width: 128, render: (_, row) => recommendationSet(row, 'A') },
    { title: '推荐B组', width: 128, render: (_, row) => recommendationSet(row, 'B') },
    { title: '全样本利润', width: 104, render: (_, row) => profitTag(row.recommended?.profit) },
    { title: '最近期利润', width: 104, render: (_, row) => profitTag(row.recommended?.recent?.profit) },
    { title: '命中', width: 76, render: (_, row) => pct(row.recommended?.hit_rate) },
    { title: '最高阶', width: 76, render: (_, row) => `${row.recommended?.max_tier_reached || 1}阶` },
    { title: '回撤', width: 82, render: (_, row) => Number(row.recommended?.max_drawdown || 0).toFixed(2) },
    { title: '理由', width: 280, render: (_, row) => <Text type="secondary">{(row.recommended?.reasons || []).join('；') || '-'}</Text> },
    {
      title: '操作',
      width: 86,
      fixed: 'right',
      render: (_, row) => (
        <Button size="small" type="link" disabled={!row.recommended} onClick={() => applyRecommendation(row)}>
          应用
        </Button>
      ),
    },
  ], [form])
  const isRunning = status === 'running'
  const hasMultipleAccounts = accountList.length > 1
  const saveButtonText = hasMultipleAccounts ? '保存并运行新增账号' : '仅保存配置'
  const summary = analysis?.summary

  return (
    <div style={{ padding: 24 }}>
      <Space style={{ marginBottom: 24, width: '100%', justifyContent: 'space-between' }} align="center">
        <Space>
          <Title level={4} style={{ margin: 0 }}>自定义金额轮换追损</Title>
          {STATUS_TAG[status] ?? STATUS_TAG.stopped}
        </Space>
        <Space>
          <Button icon={<BarChartOutlined />} onClick={handleAnalyze} loading={analyzing}>
            分析当前配置
          </Button>
          <Button icon={<PlayCircleOutlined />} type="primary" onClick={handleStartNow} loading={loading} disabled={isRunning}>
            保存并随开随跑
          </Button>
          <Button icon={<ClockCircleOutlined />} onClick={handleStartScheduled} loading={loading} disabled={isRunning}>
            保存并定时启动
          </Button>
          <Button icon={<PauseCircleOutlined />} danger onClick={handleStop} loading={loading} disabled={!isRunning}>
            停止
          </Button>
        </Space>
      </Space>

      <Row gutter={24}>
        <Col xs={24} lg={14}>
          <Card title="参数配置" style={{ borderRadius: 8, marginBottom: 24 }}>
            <Form form={form} layout="vertical">
              <Collapse defaultActiveKey={['basic', 'accounts', 'numbers', 'amounts', 'start', 'risk']} ghost forceRender>
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
                            const existing = form.getFieldValue('accounts') || []
                            const ports = existing.map((a) => Number(a?.port)).filter((p) => !Number.isNaN(p))
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

                <Panel header="轮换号码" key="numbers">
                  {BALL_LABELS.map((label, i) => (
                    <div key={label} style={{ border: '1px solid #f0f0f0', borderRadius: 8, padding: 12, marginBottom: 10, background: '#fafafa' }}>
                      <Text strong>{label}</Text>
                      <Row gutter={12} style={{ marginTop: 8 }}>
                        <Col xs={24} md={12}>
                          <Form.Item name={['number_sets', i, 'set_a']} label="A组号码" rules={[{ required: true, validator: numberRule }]}>
                            <Input placeholder="0,1,3,5,8" />
                          </Form.Item>
                        </Col>
                        <Col xs={24} md={12}>
                          <Form.Item name={['number_sets', i, 'set_b']} label="B组号码" rules={[{ required: true, validator: numberRule }]}>
                            <Input placeholder="2,4,6,7,9" />
                          </Form.Item>
                        </Col>
                      </Row>
                    </div>
                  ))}
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
                  <Form.Item label="启用球路" style={{ marginBottom: 12 }}>
                    <Space wrap>
                      {BALL_LABELS.map((label, i) => (
                        <Space key={label} size={8}>
                          <Text>{label}</Text>
                          <Form.Item name={['enabled_positions', i]} valuePropName="checked" initialValue={true} noStyle>
                            <Switch checkedChildren="开" unCheckedChildren="关" />
                          </Form.Item>
                        </Space>
                      ))}
                    </Space>
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
                    <Form.Item label="开跑时刻" name="start_time" rules={[{ required: true, pattern: /^([01]?\d|2[0-3]):[0-5]\d$/, message: '格式如 08:00' }]}>
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
              <Button onClick={hasMultipleAccounts ? handleSaveAndRunAccounts : () => handleSave()} loading={saving || (hasMultipleAccounts && loading)} block type={hasMultipleAccounts ? 'primary' : 'default'}>
                {saveButtonText}
              </Button>
              {hasMultipleAccounts && (
                <Typography.Paragraph type="secondary" style={{ margin: '10px 0 0', fontSize: 12 }}>
                  运行中新增账号时，点击此按钮会保存账号并加入当前会话。原有账号不会重启；新账号登录后从第 1 阶开始，等待下一个完整投注周期再运行。
                </Typography.Paragraph>
              )}
            </Form>
          </Card>
        </Col>

        <Col xs={24} lg={10}>
          <Card title="账号运行状态" style={{ borderRadius: 8, marginBottom: 24 }}>
            <Table size="small" rowKey="key" columns={accountColumns} dataSource={accounts} pagination={false} scroll={{ x: 640 }} />
          </Card>
          <Card title="实时日志" style={{ borderRadius: 8 }}>
            <LogViewer taskId="custom_rotatebet" running={isRunning} />
          </Card>
        </Col>
      </Row>
    </div>
  )
}
