import React, { useEffect, useMemo, useState } from 'react'
import {
  Alert, Button, Card, Col, Collapse, Divider, Empty, Form, Input, InputNumber,
  Modal, Radio, Row, Space, Statistic, Switch, Table, Tag, Typography, message,
} from 'antd'
import {
  BarChartOutlined, CheckCircleOutlined, ClockCircleOutlined, MinusCircleOutlined,
  PauseCircleOutlined, PlayCircleOutlined, PlusOutlined, StopOutlined,
} from '@ant-design/icons'
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

const PLAN_TONE = {
  stable: { color: '#389e0d', bg: '#f6ffed', border: '#b7eb8f', label: '低风险优先' },
  balanced: { color: '#1677ff', bg: '#f0f5ff', border: '#adc6ff', label: '默认推荐' },
  drawdown: { color: '#d48806', bg: '#fffbe6', border: '#ffe58f', label: '波动较大' },
}

const BALL_LABELS = ['第一球', '第二球', '第三球']
const DEFAULT_STEPS = [100, 130, 299, 389, 506]
const DEFAULT_GROUPS = ['0,1,3,8', '0,1,3,8', '0,1,3,8']

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
    .split(/[,，\s]+/)
    .map((s) => s.trim())
    .filter(Boolean)
    .map(Number)
    .filter((n, i, arr) => !Number.isNaN(n) && n >= 0 && n <= 9 && arr.indexOf(n) === i)
    .sort((a, b) => a - b)
}

function parseAmountSteps(raw) {
  const vals = Array.isArray(raw) ? raw : []
  return vals
    .slice(0, 20)
    .map((v) => Number(v))
    .filter((v) => Number.isFinite(v) && v > 0)
    .map((v) => Math.round(v))
}

function cfgToForm(cfg = {}) {
  const rest = { ...cfg }
  delete rest.budget
  delete rest.analysis_budget
  const groups = (cfg.number_groups || []).map((item) => ({
    numbers: (item.numbers || item || []).join(','),
  }))
  while (groups.length < 3) groups.push({ numbers: DEFAULT_GROUPS[groups.length] })
  const enabledPositions = Array.from(
    { length: 3 },
    (_, i) => (cfg.enabled_positions || [true, true, true])[i] !== false,
  )
  return {
    ...rest,
    number_groups: groups,
    enabled_positions: enabledPositions,
    amount_steps: parseAmountSteps(cfg.amount_steps).length ? parseAmountSteps(cfg.amount_steps) : DEFAULT_STEPS,
  }
}

function formToCfg(vals) {
  const rest = { ...(vals || {}) }
  delete rest.budget
  delete rest.analysis_budget
  return {
    ...rest,
    enabled_positions: Array.from(
      { length: 3 },
      (_, i) => (vals.enabled_positions || [true, true, true])[i] !== false,
    ),
    number_groups: (vals.number_groups || []).map((item) => ({ numbers: parseNums(item.numbers) })),
    amount_steps: parseAmountSteps(vals.amount_steps),
  }
}

function pct(value) {
  return `${((Number(value) || 0) * 100).toFixed(1)}%`
}

function money(value, digits = 2) {
  const n = Number(value) || 0
  const text = n.toFixed(digits)
  return n > 0 ? `+${text}` : text
}

