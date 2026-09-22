import React, { useEffect, useState } from 'react'
import {
  Button, Card, Collapse, Divider, Form, Input, InputNumber, message, Modal,
  Radio, Row, Col, Space, Switch, Table, Tag, Typography,
} from 'antd'
import {
  ClockCircleOutlined, MinusCircleOutlined, PauseCircleOutlined, PlayCircleOutlined,
  PlusOutlined, StopOutlined,
} from '@ant-design/icons'
import { api } from '../api/client'
import LogViewer from '../components/LogViewer'

const { Title, Text } = Typography
const { Panel } = Collapse
const BALL_LABELS = ['第一球', '第二球', '第三球']
const DEFAULT_STEPS = [100, 130, 299, 389, 506]

const STATUS_TAGS = {
  running: <Tag color="green">运行中</Tag>,
  stopping: <Tag color="orange">停止中</Tag>,
  stopped: <Tag>已停止</Tag>,
}
const ACCOUNT_STATUS_TAGS = {
  starting: <Tag color="blue">启动中</Tag>,
  waiting: <Tag color="gold">等待中</Tag>,
  running: <Tag color="green">运行中</Tag>,
  stopping: <Tag color="orange">停止中</Tag>,
  stopped: <Tag>已停止</Tag>,
  blocked: <Tag color="red">已阻止</Tag>,
  error: <Tag color="red">异常</Tag>,
}

function normalizeConfig(cfg = {}) {
  const enabled = Array.from({ length: 3 }, (_, index) => (cfg.enabled_positions || [true, true, true])[index] !== false)
  const counts = Array.from({ length: 3 }, (_, index) => {
    const value = Number((cfg.random_counts || [5, 5, 5])[index])
    return Number.isInteger(value) && value >= 1 && value <= 9 ? value : 5
  })
  const steps = (cfg.amount_steps || []).map(Number).filter((value) => Number.isFinite(value) && value > 0).slice(0, 20)
  return { ...cfg, enabled_positions: enabled, random_counts: counts, amount_steps: steps.length ? steps : DEFAULT_STEPS }
}

function normalizeForSave(values) {
  return {
    ...values,
    enabled_positions: Array.from({ length: 3 }, (_, index) => (values.enabled_positions || [true, true, true])[index] !== false),
    random_counts: Array.from({ length: 3 }, (_, index) => Math.min(9, Math.max(1, Math.round(Number((values.random_counts || [])[index]) || 5)))),
    amount_steps: (values.amount_steps || []).map(Number).filter((value) => Number.isFinite(value) && value > 0).slice(0, 20).map(Math.round),
  }
}

function nextAlarm(value) {
  const match = /^\s*(\d{1,2}):(\d{1,2})\s*$/.exec(value || '')
  if (!match) return null
  const date = new Date()
  date.setHours(Number(match[1]), Number(match[2]), 0, 0)
  if (Number(match[1]) > 23 || Number(match[2]) > 59) return null
  if (date <= new Date()) date.setDate(date.getDate() + 1)
  return date
}

