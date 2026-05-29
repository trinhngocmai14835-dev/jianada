import React, { useEffect, useState } from 'react'
import {
  Card, Form, Input, InputNumber, Button, Space, Typography, Divider,
  Switch, Row, Col, message, Spin, Tag, Collapse,
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

export default function AutoBetPage() {
  const [form] = Form.useForm()
  const [status, setStatus] = useState('stopped')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)

  const refresh = async () => {
    const s = await api.getStatus()
    setStatus(s.autobet || 'stopped')
  }

  useEffect(() => {
    api.getAutoBetConfig().then((cfg) => form.setFieldsValue(cfg))
    refresh()
    const t = setInterval(refresh, 3000)
    return () => clearInterval(t)
  }, [])

  const handleSave = async () => {
    try {
      const vals = await form.validateFields()
      setSaving(true)
      await api.saveAutoBetConfig(vals)
      message.success('配置已保存')
    } catch {
      message.error('请检查表单填写')
    } finally {
      setSaving(false)
    }
  }

  const handleStart = async () => {
    await handleSave()
    setLoading(true)
    const res = await api.startAutoBet()
    message.info(res.message)
    setLoading(false)
    if (res.ok) setStatus('running')  // 乐观更新，让 LogViewer 立即开始拉取
    refresh()
  }

  const handleStop = async () => {
    setLoading(true)
    const res = await api.stopAutoBet()
    message.info(res.message)
    setLoading(false)
    refresh()
  }

  const isRunning = status === 'running'

  return (
    <div style={{ padding: 24 }}>
      <Space style={{ marginBottom: 24, width: '100%', justifyContent: 'space-between' }} align="center">
        <Space>
          <Title level={4} style={{ margin: 0 }}>自动下注 — 三球9粒</Title>
          {STATUS_TAG[status]}
        </Space>
        <Space>
          <Button icon={<PlayCircleOutlined />} type="primary" onClick={handleStart} loading={loading} disabled={isRunning}>
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
              <Collapse defaultActiveKey={['basic', 'accounts']} ghost forceRender>
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
                        <Button type="dashed" onClick={() => add({ account: '', password: '', port: 9222 })} block icon={<PlusOutlined />}>
                          添加账号
                        </Button>
                      </>
                    )}
                  </Form.List>
                </Panel>

                <Panel header="🎯 下注策略" key="strategy">
                  <Row gutter={16}>
                    <Col span={12}>
                      <Form.Item label="单注金额 (元)" name="base_bet_amount">
                        <InputNumber style={{ width: '100%' }} min={1} />
                      </Form.Item>
                    </Col>
                    <Col span={12}>
                      <Form.Item label="每球号码数" name="numbers_per_pos">
                        <InputNumber style={{ width: '100%' }} min={1} max={9} />
                      </Form.Item>
                    </Col>
                    <Col span={12}>
                      <Form.Item label="开始时间 (时)" name="run_start_hour">
                        <InputNumber style={{ width: '100%' }} min={0} max={23} />
                      </Form.Item>
                    </Col>
                    <Col span={12}>
                      <Form.Item label="结束时间 (时)" name="run_end_hour">
                        <InputNumber style={{ width: '100%' }} min={0} max={23} />
                      </Form.Item>
                    </Col>
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

                <Panel header="📱 Telegram 通知 (可选)" key="tg">
                  <Form.Item label="Bot Token" name="tg_token">
                    <Input placeholder="留空则不发通知" />
                  </Form.Item>
                  <Form.Item label="Chat ID" name="tg_chat_id">
                    <Input placeholder="留空则不发通知" />
                  </Form.Item>
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
            <LogViewer taskId="autobet" running={isRunning} />
          </Card>
        </Col>
      </Row>
    </div>
  )
}
