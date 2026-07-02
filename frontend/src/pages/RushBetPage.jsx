import React, { useEffect, useState } from 'react'
import {
  Card, Form, Input, InputNumber, Button, Space, Typography, Divider,
  Row, Col, message, Tag, Collapse, Radio,
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

// 条件赢冲输缩默认档位参数：老客户配置缺这些字段时用它补齐，保证表单有初值
const COND_DEFAULTS = {
  conditional_tiers: [
    { base: 50, rush: 70 },
    { base: 70, rush: 98 },
    { base: 100, rush: 140 },
  ],
  loss_thresholds: [2000, 3000],
  sleep_periods: 3,
}

export default function RushBetPage() {
  const [form] = Form.useForm()
  const mode = Form.useWatch('strategy_mode', form) || 'conditional'
  const [status, setStatus] = useState('stopped')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)

  const refresh = async () => {
    const s = await api.getStatus()
    setStatus(s.rushbet || 'stopped')
  }

  useEffect(() => {
    api.getRushBetConfig().then((cfg) => form.setFieldsValue({ ...COND_DEFAULTS, ...cfg }))
    refresh()
    const t = setInterval(refresh, 3000)
    return () => clearInterval(t)
  }, [])

  const handleSave = async () => {
    try {
      const vals = await form.validateFields()
      setSaving(true)
      await api.saveRushBetConfig(vals)
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
    const res = await api.startRushBet()
    message.info(res.message)
    setLoading(false)
    if (res.ok) setStatus('running')
    refresh()
  }

  const handleStop = async () => {
    setLoading(true)
    const res = await api.stopRushBet()
    message.info(res.message)
    setLoading(false)
    refresh()
  }

  const isRunning = status === 'running'

  return (
    <div style={{ padding: 24 }}>
      <Space style={{ marginBottom: 24, width: '100%', justifyContent: 'space-between' }} align="center">
        <Space>
          <Title level={4} style={{ margin: 0 }}>赢冲输缩 — 3路4球</Title>
          {STATUS_TAG[status]}
        </Space>
        <Space>
          <Button icon={<PlayCircleOutlined />} type="primary" onClick={handleStart} loading={loading} disabled={isRunning}
            style={{ background: '#fa8c16', borderColor: '#fa8c16' }}>
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
              <Collapse defaultActiveKey={['basic', 'accounts', 'strategy']} ghost forceRender>
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
                        <Button type="dashed" onClick={() => {
                          const existing = form.getFieldValue('accounts') || []
                          const ports = existing.map((a) => Number(a?.port)).filter((p) => !Number.isNaN(p))
                          const nextPort = ports.length ? Math.max(...ports) + 1 : 9222
                          add({ account: '', password: '', port: nextPort })
                        }} block icon={<PlusOutlined />}>
                          添加账号
                        </Button>
                      </>
                    )}
                  </Form.List>
                </Panel>

                <Panel header="🔥 注码策略" key="strategy">
                  <Form.Item label="策略模式" name="strategy_mode" initialValue="conditional">
                    <Radio.Group optionType="button" buttonStyle="solid">
                      <Radio.Button value="conditional">条件赢冲输缩（档位）</Radio.Button>
                      <Radio.Button value="simple">固定赢冲输缩（原版）</Radio.Button>
                    </Radio.Group>
                  </Form.Item>

                  {mode === 'conditional' ? (
                    <div style={{
                      background: '#fff7e6', border: '1px solid #ffd591', borderRadius: 8,
                      padding: '12px 14px', marginBottom: 16,
                    }}>
                      <Text strong style={{ color: '#d46b08' }}>条件赢冲输缩 — 档位注码（可手动修改）</Text>
                      <Row gutter={8} style={{ marginTop: 10, marginBottom: 4, fontSize: 12, color: '#8c6d1f' }}>
                        <Col span={3} />
                        <Col span={6}>一阶底注(元)</Col>
                        <Col span={6}>二阶赢冲(元)</Col>
                        <Col span={9}>累计亏损升档(元)</Col>
                      </Row>
                      {[0, 1, 2].map((i) => (
                        <Row gutter={8} key={i} align="middle" style={{ marginBottom: 8 }}>
                          <Col span={3}><Text strong>档{i + 1}</Text></Col>
                          <Col span={6}>
                            <Form.Item name={['conditional_tiers', i, 'base']} rules={[{ required: true, message: '必填' }]} style={{ marginBottom: 0 }}>
                              <InputNumber style={{ width: '100%' }} min={1} />
                            </Form.Item>
                          </Col>
                          <Col span={6}>
                            <Form.Item name={['conditional_tiers', i, 'rush']} rules={[{ required: true, message: '必填' }]} style={{ marginBottom: 0 }}>
                              <InputNumber style={{ width: '100%' }} min={1} />
                            </Form.Item>
                          </Col>
                          <Col span={9}>
                            {i < 2 ? (
                              <Form.Item name={['loss_thresholds', i]} rules={[{ required: true, message: '必填' }]} style={{ marginBottom: 0 }}>
                                <InputNumber style={{ width: '100%' }} min={1} placeholder={`>此值升档${i + 2}`} />
                              </Form.Item>
                            ) : (
                              <Text type="secondary" style={{ fontSize: 12 }}>封顶档（不再升）</Text>
                            )}
                          </Col>
                        </Row>
                      ))}
                      <Row gutter={8} align="middle" style={{ marginTop: 8 }}>
                        <Col span={9}>
                          <Form.Item label="升档前休眠 (期)" name="sleep_periods" style={{ marginBottom: 0 }}
                            tooltip="升档后先跳过这么多期不下注，再用新档位开打">
                            <InputNumber style={{ width: '100%' }} min={0} />
                          </Form.Item>
                        </Col>
                        <Col span={15}>
                          <div style={{ color: '#614700', fontSize: 12, paddingTop: 28, lineHeight: 1.5 }}>
                            回正(累计利润≥0)立即归档1；档内每球独立赢冲输缩（中→二阶 / 不中→一阶）
                          </div>
                        </Col>
                      </Row>
                    </div>
                  ) : (
                    <Row gutter={16}>
                      <Col span={12}>
                        <Form.Item label="一阶底注 (元)" name="base_bet_amount" tooltip="首次或输后的注码">
                          <InputNumber style={{ width: '100%' }} min={1} />
                        </Form.Item>
                      </Col>
                      <Col span={12}>
                        <Form.Item label="二阶赢冲 (元)" name="rush_bet_amount" tooltip="命中一次后升阶使用的注码">
                          <InputNumber style={{ width: '100%' }} min={1} />
                        </Form.Item>
                      </Col>
                    </Row>
                  )}
                  <Row gutter={16}>
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
            <LogViewer taskId="rushbet" running={isRunning} />
          </Card>
        </Col>
      </Row>
    </div>
  )
}
