import React, { useState } from 'react'
import { Card, Input, Button, Alert, Typography, Space, Tag, Divider } from 'antd'
import { KeyOutlined, CopyOutlined } from '@ant-design/icons'
import { api } from '../api/client'

const { Title, Text, Paragraph } = Typography

export default function LicensePage({ licenseInfo, onActivated }) {
  const [key, setKey] = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)

  const handleActivate = async () => {
    if (!key.trim()) return
    setLoading(true)
    try {
      const res = await api.activate(key.trim())
      setResult(res)
      if (res.valid) {
        setTimeout(() => onActivated?.(), 1200)
      }
    } finally {
      setLoading(false)
    }
  }

  const copyMid = () => {
    navigator.clipboard.writeText(licenseInfo?.machine_id || '')
  }

  return (
    <div style={{
      minHeight: '100vh',
      background: 'linear-gradient(135deg, #0a0a0a 0%, #1a1a2e 50%, #16213e 100%)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      padding: 24,
    }}>
      <Card
        style={{ width: 480, borderRadius: 16, boxShadow: '0 24px 64px rgba(0,0,0,0.4)' }}
        bodyStyle={{ padding: 40 }}
      >
        <Space direction="vertical" size={24} style={{ width: '100%' }}>
          <div style={{ textAlign: 'center' }}>
            <KeyOutlined style={{ fontSize: 48, color: '#1677ff', marginBottom: 12 }} />
            <Title level={3} style={{ margin: 0 }}>自动下单系统 Pro</Title>
            <Text type="secondary">请激活授权码后使用</Text>
          </div>

          <Divider />

          <div>
            <Text strong>本机机器码</Text>
            <div style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              marginTop: 8,
              background: '#f5f5f5',
              borderRadius: 8,
              padding: '8px 12px',
            }}>
              <Text code style={{ flex: 1, fontSize: 15, letterSpacing: 2 }}>
                {licenseInfo?.machine_id || '...'}
              </Text>
              <Button size="small" icon={<CopyOutlined />} onClick={copyMid}>复制</Button>
            </div>
            <Text type="secondary" style={{ fontSize: 12 }}>将机器码发给管理员获取授权码</Text>
          </div>

          <div>
            <Text strong>输入授权码</Text>
            <Input
              style={{ marginTop: 8 }}
              placeholder="XXXXX-XXXXX-XXXXX-XXXXX-..."
              value={key}
              onChange={(e) => setKey(e.target.value)}
              onPressEnter={handleActivate}
              size="large"
              allowClear
            />
          </div>

          <Button
            type="primary"
            block
            size="large"
            loading={loading}
            onClick={handleActivate}
            style={{ borderRadius: 8 }}
          >
            激活授权
          </Button>

          {result && (
            <Alert
              type={result.valid ? 'success' : 'error'}
              message={result.message}
              showIcon
            />
          )}

          {licenseInfo && !licenseInfo.valid && licenseInfo.message !== '未激活' && (
            <Alert type="warning" message={licenseInfo.message} showIcon />
          )}
        </Space>
      </Card>
    </div>
  )
}