export default function RandomRotateBetPage() {
  const [form] = Form.useForm()
  const [status, setStatus] = useState('stopped')
  const [accounts, setAccounts] = useState([])
  const [saving, setSaving] = useState(false)
  const [loading, setLoading] = useState(false)
  const startMode = Form.useWatch('start_mode', form) || 'now'
  const amountSteps = Form.useWatch('amount_steps', form) || []
  const accountList = Form.useWatch('accounts', form) || []
  const isRunning = status === 'running'
  const hasMultipleAccounts = accountList.length > 1

  const refresh = async () => {
    const [allStatus, accountStatus] = await Promise.all([
      api.getStatus(),
      api.getRandomRotateBetAccountStatuses(),
    ])
    setStatus(allStatus.random_rotatebet || 'stopped')
    setAccounts(accountStatus.accounts || [])
  }

  useEffect(() => {
    api.getRandomRotateBetConfig().then((cfg) => form.setFieldsValue(normalizeConfig(cfg)))
    refresh()
    const timer = setInterval(refresh, 3000)
    return () => clearInterval(timer)
  }, [])

  const validate = (cfg) => {
    if (!cfg.enabled_positions.some(Boolean)) {
      message.error('至少启用一路球')
      return false
    }
    if (!cfg.amount_steps.length || cfg.amount_steps.length > 20) {
      message.error('金额阶梯需要 1 到 20 阶')
      return false
    }
    return true
  }

  const save = async (overrides = {}) => {
    try {
      const values = await form.validateFields()
      const cfg = { ...normalizeForSave(values), ...overrides }
      if (!validate(cfg)) return false
      setSaving(true)
      const result = await api.saveRandomRotateBetConfig(cfg)
      if (!result.ok) {
        message.error(result.message || '保存失败')
        return false
      }
      message.success('配置已保存')
      return true
    } catch (error) {
      const first = error?.errorFields?.[0]
      if (first?.name) form.scrollToField(first.name, { block: 'center' })
      message.error(first?.errors?.[0] || '请检查表单填写')
      return false
    } finally {
      setSaving(false)
    }
  }

  const start = async () => {
    setLoading(true)
    const result = await api.startRandomRotateBet()
    setLoading(false)
    message.info(result.message)
    await refresh()
  }

  const startNow = async () => {
    form.setFieldsValue({ start_mode: 'now' })
    if (await save({ start_mode: 'now' })) await start()
  }

  const startScheduled = () => {
    form.setFieldsValue({ start_mode: 'scheduled' })
    const target = nextAlarm(form.getFieldValue('start_time'))
    if (!target) {
      message.error('开跑时刻格式不对，应为 08:00')
      return
    }
    Modal.confirm({
      title: '确认闹钟定时启动？',
      content: `将于 ${target.toLocaleString()} 开始运行随机码追损。`,
      okText: '确认启动',
      cancelText: '取消',
      onOk: async () => { if (await save({ start_mode: 'scheduled' })) await start() },
    })
  }

  const saveAccounts = async () => {
    if (!(await save())) return
    if (isRunning) {
      message.success('新增账号已保存并加入当前会话')
      await refresh()
      return
    }
    await start()
  }

  const accountColumns = [
    { title: '账号', dataIndex: 'account', render: (value) => value || '-' },
    { title: '端口', dataIndex: 'port', width: 84 },
    { title: '状态', dataIndex: 'status', width: 92, render: (value) => ACCOUNT_STATUS_TAGS[value] || <Tag>{value || '-'}</Tag> },
    { title: '说明', dataIndex: 'message', ellipsis: true },
    { title: '操作', width: 88, render: (_, row) => <Button size="small" danger icon={<StopOutlined />} disabled={!['starting', 'waiting', 'running'].includes(row.status)} onClick={async () => { const result = await api.stopRandomRotateBetAccount(row.key); message.info(result.message); refresh() }}>停止</Button> },
  ]

  return (
    <div style={{ padding: 24 }}>
      <Space style={{ marginBottom: 24, width: '100%', justifyContent: 'space-between' }} align="center">
        <Space>
          <Title level={4} style={{ margin: 0 }}>随机码追损</Title>
          {STATUS_TAGS[status] || STATUS_TAGS.stopped}
        </Space>
        <Space>
          <Button type="primary" icon={<PlayCircleOutlined />} onClick={startNow} loading={loading} disabled={isRunning}>保存并随开随跑</Button>
          <Button icon={<ClockCircleOutlined />} onClick={startScheduled} loading={loading} disabled={isRunning}>保存并定时启动</Button>
          <Button danger icon={<PauseCircleOutlined />} onClick={async () => { setLoading(true); const result = await api.stopRandomRotateBet(); setLoading(false); message.info(result.message); refresh() }} loading={loading} disabled={!isRunning}>停止</Button>
        </Space>
      </Space>

      <Row gutter={24}>
        <Col xs={24} lg={14}>
          <Card title="参数配置" style={{ borderRadius: 8 }}>
            <Form form={form} layout="vertical">
              <Collapse defaultActiveKey={['basic', 'accounts', 'numbers', 'amounts', 'start', 'risk']} ghost forceRender>
                <Panel header="网站设置" key="basic">
                  <Form.Item label="入口网址" name="entry_url" rules={[{ required: true }]}><Input placeholder="https://166.tt" /></Form.Item>
                  <Form.Item label="安全码" name="safe_code"><Input placeholder="没有则留空" /></Form.Item>
                </Panel>
                <Panel header="账号列表" key="accounts">
                  <Form.List name="accounts">{(fields, { add, remove }) => <>
                    {fields.map(({ key, name }) => <div key={key} style={{ border: '1px solid #f0f0f0', borderRadius: 8, padding: 12, marginBottom: 8, background: '#fafafa' }}><Row gutter={8} align="middle">
                      <Col xs={24} md={7}><Form.Item name={[name, 'account']} label="账号" rules={[{ required: true }]}><Input /></Form.Item></Col>
                      <Col xs={24} md={7}><Form.Item name={[name, 'password']} label="密码" rules={[{ required: true }]}><Input.Password /></Form.Item></Col>
                      <Col xs={18} md={6}><Form.Item name={[name, 'port']} label="浏览器端口" rules={[{ required: true }]}><InputNumber min={1} style={{ width: '100%' }} /></Form.Item></Col>
                      <Col xs={6} md={4}>{fields.length > 1 && <Button danger icon={<MinusCircleOutlined />} onClick={() => remove(name)} />}</Col>
                    </Row></div>)}
                    <Button block type="dashed" icon={<PlusOutlined />} onClick={() => { const existing = form.getFieldValue('accounts') || []; const ports = existing.map((item) => Number(item?.port)).filter(Number.isFinite); add({ account: '', password: '', port: ports.length ? Math.max(...ports) + 1 : 9222 }) }}>添加账号</Button>
                  </>}</Form.List>
                </Panel>
                <Panel header="随机选码" key="numbers">
                  <Typography.Paragraph type="secondary">每期下注前，程序会从 0 至 9 中无重复随机抽取号码。同一期内全部运行账号使用相同号码。</Typography.Paragraph>
                  <Row gutter={12}>{BALL_LABELS.map((label, index) => <Col xs={24} md={8} key={label}><Card size="small" title={label} style={{ marginBottom: 10 }}><Form.Item label="随机号码数量" name={['random_counts', index]} rules={[{ required: true, type: 'number', min: 1, max: 9 }]}><InputNumber min={1} max={9} precision={0} style={{ width: '100%' }} addonAfter="个" /></Form.Item><Form.Item name={['enabled_positions', index]} valuePropName="checked" initialValue><Switch checkedChildren="启用" unCheckedChildren="关闭" /></Form.Item></Card></Col>)}</Row>
                </Panel>
                <Panel header="追损参数" key="amounts">
                  <Form.Item label="入场触发条件" name="entry_miss_trigger"><Radio.Group optionType="button" buttonStyle="solid"><Radio.Button value={0}>直接开始</Radio.Button><Radio.Button value={1}>一次不中</Radio.Button><Radio.Button value={2}>两次不中</Radio.Button><Radio.Button value={3}>三次不中</Radio.Button></Radio.Group></Form.Item>
                  <Form.List name="amount_steps">{(fields, { add, remove }) => <><Row gutter={8}>{fields.map(({ key, name }) => <Col xs={12} md={8} key={key}><Space.Compact style={{ width: '100%', marginBottom: 8 }}><Form.Item name={name} rules={[{ required: true, type: 'number', min: 1 }]} style={{ marginBottom: 0, flex: 1 }}><InputNumber addonBefore={`${name + 1}阶`} min={1} precision={0} style={{ width: '100%' }} /></Form.Item>{fields.length > 1 && <Button danger icon={<MinusCircleOutlined />} onClick={() => remove(name)} />}</Space.Compact></Col>)}</Row><Button block type="dashed" icon={<PlusOutlined />} disabled={amountSteps.length >= 20} onClick={() => { const last = Number((form.getFieldValue('amount_steps') || []).slice(-1)[0]) || 100; add(Math.max(1, Math.round(last * 1.3))) }}>添加阶梯</Button></>}</Form.List>
                </Panel>
                <Panel header="启动方式" key="start"><Form.Item label="启动方式" name="start_mode"><Radio.Group optionType="button" buttonStyle="solid" disabled={isRunning}><Radio.Button value="now">随开随跑</Radio.Button><Radio.Button value="scheduled">闹钟定时</Radio.Button></Radio.Group></Form.Item>{startMode === 'scheduled' && <Form.Item label="开跑时刻" name="start_time" rules={[{ required: true, pattern: /^([01]?\d|2[0-3]):[0-5]\d$/, message: '格式如 08:00' }]}><Input style={{ width: 160 }} /></Form.Item>}</Panel>
                <Panel header="止盈止损" key="risk"><Row gutter={16}><Col xs={24} md={12}><Form.Item label="止盈 (元)" name="take_profit" rules={[{ required: true }]}><InputNumber min={0} style={{ width: '100%' }} /></Form.Item></Col><Col xs={24} md={12}><Form.Item label="止损 (元)" name="daily_stop_loss" rules={[{ required: true }]}><InputNumber min={0} style={{ width: '100%' }} /></Form.Item></Col></Row></Panel>
              </Collapse>
              <Divider />
              <Button block type={hasMultipleAccounts ? 'primary' : 'default'} onClick={hasMultipleAccounts ? saveAccounts : () => save()} loading={saving || (hasMultipleAccounts && loading)}>{hasMultipleAccounts ? '保存并运行新增账号' : '仅保存配置'}</Button>
              {hasMultipleAccounts && <Text type="secondary" style={{ display: 'block', marginTop: 10 }}>运行中新增账号不会重启原账号；新账号从第 1 阶开始，登录后等待下一完整投注周期加入。</Text>}
            </Form>
          </Card>
        </Col>
        <Col xs={24} lg={10}>
          <Card title="账号运行状态" style={{ borderRadius: 8, marginBottom: 24 }}><Table size="small" rowKey="key" columns={accountColumns} dataSource={accounts} pagination={false} scroll={{ x: 650 }} /></Card>
          <Card title="实时日志" style={{ borderRadius: 8 }}><LogViewer taskId="random_rotatebet" running={isRunning} /></Card>
        </Col>
      </Row>
    </div>
  )
}
