import React, { useMemo, useState } from 'react'
import { Alert, Button, Card, Col, Empty, Input, InputNumber, Popconfirm, Row, Space, Statistic, Table, Tag, Typography, message } from 'antd'
import { BarChartOutlined, ChromeOutlined, DatabaseOutlined, DeleteOutlined, ExperimentOutlined, SearchOutlined } from '@ant-design/icons'
import { api } from '../api/client'

const { Title, Text, Paragraph } = Typography
const { TextArea } = Input

const DEFAULT_TEXT = `3500001 1 2 3
3500002 4 4 5
3500003 4 5 5
3500004 8 1 7`

function pct(value) {
  return `${((Number(value) || 0) * 100).toFixed(1)}%`
}

function profit(value) {
  const n = Number(value) || 0
  return n > 0 ? `+${n.toFixed(2)}` : n.toFixed(2)
}

function confidenceColor(value) {
  if (value === '高') return 'green'
  if (value === '中') return 'blue'
  return 'default'
}

function actionTag(action, target) {
  if (action === '跳过') return <Tag>跳过</Tag>
  return <Tag color="blue">{target || action}</Tag>
}

function recordsToText(records) {
  const ordered = [...(records || [])].sort((a, b) => {
    const ai = Number.parseInt(a?.issue, 10)
    const bi = Number.parseInt(b?.issue, 10)
    if (Number.isFinite(ai) && Number.isFinite(bi)) return ai - bi
    return String(a?.issue || '').localeCompare(String(b?.issue || ''))
  })
  return ordered
    .map((record) => {
      const nums = record?.numbers || []
      if (nums.length < 3) return ''
      return `${record.issue || ''} ${nums[0]} ${nums[1]} ${nums[2]}`.trim()
    })
    .filter(Boolean)
    .join('\n')
}

function BacktestView({ value }) {
  if (!value) return <Text type="secondary">-</Text>
  return (
    <Space size={[4, 4]} wrap>
      <Tag color={value.profit_units > 0 ? 'green' : value.profit_units < 0 ? 'red' : 'default'}>
        {profit(value.profit_units)} 单位
      </Tag>
      <Tag>命中 {pct(value.hit_rate)}</Tag>
      <Tag>下注 {value.bets}</Tag>
      <Tag>回撤 {Number(value.max_drawdown_units || 0).toFixed(2)}</Tag>
    </Space>
  )
}

function digitTags(values) {
  return (
    <Space size={4} wrap>
      {(values || []).map((value) => <Tag color="geekblue" key={value}>{value}</Tag>)}
    </Space>
  )
}

