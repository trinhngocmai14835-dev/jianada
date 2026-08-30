import React, { useEffect, useRef, useState } from 'react'
import { Alert, Button, Modal, Progress, Space, Typography, message } from 'antd'
import { DownloadOutlined, ReloadOutlined } from '@ant-design/icons'
import { api } from '../api/client'

const { Text } = Typography

const busyPhases = new Set(['queued', 'checking', 'ready', 'downloading', 'verifying', 'extracting', 'preparing', 'restarting'])

function formatBytes(value) {
  const n = Number(value || 0)
  if (!n) return '0 MB'
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

function clampPercent(value) {
  const n = Number(value || 0)
  return Math.max(0, Math.min(100, Math.round(n)))
}

export default function UpdateNotice() {
  const [info, setInfo] = useState(null)
  const [checking, setChecking] = useState(false)
  const [installing, setInstalling] = useState(false)
  const [installStatus, setInstallStatus] = useState(null)
  const pollRef = useRef(null)
  const statusRef = useRef(null)

  const saveInstallStatus = (status) => {
    statusRef.current = status
    setInstallStatus(status)
  }

  const stopPolling = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }

  const pollInstallStatus = async () => {
    const res = await api.getUpdateStatus()
    if (res?.phase) {
      saveInstallStatus(res)
      if (res.phase === 'failed') {
        stopPolling()
        setInstalling(false)
        message.error(res.message || '更新失败')
      } else if (!res.running && !busyPhases.has(res.phase)) {
        stopPolling()
        setInstalling(false)
      }
      return
    }

    if (statusRef.current?.phase === 'restarting') {
      stopPolling()
      return
    }

    stopPolling()
    setInstalling(false)
    saveInstallStatus({
      ok: false,
      running: false,
      phase: 'failed',
      percent: statusRef.current?.percent || 0,
      message: res?.message || '无法读取更新进度',
    })
  }

  const startPolling = () => {
    stopPolling()
    pollInstallStatus()
    pollRef.current = setInterval(pollInstallStatus, 1000)
  }

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
    api.getUpdateStatus().then((res) => {
      if (res?.running) {
        setInstalling(true)
        saveInstallStatus(res)
        startPolling()
      }
    })
    return stopPolling
  }, [])

  const install = () => {
    Modal.confirm({
      title: '立即更新软件',
      content: '更新前请确认所有投注任务已经停止。更新包下载完成后，软件会自动关闭、替换并重新打开。',
      okText: '立即更新',
      cancelText: '取消',
      onOk: async () => {
        setInstalling(true)
        saveInstallStatus({ ok: true, running: true, phase: 'queued', percent: 0, message: '正在启动更新任务...' })
        const res = await api.installUpdate()
        if (res?.ok) {
          saveInstallStatus(res.status || { ok: true, running: true, phase: 'queued', percent: 0, message: res.message || '已开始下载更新包' })
          message.success(res.message || '已开始下载更新包')
          startPolling()
        } else {
          stopPolling()
          setInstalling(false)
          saveInstallStatus({ ok: false, running: false, phase: 'failed', percent: 0, message: res?.message || '更新失败' })
          message.error(res?.message || '更新失败')
        }
      },
    })
  }

  if (!info?.ok || !info?.has_update) {
    return null
  }

  const showProgress = installing || installStatus?.running || installStatus?.phase === 'failed'
  const percent = clampPercent(installStatus?.percent)
  const totalBytes = Number(installStatus?.total_bytes || 0)
  const downloadedBytes = Number(installStatus?.downloaded_bytes || 0)
  const progressDetail = totalBytes > 0 ? `${formatBytes(downloadedBytes)} / ${formatBytes(totalBytes)}` : ''
  const progressState = installStatus?.phase === 'failed' ? 'exception' : installStatus?.phase === 'restarting' ? 'success' : 'active'

  return (
    <Alert
      type={info.force ? 'warning' : 'info'}
      showIcon
      style={{ marginBottom: 16 }}
      message={`发现新版本：${info.latest_version}`}
      description={(
        <Space direction="vertical" size={6} style={{ width: '100%' }}>
          <Text>当前版本：{info.current_version}</Text>
          {info.notes ? <Text>{info.notes}</Text> : null}
          {showProgress ? (
            <div style={{ minWidth: 280, maxWidth: 520 }}>
              <Progress percent={percent} size="small" status={progressState} />
              <Space direction="vertical" size={2}>
                <Text type={installStatus?.phase === 'failed' ? 'danger' : undefined}>
                  {installStatus?.message || '正在更新...'}
                </Text>
                {progressDetail ? <Text type="secondary" style={{ fontSize: 12 }}>{progressDetail}</Text> : null}
              </Space>
            </div>
          ) : null}
        </Space>
      )}
      action={(
        <Space>
          <Button icon={<ReloadOutlined />} loading={checking} disabled={installing} onClick={() => check(true)}>
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
