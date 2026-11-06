import React, { useEffect, useState } from 'react'
import {
  Alert, Button, Card, Collapse, Divider, Form, Input, InputNumber, message,
  Modal, Radio, Row, Col, Space, Switch, Table, Tag, Typography,
} from 'antd'
import {
  ClockCircleOutlined, MinusCircleOutlined, PauseCircleOutlined,
  PlayCircleOutlined, PlusOutlined, StopOutlined,
} from '@ant-design/icons'
import { api } from '../api/client'
import LogViewer from '../components/LogViewer'

const { Title, Text } = Typography
const { Panel } = Collapse

const STATUS_TAGS = {
  running: <Tag color="green">运行中</Tag>, stopping: <Tag color="orange">停止中</Tag>, stopped: <Tag>已停止</Tag>,
}
const ACCOUNT_STATUS_TAGS = {
  starting: <Tag color="blue">启动中</Tag>, waiting: <Tag color="gold">等待中</Tag>, running: <Tag color="green">运行中</Tag>,
  stopping: <Tag color="orange">停止中</Tag>, stopped: <Tag>已停止</Tag>, blocked: <Tag color="red">已阻止</Tag>, error: <Tag color="red">异常</Tag>,
}

function nextAlarm(value) {
  const match = /^\s*(\d{1,2}):(\d{1,2})\s*$/.exec(value || '')
  if (!match) return null
  const date = new Date()
  const hour = Number(match[1]); const minute = Number(match[2])
  if (hour > 23 || minute > 59) return null
  date.setHours(hour, minute, 0, 0)
  if (date <= new Date()) date.setDate(date.getDate() + 1)
  return date
}

function normalizeConfig(cfg = {}) {
  return {
    ...cfg,
    accounts: Array.isArray(cfg.accounts) && cfg.accounts.length ? cfg.accounts : [{ account: '', password: '', port: 9222 }],
    enabled_positions: Array.isArray(cfg.enabled_positions) && cfg.enabled_positions.length === 3 ? cfg.enabled_positions : [true, true, true],
  }
}