export default function DrawAnalysisPage() {
  const [text, setText] = useState(DEFAULT_TEXT)
  const [lookback, setLookback] = useState(80)
  const [backtestWindow, setBacktestWindow] = useState(300)
  const [groupSize, setGroupSize] = useState(5)
  const [capturePort, setCapturePort] = useState(9333)
  const [captureLimit, setCaptureLimit] = useState(500)
  const [loading, setLoading] = useState(false)
  const [captureLoading, setCaptureLoading] = useState(false)
  const [result, setResult] = useState(null)

  const runAnalyze = async (nextText = text) => {
    setLoading(true)
    try {
      const data = await api.analyzeDraws({
        text: nextText,
        lookback,
        backtest_window: backtestWindow,
        group_size: groupSize,
      })
      setResult(data)
      if (data?.ok === false) message.warning(data.message || '开奖记录不足')
      return data
    } finally {
      setLoading(false)
    }
  }

  const captureRealRecords = async () => {
    setCaptureLoading(true)
    try {
      const data = await api.captureDrawRecords({
        port: capturePort,
        limit: captureLimit,
        save: true,
        lookback,
        backtest_window: backtestWindow,
        group_size: groupSize,
      })
      if (data?.ok === false) {
        setResult(data)
        message.warning(data.message || '抓取失败')
        return data
      }
      const nextText = recordsToText(data.records || [])
      setText(nextText)
      setResult(data.analysis || null)
      message.success(`${data.message || '抓取完成'}，保存 ${data.saved || 0} 条`)
      return data
    } finally {
      setCaptureLoading(false)
    }
  }

  const loadSavedRecords = async () => {
    setLoading(true)
    try {
      const data = await api.getSavedDrawRecords(captureLimit)
      const records = data.records || []
      if (!records.length) {
        message.warning('本地还没有保存开奖记录')
        return
      }
      const nextText = data.text || recordsToText(records)
      setText(nextText)
      await runAnalyze(nextText)
    } finally {
      setLoading(false)
    }
  }

  const clearSavedRecords = async () => {
    setLoading(true)
    try {
      const data = await api.clearSavedDrawRecords()
      if (data?.ok === false) {
        message.error(data.message || '清空失败')
      } else {
        message.success('已清空本地开奖记录')
      }
    } finally {
      setLoading(false)
    }
  }

  const loadSample = async () => {
    setLoading(true)
    try {
      const data = await api.getDrawAnalysisSample(180)
      const sample = data.text || ''
      setText(sample)
      await runAnalyze(sample)
    } finally {
      setLoading(false)
    }
  }

  const mainRows = result?.main_trend || []
  const numberRows = result?.number_sets || []
  const latest = result?.summary?.latest
  const stats = result?.stats

  const mainColumns = useMemo(() => [
    { title: '盘路', dataIndex: 'name', width: 90 },
    { title: '建议', width: 100, render: (_, row) => actionTag(row.action, row.target) },
    { title: '信心', dataIndex: 'confidence', width: 80, render: (v) => <Tag color={confidenceColor(v)}>{v}</Tag> },
    { title: '得分差', dataIndex: 'gap', width: 90, render: (v) => Number(v || 0).toFixed(2) },
    { title: '回测', dataIndex: 'backtest', render: (v) => <BacktestView value={v} /> },
    { title: '依据', dataIndex: 'reasons', render: (items) => <Text type="secondary">{(items || []).join('；')}</Text> },
  ], [])

  const numberColumns = useMemo(() => [
    { title: '球路', dataIndex: 'name', width: 90 },
    { title: '候选号码', dataIndex: 'group', width: 180, render: digitTags },
    { title: '建议', width: 110, render: (_, row) => actionTag(row.action, row.action === '跳过' ? '' : '候选组') },
    { title: '信心', dataIndex: 'confidence', width: 80, render: (v) => <Tag color={confidenceColor(v)}>{v}</Tag> },
    { title: '覆盖', dataIndex: 'coverage', width: 90, render: pct },
    { title: '强度', dataIndex: 'strength', width: 90, render: (v) => Number(v || 0).toFixed(2) },
    { title: '回测', dataIndex: 'backtest', render: (v) => <BacktestView value={v} /> },
    { title: '依据', dataIndex: 'reasons', render: (items) => <Text type="secondary">{(items || []).join('；')}</Text> },
  ], [])

  const recordColumns = useMemo(() => [
    { title: '期号', dataIndex: 'issue', width: 110 },
    { title: '开奖号', dataIndex: 'numbers', width: 110, render: (nums) => (nums || []).join(' ') },
    { title: '和值', dataIndex: 'total', width: 70 },
    { title: '大小', dataIndex: 'dx', width: 70, render: (v) => <Tag color={v === '大' ? 'red' : 'blue'}>{v}</Tag> },
    { title: '单双', dataIndex: 'ds', width: 70, render: (v) => <Tag color={v === '单' ? 'purple' : 'cyan'}>{v}</Tag> },
    { title: '特殊', dataIndex: 'special', width: 80, render: (v) => v ? <Tag color="gold">13/14</Tag> : <Tag>否</Tag> },
  ], [])

  return (
    <div style={{ padding: 24 }}>
      <Space style={{ marginBottom: 24, width: '100%', justifyContent: 'space-between' }} align="center" wrap>
        <Title level={4} style={{ margin: 0 }}>开奖记录分析推荐</Title>
        <Space wrap>
          <Button type="primary" icon={<ChromeOutlined />} onClick={captureRealRecords} loading={captureLoading}>从已登录浏览器抓取</Button>
          <Button icon={<DatabaseOutlined />} onClick={loadSavedRecords} loading={loading}>读取已保存记录</Button>
          <Button icon={<ExperimentOutlined />} onClick={loadSample} loading={loading}>载入测试样本</Button>
          <Button icon={<SearchOutlined />} onClick={() => runAnalyze()} loading={loading}>分析推荐</Button>
        </Space>
      </Space>

      <Row gutter={[16, 16]}>
        <Col xs={24} xl={9}>
          <Card title="真实数据采集" style={{ borderRadius: 8, marginBottom: 16 }}>
            <Space wrap style={{ marginBottom: 12 }}>
              <Space direction="vertical" size={4}>
                <Text type="secondary">浏览器端口</Text>
                <InputNumber min={1} max={65535} value={capturePort} onChange={(v) => setCapturePort(v || 9333)} />
              </Space>
              <Space direction="vertical" size={4}>
                <Text type="secondary">抓取上限</Text>
                <InputNumber min={20} max={2000} value={captureLimit} onChange={(v) => setCaptureLimit(v || 500)} />
              </Space>
            </Space>
            <Space wrap>
              <Button type="primary" icon={<ChromeOutlined />} onClick={captureRealRecords} loading={captureLoading}>抓取并分析</Button>
              <Button icon={<DatabaseOutlined />} onClick={loadSavedRecords} loading={loading}>读取保存</Button>
              <Popconfirm title="清空本地保存的开奖记录？" okText="清空" cancelText="取消" onConfirm={clearSavedRecords}>
                <Button danger icon={<DeleteOutlined />} loading={loading}>清空</Button>
              </Popconfirm>
            </Space>
            <Paragraph type="secondary" style={{ margin: '10px 0 0', fontSize: 12 }}>
              先用调试端口浏览器登录并打开“开奖结果”，再抓取当前列表。
            </Paragraph>
          </Card>

          <Card title="分析参数" style={{ borderRadius: 8, marginBottom: 16 }}>
            <Space wrap>
              <Space direction="vertical" size={4}>
                <Text type="secondary">分析窗口</Text>
                <InputNumber min={10} max={500} value={lookback} onChange={(v) => setLookback(v || 80)} />
              </Space>
              <Space direction="vertical" size={4}>
                <Text type="secondary">回测期数</Text>
                <InputNumber min={20} max={2000} value={backtestWindow} onChange={(v) => setBacktestWindow(v || 300)} />
              </Space>
              <Space direction="vertical" size={4}>
                <Text type="secondary">号码组数量</Text>
                <InputNumber min={1} max={9} value={groupSize} onChange={(v) => setGroupSize(v || 5)} />
              </Space>
            </Space>
          </Card>

          <Card title="开奖记录" style={{ borderRadius: 8 }}>
            <TextArea
              value={text}
              onChange={(e) => setText(e.target.value)}
              autoSize={{ minRows: 16, maxRows: 28 }}
              placeholder="3500001 1 2 3"
            />
            <Paragraph type="secondary" style={{ margin: '10px 0 0', fontSize: 12 }}>
              每行一条：期号 第一球 第二球 第三球。
            </Paragraph>
          </Card>
        </Col>

        <Col xs={24} xl={15}>
          {result?.ok === false && (
            <Alert type="warning" showIcon message={result.message} style={{ marginBottom: 16 }} />
          )}

          {result?.ok ? (
            <>
              <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
                <Col xs={12} md={6}>
                  <Card style={{ borderRadius: 8 }}>
                    <Statistic title="有效记录" value={result.summary.records} />
                  </Card>
                </Col>
                <Col xs={12} md={6}>
                  <Card style={{ borderRadius: 8 }}>
                    <Statistic title="分析窗口" value={result.summary.used_window} />
                  </Card>
                </Col>
                <Col xs={12} md={6}>
                  <Card style={{ borderRadius: 8 }}>
                    <Statistic title="最新期号" value={latest?.issue || '-'} />
                  </Card>
                </Col>
                <Col xs={12} md={6}>
                  <Card style={{ borderRadius: 8 }}>
                    <Statistic title="最新和值" value={latest?.total ?? '-'} suffix={latest ? `${latest.dx}/${latest.ds}` : ''} />
                  </Card>
                </Col>
              </Row>

              <Alert
                type="info"
                showIcon
                style={{ marginBottom: 16 }}
                message="推荐只在回测利润为正且当前得分达标时给出；不达标显示跳过。"
              />

              <Card title="主势盘大小单双" style={{ borderRadius: 8, marginBottom: 16 }}>
                <Table size="small" rowKey="kind" columns={mainColumns} dataSource={mainRows} pagination={false} scroll={{ x: 900 }} />
              </Card>

              <Card title="1-3 球号码组" style={{ borderRadius: 8, marginBottom: 16 }}>
                <Table size="small" rowKey="position" columns={numberColumns} dataSource={numberRows} pagination={false} scroll={{ x: 1100 }} />
              </Card>

              <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
                {(stats?.positions || []).map((item) => (
                  <Col xs={24} md={8} key={item.position}>
                    <Card title={item.name} style={{ borderRadius: 8 }}>
                      <Space direction="vertical" size={8}>
                        <Space>热号：{digitTags((item.hot || []).map((v) => v.value))}</Space>
                        <Space>冷号：{digitTags((item.cold || []).map((v) => v.value))}</Space>
                        <Text type="secondary">最大遗漏：{Math.max(...(item.omissions || []).map((v) => v.miss))} 期</Text>
                      </Space>
                    </Card>
                  </Col>
                ))}
              </Row>

              <Card title="最近开奖记录" style={{ borderRadius: 8 }}>
                <Table size="small" rowKey={(row, index) => `${row.issue}-${index}`} columns={recordColumns} dataSource={result.records || []} pagination={{ pageSize: 10 }} scroll={{ x: 640 }} />
              </Card>
            </>
          ) : (
            <Card style={{ borderRadius: 8 }}>
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="等待分析" />
            </Card>
          )}
        </Col>
      </Row>
    </div>
  )
}