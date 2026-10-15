import React, { useEffect, useMemo, useState } from 'react'
import {
  Alert, Button, Card, Col, Empty, Form, Input, InputNumber, Row, Space,
  Statistic, Switch, Table, Tag, Typography, message,
} from 'antd'
import { BarChartOutlined, ChromeOutlined, DatabaseOutlined, PlusOutlined } from '@ant-design/icons'
import { api } from '../api/client'

const { Title, Text, Paragraph } = Typography
const { TextArea } = Input
const BALLS = ['第一球', '第二球', '第三球']

function parseNumbers(value) {
  return String(value || '')
    .split(/[，,\s]+/)
    .map((item) => Number(item.trim()))
    .filter((item, index, all) => Number.isInteger(item) && item >= 0 && item <= 9 && all.indexOf(item) === index)
}

function parseSteps(value) {
  return (Array.isArray(value) ? value : [])
    .map((item) => Number(item))
    .filter((item) => Number.isFinite(item) && item > 0)
    .map((item) => Math.round(item))
    .slice(0, 20)
}

function toForm(config = {}) {
  const sets = Array.from({ length: 3 }, (_, index) => {
    const row = config.number_sets?.[index] || { set_a: [0, 1, 3, 5, 8], set_b: [2, 4, 6, 7, 9] }
    return { set_a: (row.set_a || []).join(','), set_b: (row.set_b || []).join(',') }
  })
  return {
    amount_steps: parseSteps(config.amount_steps).length ? parseSteps(config.amount_steps) : [99, 232, 582, 1498],
    number_sets: sets,
    enabled_positions: Array.from({ length: 3 }, (_, index) => config.enabled_positions?.[index] !== false),
    entry_miss_trigger: Number.isInteger(config.entry_miss_trigger) ? config.entry_miss_trigger : 2,
    odds: Number.isFinite(Number(config.odds)) && Number(config.odds) > 0 ? Number(config.odds) : 9.927,
    rebate_rate: Number.isFinite(Number(config.rebate_rate)) ? Number(config.rebate_rate) : 0.0051,
  }
}

function toConfig(values) {
  return {
    amount_steps: parseSteps(values.amount_steps),
    number_sets: (values.number_sets || []).map((row) => ({
      set_a: parseNumbers(row?.set_a),
      set_b: parseNumbers(row?.set_b),
    })),
    enabled_positions: Array.from({ length: 3 }, (_, index) => values.enabled_positions?.[index] !== false),
    entry_miss_trigger: Math.max(0, Math.round(Number(values.entry_miss_trigger) || 0)),
    odds: Number(values.odds) || 9.927,
    rebate_rate: Math.max(0, Number(values.rebate_rate) || 0),
  }
}

function recordsToText(records) {
  return [...(records || [])]
    .sort((a, b) => String(a.issue || '').localeCompare(String(b.issue || ''), undefined, { numeric: true }))
    .map((row) => `${row.issue || ''} ${(row.numbers || []).slice(0, 3).join(' ')}`.trim())
    .filter(Boolean)
    .join('\n')
}

function money(value) {
  const number = Number(value) || 0
  return `${number > 0 ? '+' : ''}${number.toFixed(2)}`
}

function percentage(value) {
  return `${((Number(value) || 0) * 100).toFixed(2)}%`
}

function sourceText(source) {
  if (!source) return ''
  const parts = [source.label || '开奖记录']
  if (source.record_count) parts.push(`${source.record_count} 条`)
  if (source.first_issue || source.last_issue) parts.push(`${source.first_issue || '-'} 至 ${source.last_issue || '-'}`)
  return parts.join('；')
}

