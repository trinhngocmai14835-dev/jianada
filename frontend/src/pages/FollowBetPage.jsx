import React, { useEffect, useState } from 'react'
import {
  Card, Form, Input, InputNumber, Button, Space, Typography,
  Row, Col, message, Tag, Alert, Collapse,
} from 'antd'
import { PlusOutlined, MinusCircleOutlined, PlayCircleOutlined, PauseCircleOutlined, ChromeOutlined } from '@ant-design/icons'
import { api } from '../api/client'
import LogViewer from '../components/LogViewer'

const { Title } = Typography
const { Panel } = Collapse

const STATUS_TAG = {
  running: <Tag color="green">运行中</Tag>,
  stopping: <Tag color="orange">停止中</Tag>,
  stopped: <Tag color="default">已停止</Tag>,
}

export default function FollowBetPage() {
  const [form] = Form.useForm()
  const [status, setStatus] = useState('stopped')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)

  const refresh = async () => {
    const s = await api.getStatus()
    setStatus(s.followbet || 'stopped')
  }

  useEffect(() => {
    api.getFollowBetConfig().then((cfg) => form.setFieldsValue(cfg))
    refresh()
    const t = setInterval(refresh, 3000)
    return () => clearInterval(t)
  }, [])

  const handleSave = async () => {
    try {
      const vals = await form.validateFields()
      setSaving(true)
      await api.saveFollowBetConfig(vals)
      message.success('配置已保存')
      return vals
    } catch {
      message.error('请检查表单填写')
    } finally {
      setSaving(false)
    }
  }

  const handleStart = async () => {
    await handleSave()
    setLoading(true)
    const res = await api.startFollowBet()
    message.info(res.message)
    setLoading(false)
    if (res.ok) setStatus('running')  // 乐观更新，让 LogViewer 立即开始拉取
    refresh()
  }

  const handleStop = async () => {
    setLoading(true)
    const res = await api.stopFollowBet()
    message.info(res.message)
    setLoading(false)
    refresh()
  }

  const isRunning = status === 'running'

  return (
    <div style={{ padding: 24 }}>
      <Space style={{ marginBottom: 24, width: '100%', justifyContent: 'space-between' }} align="center">
        <Space>
          <Title level={4} style={{ margin: 0 }}>多账号跟投</Title>
          {STATUS_TAG[status]}
        </Space>
        <Space>
          <Button
            icon={<PlayCircleOutlined />}
            onClick={handleStart}
            loading={loading}
            disabled={isRunning}
            style={{ background: '#52c41a', borderColor: '#52c41a', color: '#fff' }}
          >
            保存并启动
          </Button>
          <Button icon={<PauseCircleOutlined />} danger onClick={handleStop} loading={loading} disabled={!isRunning}>
            停止
          </Button>
        </Space>
      </Space>

      <Alert
        type="info"
        message="使用前置条件：主账号（采集端口）需已登录并打开注单明细报表页（可提前打开空的「未结明细」页挂着，无需等客户下注）；跟投账号需已登录并停留在下注页面。客户一下注即自动贴身跟投。"
        style={{ marginBottom: 16 }}
        showIcon
      />

      <Row gutter={24}>
        <Col xs={24} lg={12}>
          <Card title="参数配置" style={{ borderRadius: 12, marginBottom: 24 }}>
            <Form form={form} layout="vertical">
              <Collapse defaultActiveKey={['conn', 'followers', 'strategy']} ghost forceRender>
                <Panel header="🔌 连接设置" key="conn">
                  <Form.Item label="入口网址（启动时自动打开，可留空）" name="entry_url">
                    <Input placeholder="https://166.tt（留空则打开空白页）" />
                  </Form.Item>
                  <Form.Item label="采集端口（主账号）" name="source_port" rules={[{ required: true }]}>
                    <Input placeholder="9222" />
                  </Form.Item>
                  <Form.Item label="刷新间隔 (秒)" name="refresh_sec">
                    <InputNumber style={{ width: '100%' }} min={1} />
                  </Form.Item>
                </Panel>

                <Panel header="👥 跟投账号列表" key="followers">
                  <Form.List name="followers">
                    {(fields, { add, remove }) => (
                      <>
                        {fields.map(({ key, name }) => (
                          <Card size="small" key={key} style={{ marginBottom: 8, background: '#fafafa' }}
                            extra={
                              <Space>
                                <Form.Item noStyle shouldUpdate>
                                  {({ getFieldValue }) => {
                                    const port = getFieldValue(['followers', name, 'port'])
                                    const entryUrl = getFieldValue('entry_url')
                                    return (
                                      <Button
                                        size="small"
                                        icon={<ChromeOutlined />}
                                        onClick={async () => {
                                          const res = await api.openFollowerBrowser(port, entryUrl)
                                          message.info(res.message || (res.ok ? '浏览器已启动' : '启动失败'))
                                        }}
                                      >
                                        打开浏览器
                                      </Button>
                                    )
                                  }}
                                </Form.Item>
                                {fields.length > 1 && (
                                  <MinusCircleOutlined onClick={() => remove(name)} style={{ color: 'red' }} />
                                )}
                              </Space>
                            }>
                            <Row gutter={8}>
                              <Col span={12}>
                                <Form.Item name={[name, 'port']} label="CDP端口" rules={[{ required: true }]}>
                                  <Input placeholder="9223" />
                                </Form.Item>
                              </Col>
                              <Col span={12}>
                                <Form.Item name={[name, 'multiplier']} label="跟投倍数" rules={[{ required: true }]} tooltip="每注金额 = 客户该注金额 × 倍数，支持小数">
                                  <InputNumber style={{ width: '100%' }} placeholder="2" min={0.1} step={0.5} />
                                </Form.Item>
                              </Col>
                            </Row>
                          </Card>
                        ))}
                        <Button type="dashed" onClick={() => add({ port: '9223', multiplier: 1 })} block icon={<PlusOutlined />}>
                          添加跟投账号
                        </Button>
                      </>
                    )}
                  </Form.List>
                </Panel>

                <Panel header="⚙️ 投注参数" key="strategy">
                  <Row gutter={16}>
                    <Col span={12}>
                      <Form.Item label="开始窗口倒计时 (秒)" name="bet_window_start">
                        <InputNumber style={{ width: '100%' }} min={1} />
                      </Form.Item>
                    </Col>
                    <Col span={12}>
                      <Form.Item label="封盘保护倒计时 (秒)" name="bet_window_end">
                        <InputNumber style={{ width: '100%' }} min={1} />
                      </Form.Item>
                    </Col>
                    <Col span={12}>
                      <Form.Item label="赔率" name="odds">
                        <InputNumber style={{ width: '100%' }} step={0.01} />
                      </Form.Item>
                    </Col>
                    <Col span={12}>
                      <Form.Item label="退水比例" name="rebate">
                        <InputNumber style={{ width: '100%' }} step={0.0001} />
                      </Form.Item>
                    </Col>
                    <Col span={12}>
                      <Form.Item label="刷新间隔 (秒)" name="refresh_sec" tooltip="轮询客户注单明细的间隔，越小跟得越贴身（建议 2~3 秒，过小会频繁刷新报表页）">
                        <InputNumber style={{ width: '100%' }} min={1} max={30} />
                      </Form.Item>
                    </Col>
                  </Row>
                </Panel>
              </Collapse>

              <div style={{ marginTop: 16 }}>
                <Button onClick={handleSave} loading={saving} block>
                  仅保存配置
                </Button>
              </div>
            </Form>
          </Card>
        </Col>

        <Col xs={24} lg={12}>
          <Card title="实时日志" style={{ borderRadius: 12 }}>
            <LogViewer taskId="followbet" running={isRunning} />
          </Card>
        </Col>
      </Row>
    </div>
  )
}
