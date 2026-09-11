import React, { useEffect, useRef, useState } from 'react'
import { Alert, Button, Modal, Progress, Space, Typography, message } from 'antd'
import { DownloadOutlined, ReloadOutlined } from '@ant-design/icons'
import { api } from '../api/client'

const { Text } = Typography

const busyPhases = new Set(['queued', 'checking', 'ready', 'downloading', 'verifying', 'extracting', 'preparing', 'restarting'])
const restartProbePhases = new Set(['preparing', 'restarting'])

function formatBytes(value) {
  const n = Number(value || 0)
  if (!n) return '0 MB'
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

function clampPercent(value) {
  const n = Number(value || 0)
  return Math.max(0, Math.min(100, Math.round(n)))
}

function sameVersion(a, b) {
  return String(a || '').trim() && String(a || '').trim() === String(b || '').trim()
}

export default function UpdateNotice() {
  const [info, setInfo] = useState(null)
  const [checking, setChecking] = useState(false)
  const [installing, setInstalling] = useState(false)
  const [installStatus, setInstallStatus] = useState(null)
  const pollRef = useRef(null)
  const statusRef = useRef(null)
  const restartRef = useRef({ active: false, timers: [] })

  const saveInstallStatus = (status) => {
    statusRef.current = status
    setInstallStatus(status)
  }

  const addRestartTimer = (fn, delay) => {
    const timer = window.setTimeout(fn, delay)
    restartRef.current.timers.push(timer)
    return timer
  }

  const clearRestartTimers = () => {
    restartRef.current.timers.forEach((timer) => window.clearTimeout(timer))
    restartRef.current = { active: false, timers: [] }
  }

  const stopPolling = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }

  const beginRestartHandoff = (latestVersion) => {
    if (restartRef.current.active) return
    restartRef.current.active = true
    stopPolling()
    message.success('更新包已准备好，正在关闭旧版本并等待新版本启动')

    addRestartTimer(() => {
      try { window.close() } catch (_) {}
    }, 300)

    const startedAt = Date.now()
    const targetVersion = latestVersion || statusRef.current?.latest_version || info?.latest_version || ''

    const probe = async () => {
      try {
        const res = await fetch(`/api/update/check?t=${Date.now()}`, { cache: 'no-store' })
        const data = await res.json()
        if (data?.ok && (sameVersion(data.current_version, targetVersion) || data.has_update === false)) {
          window.location.replace(`/?updated=${Date.now()}`)
          return
        }
      } catch (_) {
        // 旧版本正在退出或新版本尚未启动，继续等待。
      }

      if (Date.now() - startedAt < 90000) {
        addRestartTimer(probe, 1500)
        return
      }

      setInstalling(false)
      saveInstallStatus({
        ...(statusRef.current || {}),
        ok: false,
        running: false,
        phase: 'failed',
        percent: 100,
        message: '已等待新版本启动，请手动关闭当前窗口后重新打开软件。',
      })
      message.warning('已等待新版本启动，请手动关闭当前窗口后重新打开软件')
    }

    addRestartTimer(probe, 5000)
  }

  const pollInstallStatus = async () => {
    try {
      const res = await api.getUpdateStatus()
      if (res?.phase) {
        saveInstallStatus(res)
        if (res.phase === 'restarting') {
          beginRestartHandoff(res.latest_version)
        } else if (res.phase === 'failed') {
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
        beginRestartHandoff(statusRef.current.latest_version)
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
    } catch (_) {
      const last = statusRef.current || {}
      if (restartProbePhases.has(last.phase) || Number(last.percent || 0) >= 96) {
        beginRestartHandoff(last.latest_version)
        return
      }
      stopPolling()
      setInstalling(false)
      saveInstallStatus({
        ok: false,
        running: false,
        phase: 'failed',
        percent: last.percent || 0,
        message: '更新连接中断，请重新打开软件后检查版本。',
      })
    }
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
        if (res.phase === 'restarting') beginRestartHandoff(res.latest_version)
        else startPolling()
      }
    })
    return () => {
      stopPolling()
      clearRestartTimers()
    }
  }, [])

  const install = () => {
    Modal.confirm({
      title: '立即更新软件',
      content: '更新前请确认所有投注任务已经停止。更新包下载完成后，软件会自动关闭、替换并重新打开；当前页面会在新版本启动后自动刷新。',
      okText: '立即更新',
      cancelText: '取消',
      onOk: async () => {
        clearRestartTimers()
        setInstalling(true)
        saveInstallStatus({ ok: true, running: true, phase: 'queued', percent: 0, message: '正在启动更新任务...' })
        const res = await api.installUpdate()
        if (res?.ok) {
          const nextStatus = res.status || { ok: true, running: true, phase: 'queued', percent: 0, message: res.message || '已开始下载更新包' }
          saveInstallStatus(nextStatus)
          message.success(res.message || '已开始下载更新包')
          if (nextStatus.phase === 'restarting') beginRestartHandoff(nextStatus.latest_version)
          else startPolling()
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
  const updatingText = installStatus?.phase === 'restarting' ? '等待新版本启动' : '立即更新'

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
          <Button type="primary" icon={<DownloadOutlined />} loading={installing} disabled={installStatus?.phase === 'restarting'} onClick={install}>
            {updatingText}
          </Button>
        </Space>
      )}
    />
  )
}