export default function CustomRotateBacktestPage() {
  const [form] = Form.useForm()
  const numberSets = Form.useWatch('number_sets', form) || []
  const [recordText, setRecordText] = useState('')
  const [recordLimit, setRecordLimit] = useState(500)
  const [capturePort, setCapturePort] = useState(9333)
  const [loading, setLoading] = useState(false)
  const [capturing, setCapturing] = useState(false)
  const [openingBrowser, setOpeningBrowser] = useState(false)
  const [result, setResult] = useState(null)

  useEffect(() => {
    api.getCustomRotateBetConfig().then((config) => form.setFieldsValue(toForm(config)))
  }, [form])

  const validateConfig = (config) => {
    if (!config.amount_steps.length) return '至少填写一个金额阶段'
    if (!config.enabled_positions.some(Boolean)) return '至少启用一路球'
    for (let index = 0; index < 3; index += 1) {
      const row = config.number_sets[index] || {}
      if (!row.set_a?.length || !row.set_b?.length) return `${BALLS[index]}的 A/B 组至少填写一个号码`
    }
    return ''
  }

  const runBacktest = async (nextText = recordText) => {
    try {
      const values = await form.validateFields()
      const config = toConfig(values)
      const invalid = validateConfig(config)
      if (invalid) {
        message.error(invalid)
        return
      }
      setLoading(true)
      const data = await api.backtestCustomRotateBet({ config, text: nextText, limit: recordLimit })
      setResult(data)
      if (data?.ok === false) message.warning(data.message || '回测失败')
      else message.success('回测已完成')
    } finally {
      setLoading(false)
    }
  }

  const captureRecords = async () => {
    setCapturing(true)
    try {
      const data = await api.captureDrawRecords({ port: capturePort, limit: recordLimit, save: true })
      if (data?.ok === false) {
        message.error(data.message || '开奖记录抓取失败')
        return
      }
      const nextText = recordsToText(data.records || [])
      setRecordText(nextText)
      message.success(`${data.message || '抓取完成'}，已载入 ${data.records?.length || 0} 条记录`)
    } finally {
      setCapturing(false)
    }
  }

  const openCaptureBrowser = async () => {
    setOpeningBrowser(true)
    try {
      const data = await api.openDrawAnalysisBrowser(capturePort)
      if (data?.ok === false) {
        message.error(data.message || '打开浏览器失败')
      } else {
        message.success(data.message || `已打开 ${capturePort} 端口浏览器`)
      }
    } finally {
      setOpeningBrowser(false)
    }
  }

  const loadSavedRecords = async () => {
    setLoading(true)
    try {
      const data = await api.getSavedDrawRecords(recordLimit)
      if (!data.records?.length) {
        message.warning('还没有已保存的开奖记录，请先到开奖记录分析页面抓取。')
        return
      }
      const nextText = data.text || recordsToText(data.records)
      setRecordText(nextText)
      message.success(`已读取 ${data.records.length} 条开奖记录`)
    } finally {
      setLoading(false)
    }
  }

  const columns = useMemo(() => [
    { title: '期号', dataIndex: 'issue', width: 110 },
    { title: '球路', dataIndex: 'name', width: 78 },
    { title: '开奖', dataIndex: 'winning_number', width: 62 },
    { title: '组别', dataIndex: 'set', width: 62, render: (value) => <Tag color={value === 'A' ? 'blue' : 'purple'}>{value}</Tag> },
    { title: '下注号码', dataIndex: 'numbers', width: 150, render: (values) => (values || []).join(',') },
    { title: '档位', dataIndex: 'tier', width: 62, render: (value) => `${value}档` },
    { title: '每号金额', dataIndex: 'amount_per_number', width: 90 },
    { title: '本期总下注', dataIndex: 'stake', width: 92 },
    { title: '结果', dataIndex: 'result', width: 72, render: (value) => <Tag color={value === '命中' ? 'green' : 'red'}>{value}</Tag> },
    { title: '返奖', dataIndex: 'payout', width: 88 },
    { title: '退水', dataIndex: 'rebate', width: 76 },
    { title: '本期盈亏', dataIndex: 'profit', width: 95, render: (value) => <Tag color={Number(value) > 0 ? 'green' : 'red'}>{money(value)}</Tag> },
    { title: '累计盈亏', dataIndex: 'cumulative_profit', width: 102, fixed: 'right', render: (value) => <Tag color={Number(value) > 0 ? 'green' : Number(value) < 0 ? 'red' : 'default'}>{money(value)}</Tag> },
  ], [])

  const summary = result?.summary
  return (
    <div style={{ padding: 24 }}>
      <Space style={{ marginBottom: 24, width: '100%', justifyContent: 'space-between' }} align="center" wrap>
        <Space>
          <BarChartOutlined />
          <Title level={4} style={{ margin: 0 }}>轮换追损回测计算器</Title>
        </Space>
        <Button type="primary" icon={<BarChartOutlined />} loading={loading} onClick={() => runBacktest()}>
          开始回测
        </Button>
      </Space>

      <Alert
        showIcon
        type="info"
        style={{ marginBottom: 16 }}
        message="回测只根据历史开奖重放当前轮换追损规则，不会下单，也不会更改实盘配置。金额为每个号码下注金额；退水填写小数，例如 0.51% 填 0.0051。"
      />

      <Row gutter={[16, 16]}>
        <Col xs={24} xl={9}>
          <Card title="开奖记录" style={{ borderRadius: 8, marginBottom: 16 }}>
            <Space wrap style={{ marginBottom: 12 }}>
              <Space direction="vertical" size={4}><Text type="secondary">浏览器端口</Text><InputNumber min={1} max={65535} value={capturePort} onChange={(value) => setCapturePort(value || 9333)} /></Space>
              <Space direction="vertical" size={4}><Text type="secondary">记录上限</Text><InputNumber min={2} max={5000} value={recordLimit} onChange={(value) => setRecordLimit(value || 500)} /></Space>
            </Space>
            <Space wrap style={{ marginBottom: 12 }}>
              <Button icon={<ChromeOutlined />} loading={openingBrowser} onClick={openCaptureBrowser}>打开抓取浏览器</Button>
              <Button icon={<ChromeOutlined />} loading={capturing} onClick={captureRecords}>从已登录浏览器抓取</Button>
              <Button icon={<DatabaseOutlined />} loading={loading} onClick={loadSavedRecords}>读取已保存记录</Button>
            </Space>
            <TextArea value={recordText} onChange={(event) => setRecordText(event.target.value)} autoSize={{ minRows: 14, maxRows: 28 }} placeholder="每行：期号 第一球 第二球 第三球" />
            <Paragraph type="secondary" style={{ margin: '10px 0 0', fontSize: 12 }}>先点击“打开抓取浏览器”并登录、进入开奖结果页面，再点击抓取。也可从“开奖记录分析推荐”先抓取并保存，再在这里读取。</Paragraph>
          </Card>

          <Card title="回测规则" style={{ borderRadius: 8 }}>
            <Form form={form} layout="vertical" initialValues={toForm()}>
              <Row gutter={12}>
                <Col span={8}><Form.Item label="连续未中后入场" name="entry_miss_trigger"><InputNumber min={0} max={99} style={{ width: '100%' }} /></Form.Item></Col>
                <Col span={8}><Form.Item label="单号赔率（含本金）" name="odds"><InputNumber min={0.01} step={0.001} style={{ width: '100%' }} /></Form.Item></Col>
                <Col span={8}><Form.Item label="退水率" name="rebate_rate"><InputNumber min={0} max={1} step={0.0001} style={{ width: '100%' }} /></Form.Item></Col>
              </Row>
              <Text strong>金额阶段（每个号码）</Text>
              <Form.List name="amount_steps">
                {(fields, { add, remove }) => <Space wrap style={{ display: 'flex', margin: '8px 0 16px' }}>
                  {fields.map((field, index) => <Space key={field.key}><Form.Item {...field} noStyle><InputNumber min={1} placeholder={`${index + 1}档`} /></Form.Item><Button size="small" danger onClick={() => remove(field.name)}>×</Button></Space>)}
                  <Button icon={<PlusOutlined />} size="small" onClick={() => add(100)}>添加档位</Button>
                </Space>}
              </Form.List>
              {numberSets.map((_, index) => (
                <Card key={index} size="small" title={BALLS[index]} style={{ marginBottom: 10 }} extra={<Form.Item name={['enabled_positions', index]} valuePropName="checked" noStyle><Switch checkedChildren="启用" unCheckedChildren="关闭" /></Form.Item>}>
                  <Row gutter={8}>
                    <Col span={12}><Form.Item label="A 组号码" name={['number_sets', index, 'set_a']} style={{ marginBottom: 0 }}><Input placeholder="0,1,3,5,8,9" /></Form.Item></Col>
                    <Col span={12}><Form.Item label="B 组号码" name={['number_sets', index, 'set_b']} style={{ marginBottom: 0 }}><Input placeholder="0,1,3,5,8,9" /></Form.Item></Col>
                  </Row>
                </Card>
              ))}
            </Form>
          </Card>
        </Col>

        <Col xs={24} xl={15}>
          {result?.ok === false && <Alert type="warning" showIcon message={result.message} style={{ marginBottom: 16 }} />}
          {result?.ok ? <>
            <Row gutter={[12, 12]} style={{ marginBottom: 16 }}>
              <Col xs={12} md={6}><Card><Statistic title="回测记录" value={summary.records} /></Card></Col>
              <Col xs={12} md={6}><Card><Statistic title="实际下注" value={summary.bets} /></Card></Col>
              <Col xs={12} md={6}><Card><Statistic title="总下注" value={summary.stake} precision={2} /></Card></Col>
              <Col xs={12} md={6}><Card><Statistic title="累计盈亏" value={summary.profit} precision={2} valueStyle={{ color: Number(summary.profit) >= 0 ? '#3f8600' : '#cf1322' }} /></Card></Col>
            </Row>
            <Card title="回测汇总" style={{ borderRadius: 8, marginBottom: 16 }}>
              <Space wrap size={[16, 10]}>
                <Text>命中 {summary.wins}/{summary.bets}（{percentage(summary.hit_rate)}）</Text>
                <Text>返奖 {Number(summary.payout).toFixed(2)}</Text>
                <Text>退水 {Number(summary.rebate).toFixed(2)}</Text>
                <Text>ROI {percentage(summary.roi)}</Text>
                <Text>结算期数 {summary.settled_issues}</Text>
              </Space>
              {result.record_source && <Alert type="info" showIcon style={{ marginTop: 12 }} message={`数据来源：${sourceText(result.record_source)}`} />}
            </Card>
            <Card title="逐期回测账单" style={{ borderRadius: 8 }}>
              <Table size="small" rowKey="key" columns={columns} dataSource={result.ledger || []} pagination={{ pageSize: 20, showSizeChanger: true }} scroll={{ x: 1350 }} />
            </Card>
          </> : <Card style={{ borderRadius: 8 }}><Empty description="填写参数并载入开奖记录后开始回测" /></Card>}
        </Col>
      </Row>
    </div>
  )
}
