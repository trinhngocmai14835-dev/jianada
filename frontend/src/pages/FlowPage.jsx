import React, { useEffect, useState } from 'react'
import {
  Card, Table, Select, Button, Space, Typography, Tag, Popconfirm, message, Tooltip,
} from 'antd'
import { ReloadOutlined, DeleteOutlined } from '@ant-design/icons'
import { api } from '../api/client'

const { Title, Text } = Typography

const MODE_LABEL = {
  autobet: '自动下注',
  rushbet: '赢冲输缩',
  pickbet: '自选号赢冲',
  followbet: '多账号跟投',
  rotatebet: '轮换追损',
  custom_rotatebet: '自定义金额轮换追损',
  custom_winbet: '自定义金额轮换赢冲',
  main_trend_bet: '主势大小单双追损',
}
const MODE_COLOR = {
  autobet: 'blue', rushbet: 'volcano', pickbet: 'geekblue', followbet: 'green', rotatebet: 'purple', custom_rotatebet: 'magenta', custom_winbet: 'gold', main_trend_bet: 'cyan',
}

export default function FlowPage() {
  const [accounts, setAccounts] = useState([])
  const [account, setAccount] = useState('')
  const [records, setRecords] = useState([])
  const [loading, setLoading] = useState(false)

  const loadAccounts = async () => {
    const r = await api.getFlowAccounts()
    setAccounts(r.accounts || [])
  }

  const loadFlow = async (acc = account) => {
    setLoading(true)
    try {
      const r = await api.getFlow(acc, '', 800)
      setRecords(r.records || [])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadAccounts()
    loadFlow('')
    const t = setInterval(() => { loadAccounts(); loadFlow() }, 8000)
    return () => clearInterval(t)
  }, [account])

  const onClear = async (mode) => {
    if (mode === 'account' && !account) {
      message.warning('请先在上方选择一个账号')
      return
    }
    await api.clearFlow(mode === 'account' ? account : '', mode === 'old' ? 7 : 0)
    message.success('已清理')
    loadAccounts(); loadFlow()
  }

  const columns = [
    { title: '时间', dataIndex: 'ts', width: 165, render: (t) => <Text style={{ fontSize: 12 }}>{t}</Text> },
    {
      title: '模式', dataIndex: 'mode', width: 100,
      render: (m) => <Tag color={MODE_COLOR[m] || 'default'}>{MODE_LABEL[m] || m}</Tag>,
    },
    { title: '账号', dataIndex: 'account', width: 110, render: (a) => <Text strong>{a || '-'}</Text> },
    { title: '流水内容', dataIndex: 'msg', render: (m) => <Text style={{ fontSize: 13 }}>{m}</Text> },
  ]

  return (
    <div style={{ padding: 24 }}>
      <Title level={4}>投注流水</Title>
      <Text type="secondary">只保存有用信息（登录 / 下注 / 结算），啰嗦运行日志不入库。关软件也不丢，可按账号查看、随时清理。</Text>

      <Card style={{ borderRadius: 12, marginTop: 16 }}>
        <Space wrap style={{ marginBottom: 16 }}>
          <Text>账号筛选：</Text>
          <Select
            style={{ width: 220 }}
            value={account}
            onChange={setAccount}
            options={[
              { value: '', label: '全部账号' },
              ...accounts.map((a) => ({ value: a.account, label: `${a.account}（${a.count} 条）` })),
            ]}
          />
          <Button icon={<ReloadOutlined />} onClick={() => { loadAccounts(); loadFlow() }}>刷新</Button>
          <Popconfirm title="删除7天前的流水？" onConfirm={() => onClear('old')}>
            <Button>清理7天前</Button>
          </Popconfirm>
          <Popconfirm title={account ? `清空账号「${account}」的全部流水？` : '请先选择账号'} onConfirm={() => onClear('account')}>
            <Button danger icon={<DeleteOutlined />}>清空该账号</Button>
          </Popconfirm>
          <Popconfirm title="清空全部账号的所有流水？此操作不可恢复" onConfirm={() => onClear('all')}>
            <Button danger>清空全部</Button>
          </Popconfirm>
        </Space>

        <Table
          size="small"
          rowKey="id"
          loading={loading}
          columns={columns}
          dataSource={records}
          pagination={{ pageSize: 50, showSizeChanger: false }}
          scroll={{ x: 700 }}
        />
      </Card>
    </div>
  )
}