export default function NineCodePickBetPage() {
  const [form] = Form.useForm()
  const [status, setStatus] = useState('stopped')
  const [accounts, setAccounts] = useState([])
  const [saving, setSaving] = useState(false)
  const [loading, setLoading] = useState(false)
  const startMode = Form.useWatch('start_mode', form) || 'now'
  const accountList = Form.useWatch('accounts', form) || []
  const enabled = Form.useWatch('enabled_positions', form) || [true, true, true]
  const amount = Number(Form.useWatch('base_bet_amount', form) || 0)
  const isRunning = status === 'running'
  const hasMultipleAccounts = accountList.length > 1
  const totalPerIssue = amount * 9 * enabled.filter(Boolean).length

  const refresh = async () => {
    const [allStatus, accountStatus] = await Promise.all([api.getStatus(), api.getNineCodePickBetAccountStatuses()])
    setStatus(allStatus.nine_code_pickbet || 'stopped')
    setAccounts(accountStatus.accounts || [])
  }

  useEffect(() => {
    api.getNineCodePickBetConfig().then((cfg) => form.setFieldsValue(normalizeConfig(cfg)))
    refresh()
    const timer = setInterval(refresh, 3000)
    return () => clearInterval(timer)
  }, [])

  const save = async (overrides = {}) => {
    try {
      const cfg = { ...(await form.validateFields()), ...overrides }
      if (!(cfg.enabled_positions || []).some(Boolean)) {
        message.error('至少开启一个球位')
        return false
      }
      setSaving(true)
      const result = await api.saveNineCodePickBetConfig(cfg)
      if (!result.ok) { message.error(result.message || '保存失败'); return false }
      message.success('配置已保存')
      return true
    } catch (error) {
      const first = error?.errorFields?.[0]
      if (first?.name) form.scrollToField(first.name, { block: 'center' })
      message.error(first?.errors?.[0] || '请检查表单填写')
      return false
    } finally { setSaving(false) }
  }

  const start = async () => {
    setLoading(true)
    const result = await api.startNineCodePickBet()
    setLoading(false)
    message.info(result.message)
    await refresh()
  }
  const startNow = async () => { form.setFieldsValue({ start_mode: 'now' }); if (await save({ start_mode: 'now' })) await start() }
  const startScheduled = () => {
    form.setFieldsValue({ start_mode: 'scheduled' })
    const target = nextAlarm(form.getFieldValue('start_time'))
    if (!target) { message.error('开跑时刻格式不对，应为 08:00'); return }
    Modal.confirm({
      title: '确认闹钟定时启动？', content: `将于 ${target.toLocaleString()} 开始运行1106私订九粒选码。`, okText: '确认启动', cancelText: '取消',
      onOk: async () => { if (await save({ start_mode: 'scheduled' })) await start() },
    })
  }
  const saveAccounts = async () => { if (!(await save())) return; if (isRunning) { message.success('新增账号已保存并加入当前会话'); await refresh() } else await start() }

  const columns = [
    { title: '账号', dataIndex: 'account', render: (value) => value || '-' },
    { title: '端口', dataIndex: 'port', width: 84 },
    { title: '状态', dataIndex: 'status', width: 92, render: (value) => ACCOUNT_STATUS_TAGS[value] || <Tag>{value || '-'}</Tag> },
    { title: '说明', dataIndex: 'message', ellipsis: true },
    { title: '操作', width: 88, render: (_, row) => <Button size="small" danger icon={<StopOutlined />} disabled={!['starting', 'waiting', 'running'].includes(row.status)} onClick={async () => { const result = await api.stopNineCodePickBetAccount(row.key); message.info(result.message); refresh() }}>停止</Button> },
  ]

  return <div style={{ padding: 24 }}>
    <Space style={{ marginBottom: 24, width: '100%', justifyContent: 'space-between' }} align="center">
      <Space><Title level={4} style={{ margin: 0 }}>1106私订九粒选码</Title>{STATUS_TAGS[status] || STATUS_TAGS.stopped}</Space>
      <Space>
        <Button type="primary" icon={<PlayCircleOutlined />} onClick={startNow} loading={loading} disabled={isRunning}>保存并随开随跑</Button>
        <Button icon={<ClockCircleOutlined />} onClick={startScheduled} loading={loading} disabled={isRunning}>保存并定时启动</Button>
        <Button danger icon={<PauseCircleOutlined />} onClick={async () => { setLoading(true); const result = await api.stopNineCodePickBet(); setLoading(false); message.info(result.message); refresh() }} loading={loading} disabled={!isRunning}>停止</Button>
      </Space>
    </Space>
    <Row gutter={24}>
      <Col xs={24} lg={14}><Card title="参数配置" style={{ borderRadius: 8 }}><Form form={form} layout="vertical">
        <Alert type="info" showIcon style={{ marginBottom: 16 }} message="规则：先观察一期；随后按上一期开奖数字依次 +1、+2、+5、+6、+9 排除，每球投注其余9码，然后回到 +1。金额固定，不追损。" />
        <Collapse defaultActiveKey={['basic', 'accounts', 'rule', 'amount', 'start', 'risk']} ghost forceRender>
          <Panel header="网站设置" key="basic"><Form.Item label="入口网址" name="entry_url" rules={[{ required: true }]}><Input placeholder="https://166.tt" /></Form.Item><Form.Item label="安全码" name="safe_code"><Input placeholder="没有则留空" /></Form.Item></Panel>
          <Panel header="账号列表" key="accounts"><Form.List name="accounts">{(fields, { add, remove }) => <>{fields.map(({ key, name }) => <div key={key} style={{ border: '1px solid #f0f0f0', borderRadius: 8, padding: 12, marginBottom: 8, background: '#fafafa' }}><Row gutter={8} align="middle"><Col xs={24} md={7}><Form.Item name={[name, 'account']} label="账号" rules={[{ required: true }]}><Input /></Form.Item></Col><Col xs={24} md={7}><Form.Item name={[name, 'password']} label="密码" rules={[{ required: true }]}><Input.Password /></Form.Item></Col><Col xs={18} md={6}><Form.Item name={[name, 'port']} label="浏览器端口" rules={[{ required: true }]}><InputNumber min={1} style={{ width: '100%' }} /></Form.Item></Col><Col xs={6} md={4}>{fields.length > 1 && <Button danger icon={<MinusCircleOutlined />} onClick={() => remove(name)} />}</Col></Row></div>)}<Button block type="dashed" icon={<PlusOutlined />} onClick={() => { const values = form.getFieldValue('accounts') || []; const ports = values.map((item) => Number(item?.port)).filter(Number.isFinite); add({ account: '', password: '', port: ports.length ? Math.max(...ports) + 1 : 9222 }) }}>添加账号</Button></>}</Form.List></Panel>
          <Panel header="九粒选号规则" key="rule"><Typography.Paragraph>每个开启球位均使用上一期该球开奖号码计算：<Text code>排除 = (上一期号码 + 当前偏移) mod 10</Text>，再投注剩余 9 个号码。偏移顺序固定为 <Text strong>+1、+2、+5、+6、+9、+1</Text>。</Typography.Paragraph><Form.Item label="开启球位" name="enabled_positions"><Switch checkedChildren="第一球" unCheckedChildren="第一球" checked={enabled[0]} onChange={(value) => form.setFieldValue('enabled_positions', [value, enabled[1], enabled[2]])} /><Switch style={{ marginLeft: 12 }} checkedChildren="第二球" unCheckedChildren="第二球" checked={enabled[1]} onChange={(value) => form.setFieldValue('enabled_positions', [enabled[0], value, enabled[2]])} /><Switch style={{ marginLeft: 12 }} checkedChildren="第三球" unCheckedChildren="第三球" checked={enabled[2]} onChange={(value) => form.setFieldValue('enabled_positions', [enabled[0], enabled[1], value])} /></Form.Item></Panel>
          <Panel header="固定金额" key="amount"><Form.Item label="每个号码下注金额（元）" name="base_bet_amount" rules={[{ required: true, type: 'number', min: 1 }]}><InputNumber min={1} precision={0} style={{ width: 240 }} /></Form.Item><Text type="secondary">当前开启 {enabled.filter(Boolean).length} 个球位：每期开启球位各投 9 码，预计总注 {totalPerIssue} 元。不设追损阶梯。</Text></Panel>
          <Panel header="启动方式" key="start"><Form.Item label="启动方式" name="start_mode"><Radio.Group optionType="button" buttonStyle="solid" disabled={isRunning}><Radio.Button value="now">随开随跑</Radio.Button><Radio.Button value="scheduled">闹钟定时</Radio.Button></Radio.Group></Form.Item>{startMode === 'scheduled' && <Form.Item label="开跑时刻" name="start_time" rules={[{ required: true, pattern: /^([01]?\d|2[0-3]):[0-5]\d$/, message: '格式如 08:00' }]}><Input style={{ width: 160 }} /></Form.Item>}</Panel>
          <Panel header="止盈止损" key="risk"><Row gutter={16}><Col xs={24} md={12}><Form.Item label="止盈 (元)" name="take_profit" rules={[{ required: true }]}><InputNumber min={0} style={{ width: '100%' }} /></Form.Item></Col><Col xs={24} md={12}><Form.Item label="止损 (元)" name="daily_stop_loss" rules={[{ required: true }]}><InputNumber min={0} style={{ width: '100%' }} /></Form.Item></Col></Row></Panel>
        </Collapse><Divider /><Button block type={hasMultipleAccounts ? 'primary' : 'default'} onClick={hasMultipleAccounts ? saveAccounts : () => save()} loading={saving || (hasMultipleAccounts && loading)}>{hasMultipleAccounts ? '保存并运行新增账号' : '仅保存配置'}</Button>{hasMultipleAccounts && <Text type="secondary" style={{ display: 'block', marginTop: 10 }}>运行中新增账号不会重启原账号；新账号登录后同样先观察一完整期再进入投注。</Text>}</Form></Card></Col>
      <Col xs={24} lg={10}><Card title="账号运行状态" style={{ borderRadius: 8, marginBottom: 24 }}><Table size="small" rowKey="key" columns={columns} dataSource={accounts} pagination={false} scroll={{ x: 650 }} /></Card><Card title="实时日志" style={{ borderRadius: 8 }}><LogViewer taskId="nine_code_pickbet" running={isRunning} /></Card></Col>
    </Row>
  </div>
}
