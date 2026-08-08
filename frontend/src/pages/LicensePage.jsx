import React, { useEffect, useState } from 'react'
import { Card, Input, Button, Alert, Typography, Space, Tag, Divider, Spin } from 'antd'
import { KeyOutlined, CopyOutlined, ReloadOutlined } from '@ant-design/icons'
import { api } from '../api/client'

const { Title, Text } = Typography

export default function LicensePage({ licenseInfo, onActivated }) {
  const [key, setKey] = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [whitelist, setWhitelist] = useState(null)
  const [whitelistLoading, setWhitelistLoading] = useState(false)

  const loadWhitelist = async () => {
    if (!licenseInfo?.valid) return
    setWhitelistLoading(true)
    try {
      const res = await api.getAccountWhitelist()
      setWhitelist(res)
    } finally {
      setWhitelistLoading(false)
    }
  }

  useEffect(() => {
    loadWhitelist()
  }, [licenseInfo?.valid])

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

  const accounts = whitelist?.accounts || []

  return (
    <div style={{
      minHeight: licenseInfo?.valid ? 'auto' : '100vh',
      background: licenseInfo?.valid ? 'transparent' : 'linear-gradient(135deg, #0a0a0a 0%, #1a1a2e 50%, #16213e 100%)',
      display: 'flex',
      alignItems: licenseInfo?.valid ? 'flex-start' : 'center',
      justifyContent: 'center',
      padding: licenseInfo?.valid ? 0 : 24,
    }}>
      <Card
        style={{ width: 560, borderRadius: 12, boxShadow: licenseInfo?.valid ? 'none' : '0 24px 64px rgba(0,0,0,0.4)' }}
        bodyStyle={{ padding: 32 }}
      >
        <Space direction="vertical" size={22} style={{ width: '100%' }}>
          <div style={{ textAlign: 'center' }}>
            <KeyOutlined style={{ fontSize: 44, color: '#1677ff', marginBottom: 12 }} />
            <Title level={3} style={{ margin: 0 }}>自动下单系统 Pro</Title>
            <Text type="secondary">授权码与账号白名单</Text>
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
            <Text type="secondary" style={{ fontSize: 12 }}>把机器码发给管理员，用于开通授权码和账号白名单。</Text>
          </div>

          {!licenseInfo?.valid && (
            <>
              <div>
                <Text strong>授权码</Text>
                <Input
                  style={{ marginTop: 8 }}
                  placeholder="请输入授权码"
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
            </>
          )}

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

          {licenseInfo?.valid && (
            <div>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
                <Text strong>已授权平台账号</Text>
                <Button size="small" icon={<ReloadOutlined />} loading={whitelistLoading} onClick={loadWhitelist}>刷新</Button>
              </div>

              {whitelistLoading && !whitelist ? (
                <Spin size="small" />
              ) : whitelist?.ok ? (
                <div style={{ background: '#fafafa', border: '1px solid #f0f0f0', borderRadius: 8, padding: 12 }}>
                  <div style={{ marginBottom: 8 }}>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      来源：{whitelist.source === 'remote' ? '远程白名单' : '本机缓存'}
                      {whitelist.customer_id ? ` · 客户ID：${whitelist.customer_id}` : ''}
                    </Text>
                  </div>
                  {accounts.length ? (
                    <Space wrap>
                      {accounts.map((account) => <Tag color="blue" key={account}>{account}</Tag>)}
                    </Space>
                  ) : (
                    <Alert type="warning" showIcon message="本机暂无授权账号，请联系管理员添加账号白名单。" />
                  )}
                </div>
              ) : (
                <Alert type="warning" showIcon message={whitelist?.message || '账号白名单获取失败'} />
              )}
            </div>
          )}
        </Space>
      </Card>
    </div>
  )
}
