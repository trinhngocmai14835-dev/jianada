import React, { useEffect, useState } from 'react'
import { Card, Row, Col, Tag, Button, Space, Typography, Statistic, Alert } from 'antd'
import { PlayCircleOutlined, PauseCircleOutlined, ThunderboltOutlined, FireOutlined, TeamOutlined } from '@ant-design/icons'
import { api } from '../api/client'

const { Title, Text } = Typography

const STATUS_TAG = {
  running: <Tag color="green">运行中</Tag>,
  stopping: <Tag color="orange">停止中</Tag>,
  stopped: <Tag color="default">已停止</Tag>,
}

export default function Dashboard({ licenseInfo }) {
  const [status, setStatus] = useState({ autobet: 'stopped', rushbet: 'stopped', followbet: 'stopped' })
  const [loading, setLoading] = useState({})

  const refresh = async () => {
    const s = await api.getStatus()
    setStatus(s)
  }

  useEffect(() => {
    refresh()
    const t = setInterval(refresh, 3000)
    return () => clearInterval(t)
  }, [])

  const ctrl = async (action, key) => {
    setLoading((p) => ({ ...p, [key]: true }))
    try {
      const fn = {
        'autobet-start': api.startAutoBet,
        'autobet-stop': api.stopAutoBet,
        'rushbet-start': api.startRushBet,
        'rushbet-stop': api.stopRushBet,
        'followbet-start': api.startFollowBet,
        'followbet-stop': api.stopFollowBet,
      }[action]
      await fn()
      await refresh()
    } finally {
      setLoading((p) => ({ ...p, [key]: false }))
    }
  }

  const expiry = licenseInfo?.expiry
  const daysLeft = expiry
    ? Math.ceil((new Date(expiry.replace(/(\d{4})(\d{2})(\d{2})/, '$1-$2-$3')) - new Date()) / 86400000)
    : 0

  return (
    <div style={{ padding: 24 }}>
      <Title level={4} style={{ marginBottom: 24 }}>控制台</Title>

      {licenseInfo?.valid && expiry && daysLeft <= 7 && (
        <Alert
          type="warning"
          message={`授权码还有 ${daysLeft} 天到期，请及时续费`}
          style={{ marginBottom: 16 }}
          showIcon
        />
      )}

      <Row gutter={24}>
        {/* 自动下注模块 */}
        <Col xs={24} md={12}>
          <Card
            title={<Space><ThunderboltOutlined style={{ color: '#1677ff' }} /><span>自动下注（三球9粒）</span></Space>}
            extra={STATUS_TAG[status.autobet] || STATUS_TAG.stopped}
            style={{ borderRadius: 12, marginBottom: 24 }}
          >
            <Space direction="vertical" style={{ width: '100%' }} size={16}>
              <Text type="secondary">随机选号，全自动登录下注，支持多账号并发，内置止盈止损</Text>
              <Row gutter={16}>
                <Col span={12}>
                  <Statistic title="状态" value={status.autobet === 'running' ? '运行中' : '已停止'} />
                </Col>
              </Row>
              <Space>
                <Button
                  type="primary"
                  icon={<PlayCircleOutlined />}
                  disabled={status.autobet === 'running'}
                  loading={loading['autobet']}
                  onClick={() => ctrl('autobet-start', 'autobet')}
                >
                  启动
                </Button>
                <Button
                  danger
                  icon={<PauseCircleOutlined />}
                  disabled={status.autobet === 'stopped'}
                  loading={loading['autobet-stop']}
                  onClick={() => ctrl('autobet-stop', 'autobet-stop')}
                >
                  停止
                </Button>
              </Space>
            </Space>
          </Card>
        </Col>

        {/* 赢冲输缩模块 */}
        <Col xs={24} md={12}>
          <Card
            title={<Space><FireOutlined style={{ color: '#fa8c16' }} /><span>赢冲输缩（3路4球）</span></Space>}
            extra={STATUS_TAG[status.rushbet] || STATUS_TAG.stopped}
            style={{ borderRadius: 12, marginBottom: 24 }}
          >
            <Space direction="vertical" style={{ width: '100%' }} size={16}>
              <Text type="secondary">每期随机4球，命中升二阶赢冲，未中降回一阶，纯止盈止损</Text>
              <Row gutter={16}>
                <Col span={12}>
                  <Statistic title="状态" value={status.rushbet === 'running' ? '运行中' : '已停止'} />
                </Col>
              </Row>
              <Space>
                <Button
                  type="primary"
                  icon={<PlayCircleOutlined />}
                  disabled={status.rushbet === 'running'}
                  loading={loading['rushbet']}
                  onClick={() => ctrl('rushbet-start', 'rushbet')}
                  style={{ background: '#fa8c16', borderColor: '#fa8c16' }}
                >
                  启动
                </Button>
                <Button
                  danger
                  icon={<PauseCircleOutlined />}
                  disabled={status.rushbet === 'stopped'}
                  loading={loading['rushbet-stop']}
                  onClick={() => ctrl('rushbet-stop', 'rushbet-stop')}
                >
                  停止
                </Button>
              </Space>
            </Space>
          </Card>
        </Col>

        {/* 跟投模块 */}
        <Col xs={24} md={12}>
          <Card
            title={<Space><TeamOutlined style={{ color: '#52c41a' }} /><span>多账号跟投</span></Space>}
            extra={STATUS_TAG[status.followbet] || STATUS_TAG.stopped}
            style={{ borderRadius: 12, marginBottom: 24 }}
          >
            <Space direction="vertical" style={{ width: '100%' }} size={16}>
              <Text type="secondary">监控主账号报表，自动镜像下注到多个跟投账号，内置自算帐结算</Text>
              <Row gutter={16}>
                <Col span={12}>
                  <Statistic title="状态" value={status.followbet === 'running' ? '运行中' : '已停止'} />
                </Col>
              </Row>
              <Space>
                <Button
                  type="primary"
                  icon={<PlayCircleOutlined />}
                  disabled={status.followbet === 'running'}
                  loading={loading['followbet']}
                  onClick={() => ctrl('followbet-start', 'followbet')}
                  style={{ background: '#52c41a', borderColor: '#52c41a' }}
                >
                  启动
                </Button>
                <Button
                  danger
                  icon={<PauseCircleOutlined />}
                  disabled={status.followbet === 'stopped'}
                  loading={loading['followbet-stop']}
                  onClick={() => ctrl('followbet-stop', 'followbet-stop')}
                >
                  停止
                </Button>
              </Space>
            </Space>
          </Card>
        </Col>
      </Row>

      {/* 授权信息 */}
      <Card title="授权信息" style={{ borderRadius: 12 }}>
        <Row gutter={24}>
          <Col><Statistic title="状态" value={licenseInfo?.valid ? '已激活' : '未激活'} valueStyle={{ color: licenseInfo?.valid ? '#52c41a' : '#ff4d4f' }} /></Col>
          <Col><Statistic title="到期日期" value={expiry ? `${expiry.slice(0,4)}-${expiry.slice(4,6)}-${expiry.slice(6,8)}` : '—'} /></Col>
          <Col><Statistic title="剩余天数" value={daysLeft > 0 ? daysLeft : 0} suffix="天" /></Col>
        </Row>
      </Card>
    </div>
  )
}