function amount(value) {
  return Number(value || 0).toFixed(0)
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

function budgetTag(row) {
  if (!row) return <Tag>-</Tag>
  return <Tag color="blue">{row.budget_needed || 0}</Tag>
}

function actionTag(value) {
  const color = value === '建议替换' ? 'blue' : value === '保持当前' ? 'green' : value === '准备金不足' ? 'red' : 'default'
  return <Tag color={color}>{value || '-'}</Tag>
}

function adviceTag(value) {
  const color = value === '可观察入场' ? 'blue' : value === '表现平稳' ? 'green' : value === '准备金不足' ? 'red' : 'gold'
  return <Tag color={color}>{value || '-'}</Tag>
}

function suggestionType(level) {
  if (level === 'error') return 'error'
  if (level === 'success') return 'success'
  if (level === 'warning') return 'warning'
  return 'info'
}

function recordSourceText(source) {
  if (!source) return ''
  const parts = [source.label || '开奖记录']
  if (source.captured_at) parts.push(`抓取 ${source.captured_at}`)
  if (source.record_count) parts.push(`${source.record_count}条`)
  if (source.first_issue || source.last_issue) parts.push(`期号 ${source.first_issue || '-'} - ${source.last_issue || '-'}`)
  return parts.join('，')
}

function PlanBox({ plan, onApply }) {
  const tone = PLAN_TONE[plan.key] || PLAN_TONE.balanced
  const summary = plan.summary || {}
  return (
    <div style={{ border: `1px solid ${tone.border}`, background: tone.bg, borderRadius: 8, padding: 12 }}>
      <Space align="start" style={{ width: '100%', justifyContent: 'space-between' }}>
        <Space direction="vertical" size={2}>
          <Space>
            <Text strong style={{ color: tone.color, fontSize: 16 }}>{plan.name}</Text>
            <Tag color={plan.key === 'stable' ? 'green' : plan.key === 'drawdown' ? 'gold' : 'blue'}>{tone.label}</Tag>
          </Space>
          <Text type="secondary" style={{ fontSize: 12 }}>{plan.description}</Text>
        </Space>
        <Button size="small" type={plan.key === 'balanced' ? 'primary' : 'default'} onClick={() => onApply(plan)}>
          使用这套
        </Button>
      </Space>

      <Row gutter={[8, 8]} style={{ marginTop: 10 }}>
        <Col span={8}><Statistic title="最低准备" value={summary.budget_needed || 0} valueStyle={{ fontSize: 18, color: tone.color }} /></Col>
        <Col span={8}><Statistic title="建议准备" value={summary.suggested_budget || 0} valueStyle={{ fontSize: 18 }} /></Col>
        <Col span={8}><Statistic title="保守准备" value={summary.safe_budget || 0} valueStyle={{ fontSize: 18 }} /></Col>
      </Row>

      <Space direction="vertical" size={8} style={{ width: '100%', marginTop: 10 }}>
        {(plan.positions || []).map((row) => {
          const candidate = row.candidate || {}
          return (
            <div key={row.position} style={{ background: '#fff', border: '1px solid #f0f0f0', borderRadius: 6, padding: 8 }}>
              <Space style={{ width: '100%', justifyContent: 'space-between' }} align="start">
                <Space direction="vertical" size={4}>
                  <Space>
                    <Text strong>{row.name}</Text>
                    {!row.enabled && <Tag>关闭</Tag>}
                  </Space>
                  {numberTags(row.numbers || [], plan.key === 'drawdown' ? 'orange' : plan.key === 'stable' ? 'green' : 'blue')}
                </Space>
                <Space direction="vertical" size={2} style={{ textAlign: 'right' }}>
                  <Text type="secondary">回撤 {amount(candidate.current_drawdown)}</Text>
                  <Text type="secondary">需备 {candidate.budget_needed || 0}</Text>
                  <Text type="secondary">第{candidate.next_state?.tier || 1}阶/{candidate.next_state?.amount || 0}</Text>
                </Space>
              </Space>
              <Text type="secondary" style={{ display: 'block', marginTop: 6, fontSize: 12 }}>
                命中 {pct(candidate.hit_rate)}，最大回撤 {amount(candidate.max_drawdown)}，最大连挂 {candidate.max_miss_streak || 0}
              </Text>
            </div>
          )
        })}
      </Space>
    </div>
  )
}

export default function FourCodeWinBetPage() {
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

  const isRunning = status === 'running'
  const joinMode = isRunning && accountList.length > 1
  const saveButtonText = joinMode ? '保存并运行新增账号' : '仅保存配置'

  const refresh = async () => {
    const s = await api.getStatus()
    setStatus(s.four_code_winbet || 'stopped')
    const accountStatus = await api.getFourCodeWinBetAccountStatuses()
    setAccounts(accountStatus.accounts || [])
  }

  useEffect(() => {
    api.getFourCodeWinBetConfig().then((cfg) => form.setFieldsValue(cfgToForm(cfg)))
    refresh()
    const t = setInterval(refresh, 3000)
    return () => clearInterval(t)
  }, [])

  const showSaveError = (err) => {
    const firstError = err?.errorFields?.[0]
    if (firstError?.name) form.scrollToField(firstError.name, { block: 'center' })
    message.error(firstError?.errors?.[0] || err?.message || '请检查表单填写')
  }

  const validateConfig = (cfg) => {
    if (!cfg.enabled_positions.some(Boolean)) {
      message.error('至少启用一路球')
      return false
    }
    if (cfg.amount_steps.length < 1 || cfg.amount_steps.length > 20) {
      message.error('金额阶梯需要 1 到 20 阶')
      return false
    }
    for (let i = 0; i < 3; i += 1) {
      const nums = cfg.number_groups?.[i]?.numbers || []
      if (nums.length !== 4) {
        message.error(`${BALL_LABELS[i]} 必须填写 4 个不重复号码`)
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
      if (!validateConfig(cfg)) return false
      setSaving(true)
      const res = await api.saveFourCodeWinBetConfig(cfg)
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
    if (!validateConfig(cfg)) return
    setAnalyzing(true)
    try {
      const res = await api.analyzeFourCodeWinBet({ config: cfg, limit: 1000 })
      setAnalysis(res)
      if (res?.ok === false) {
        message.warning(res.message || '分析失败')
      } else {
        message.success('三套选号方案已生成')
      }
    } finally {
      setAnalyzing(false)
    }
  }

  const doStart = async () => {
    setLoading(true)
    const res = await api.startFourCodeWinBet()
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
    const res = await api.stopFourCodeWinBet()
    message.info(res.message)
    setLoading(false)
    refresh()
  }

  const handleStopAccount = async (key) => {
    const res = await api.stopFourCodeWinBetAccount(key)
    message.info(res.message)
    refresh()
  }

  const numberRule = (_, value) => {
    const nums = parseNums(value)
    return nums.length === 4
      ? Promise.resolve()
      : Promise.reject(new Error('必须填写 4 个不重复有效号码'))
  }

  const applyNumbers = (groups, label) => {
    const nextGroups = [...(form.getFieldValue('number_groups') || [])]
    while (nextGroups.length < 3) nextGroups.push({ numbers: '' })
    ;(groups || []).forEach((group, index) => {
      if ((group.numbers || []).length === 4) {
        nextGroups[index] = { ...(nextGroups[index] || {}), numbers: group.numbers.join(',') }
      }
    })
    form.setFieldsValue({ number_groups: nextGroups })
    message.success(`已应用${label}`)
  }

  const applyPlan = (plan) => {
    applyNumbers(plan.recommended_config?.number_groups || [], plan.short_name || plan.name || '推荐方案')
  }

  const applyRecommendation = (row) => {
    const candidate = row.recommended
    const pos = Number(row.position) - 1
    if (!candidate || pos < 0 || pos >= 3) return
    const nextGroups = [...(form.getFieldValue('number_groups') || [])]
    while (nextGroups.length < 3) nextGroups.push({ numbers: '' })
    nextGroups[pos] = { ...(nextGroups[pos] || {}), numbers: (candidate.numbers || []).join(',') }
    form.setFieldsValue({ number_groups: nextGroups })
    message.success(`${row.name} 已应用推荐4码`)
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

  const recommendationColumns = useMemo(() => [
    { title: '球路', dataIndex: 'name', width: 72 },
    { title: '动作', dataIndex: 'action', width: 96, render: actionTag },
    { title: '推荐4码', width: 130, render: (_, row) => numberTags(row.recommended?.numbers || [], 'blue') },
    { title: '建议', width: 96, render: (_, row) => adviceTag(row.recommended?.advice) },
    { title: '当前回撤', width: 92, render: (_, row) => amount(row.recommended?.current_drawdown) },
    { title: '需备金额', width: 86, render: (_, row) => budgetTag(row.recommended) },
    { title: '下期阶数', width: 92, render: (_, row) => `第${row.recommended?.next_state?.tier || 1}阶/${row.recommended?.next_state?.amount || 0}` },
    { title: '利润', width: 88, render: (_, row) => profitTag(row.recommended?.profit) },
    { title: '命中', width: 76, render: (_, row) => pct(row.recommended?.hit_rate) },
    { title: '最大回撤', width: 86, render: (_, row) => amount(row.recommended?.max_drawdown) },
    { title: '最大连挂', width: 82, render: (_, row) => row.recommended?.max_miss_streak || 0 },
    { title: '理由', width: 280, render: (_, row) => <Text type="secondary">{(row.recommended?.reasons || []).join('，') || '-'}</Text> },
    {
      title: '操作', width: 86, fixed: 'right',
      render: (_, row) => <Button size="small" type="link" disabled={!row.recommended} onClick={() => applyRecommendation(row)}>应用</Button>,
    },
  ], [form])

  const currentColumns = useMemo(() => [
    { title: '球路', dataIndex: 'name', width: 72 },
    { title: '状态', dataIndex: 'enabled', width: 72, render: (v) => (v ? <Tag color="green">开启</Tag> : <Tag>关闭</Tag>) },
    { title: '当前4码', dataIndex: 'numbers', width: 130, render: (v) => numberTags(v, 'geekblue') },
    { title: '当前回撤', dataIndex: 'current_drawdown', width: 92, render: amount },
    { title: '需备金额', width: 86, render: (_, row) => budgetTag(row) },
    { title: '下期阶数', width: 92, render: (_, row) => `第${row.next_state?.tier || 1}阶/${row.next_state?.amount || 0}` },
    { title: '利润', dataIndex: 'profit', width: 88, render: profitTag },
    { title: '命中', dataIndex: 'hit_rate', width: 76, render: pct },
    { title: '最大回撤', dataIndex: 'max_drawdown', width: 86, render: amount },
    { title: '最大连挂', dataIndex: 'max_miss_streak', width: 82 },
    { title: '建议', dataIndex: 'advice', width: 110, render: adviceTag },
  ], [])

  const summary = analysis?.summary
  const profilePlans = analysis?.recommendations?.profile_plans || []
  const stablePlan = profilePlans.find((item) => item.key === 'stable')
  const balancedPlan = profilePlans.find((item) => item.key === 'balanced')
  const drawdownPlan = profilePlans.find((item) => item.key === 'drawdown')

  return (
    <div style={{ padding: 24 }}>
      <Space style={{ marginBottom: 24, width: '100%', justifyContent: 'space-between' }} align="center" wrap>
        <Space>
          <Title level={4} style={{ margin: 0 }}>4粒码赢冲输缩</Title>
          {STATUS_TAG[status] ?? STATUS_TAG.stopped}
        </Space>
        <Space wrap>
          <Button icon={<BarChartOutlined />} onClick={handleAnalyze} loading={analyzing}>
            生成三套选号方案
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
              <Collapse defaultActiveKey={['basic', 'accounts', 'amounts', 'numbers', 'start', 'risk']} ghost forceRender>
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

                <Panel header="金额阶梯" key="amounts">

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

                <Panel header="4粒码号码" key="numbers">
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
                  {BALL_LABELS.map((label, i) => (
                    <Form.Item key={label} name={['number_groups', i, 'numbers']} label={`${label} 4粒码`} rules={[{ required: true, validator: numberRule }]}>
                      <Input placeholder="0,1,3,8" />
                    </Form.Item>
                  ))}
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
              <Button onClick={joinMode ? handleSaveAndRunAccounts : () => handleSave()} loading={saving || (joinMode && loading)} block type={joinMode ? 'primary' : 'default'}>
                {saveButtonText}
              </Button>
              {joinMode && (
                <Paragraph type="secondary" style={{ margin: '10px 0 0', fontSize: 12 }}>
                  运行中新增账号会加入当前会话，沿用当前保存的4粒码和金额阶梯，从第1阶开始，等待下一完整投注周期再运行。
                </Paragraph>
              )}
            </Form>
          </Card>
        </Col>

        <Col xs={24} lg={10}>
          <Card
            title="三套选号方案"
            extra={<Button size="small" icon={<BarChartOutlined />} onClick={handleAnalyze} loading={analyzing}>生成</Button>}
            style={{ borderRadius: 8, marginBottom: 24 }}
          >
            {analysis?.ok === false && <Alert type="warning" showIcon message={analysis.message} />}
            {analysis?.ok ? (
              <Space direction="vertical" size={12} style={{ width: '100%' }}>
                <Row gutter={[8, 8]}>
                  <Col xs={12} md={8}><Statistic title="当前配置需备" value={summary?.budget_needed || 0} valueStyle={{ color: '#1677ff' }} /></Col>
                  <Col xs={12} md={8}><Statistic title="当前回撤" value={amount(summary?.current_drawdown)} /></Col>
                  <Col xs={12} md={8}><Statistic title="开奖记录" value={summary?.records || 0} /></Col>
                </Row>
                {analysis.record_source && <Alert type="info" showIcon message={`回测样本：${recordSourceText(analysis.record_source)}`} />}
                {(stablePlan || balancedPlan || drawdownPlan) && (
                  <Alert
                    type="success"
                    showIcon
                    message={`准备金参考：稳健 ${stablePlan?.summary?.suggested_budget || '-'}，均衡 ${balancedPlan?.summary?.suggested_budget || '-'}，冲回撤 ${drawdownPlan?.summary?.suggested_budget || '-'}`}
                    description="最低准备覆盖历史回测风险；建议准备留20%缓冲；保守准备留50%缓冲。"
                  />
                )}
                {profilePlans.map((plan) => <PlanBox key={plan.key} plan={plan} onApply={applyPlan} />)}
                {(analysis.suggestions || []).map((item, index) => (
                  <Alert key={`${item.level}-${index}`} type={suggestionType(item.level)} showIcon message={item.text} />
                ))}
                <Collapse ghost>
                  <Panel header="详细回测表" key="detail">
                    <Alert type="info" showIcon message={`已回测 ${analysis.recommendations?.records || 0} 条开奖记录`} description={analysis.recommendations?.method} style={{ marginBottom: 12 }} />
                    <Table
                      size="small"
                      rowKey="position"
                      columns={recommendationColumns}
                      dataSource={analysis.recommendations?.positions || []}
                      pagination={false}
                      scroll={{ x: 1380 }}
                    />
                    <Divider orientation="left" plain>当前配置回测</Divider>
                    <Table
                      size="small"
                      rowKey="position"
                      columns={currentColumns}
                      dataSource={analysis.positions || []}
                      pagination={false}
                      scroll={{ x: 1040 }}
                    />
                  </Panel>
                </Collapse>
              </Space>
            ) : !analysis && (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="先抓取开奖记录，再生成三套选号方案" />
            )}
          </Card>

          <Card title="账号运行状态" style={{ borderRadius: 8, marginBottom: 24 }}>
            <Table size="small" rowKey="key" columns={accountColumns} dataSource={accounts} pagination={false} scroll={{ x: 640 }} />
          </Card>
          <Card title="实时日志" style={{ borderRadius: 8 }}>
            <LogViewer taskId="four_code_winbet" running={isRunning} />
          </Card>
        </Col>
      </Row>
    </div>
  )
}
