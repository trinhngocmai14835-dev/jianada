import React, { useEffect, useState } from 'react'
import { Alert, Button, Modal, Space, Typography, message } from 'antd'
import { DownloadOutlined, ReloadOutlined } from '@ant-design/icons'
import { api } from '../api/client'

const { Text } = Typography

export default function UpdateNotice() {
  const [info, setInfo] = useState(null)
  const [checking, setChecking] = useState(false)
  const [installing, setInstalling] = useState(false)

  const check = async (manual = false) => {
    setChecking(true)
    try {
      const res = await api.checkUpdate()
      setInfo(res)
      if (manual) {
        if (res?.ok && res?.has_update) {
          message.info('发现新版本')
        } else if (res?.ok) {
          message.success('当前已经是最新版本')
        } else {
          message.warning(res?.message || '检查更新失败')
        }
      }
    } finally {
      setChecking(false)
    }
  }

  useEffect(() => {
    check(false)
  }, [])

  const install = () => {
    Modal.confirm({
      title: '立即更新软件',
      content: '更新前请确认所有投注任务已经停止。更新包下载完成后，软件会自动关闭、替换并重新打开。',
      okText: '立即更新',
      cancelText: '取消',
      onOk: async () => {
        setInstalling(true)
        const res = await api.installUpdate()
        if (res?.ok) {
          message.success(res.message || '正在更新')
        } else {
          message.error(res?.message || '更新失败')
          setInstalling(false)
        }
      },
    })
  }

  if (!info?.ok || !info?.has_update) {
    return null
  }

  return (
    <Alert
      type={info.force ? 'warning' : 'info'}
      showIcon
      style={{ marginBottom: 16 }}
      message={`发现新版本 ${info.latest_version}`}
      description={(
        <Space direction="vertical" size={4}>
          <Text>当前版本：{info.current_version}</Text>
          {info.notes ? <Text>{info.notes}</Text> : null}
        </Space>
      )}
      action={(
        <Space>
          <Button icon={<ReloadOutlined />} loading={checking} onClick={() => check(true)}>
            重新检查
          </Button>
          <Button type="primary" icon={<DownloadOutlined />} loading={installing} onClick={install}>
            立即更新
          </Button>
        </Space>
      )}
    />
  )
}