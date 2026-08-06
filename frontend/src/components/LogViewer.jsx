import React, { useEffect, useRef, useState } from 'react'
import { Button } from 'antd'
import { ClearOutlined } from '@ant-design/icons'

const LEVEL_COLORS = { error: '#ff4d4f', warn: '#faad14', info: '#52c41a' }

export default function LogViewer({ taskId, running }) {
  const [logs, setLogs] = useState([])
  const boxRef = useRef(null)
  const bottomRef = useRef(null)
  const lastSeqRef = useRef(0)
  const timerRef = useRef(null)
  const autoFollowRef = useRef(true)

  useEffect(() => {
    setLogs([])
    lastSeqRef.current = 0
    autoFollowRef.current = true
  }, [taskId])

  useEffect(() => {
    if (timerRef.current) clearInterval(timerRef.current)

    const poll = async () => {
      try {
        const res = await fetch(`/api/logs/${taskId}?since=${lastSeqRef.current}`)
        const data = await res.json()
        if (!data.messages || data.messages.length === 0) return
        lastSeqRef.current = data.seq
        setLogs((prev) => [...prev, ...data.messages].slice(-500))
      } catch (_) {}
    }

    if (!running) {
      timerRef.current = setTimeout(poll, 1000)
      return () => clearTimeout(timerRef.current)
    }

    poll()
    timerRef.current = setInterval(poll, 2000)
    return () => clearInterval(timerRef.current)
  }, [taskId, running])

  useEffect(() => {
    if (autoFollowRef.current) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
    }
  }, [logs])

  const handleScroll = () => {
    const el = boxRef.current
    if (!el) return
    const distanceToBottom = el.scrollHeight - el.scrollTop - el.clientHeight
    autoFollowRef.current = distanceToBottom < 40
  }

  const handleClear = () => {
    setLogs([])
    autoFollowRef.current = true
  }

  return (
    <div style={{ position: 'relative' }}>
      <Button
        size="small"
        icon={<ClearOutlined />}
        style={{ position: 'absolute', top: 8, right: 8, zIndex: 1 }}
        onClick={handleClear}
      >
        清空
      </Button>
      <div
        ref={boxRef}
        onScroll={handleScroll}
        style={{
          background: '#0d1117',
          color: '#e6edf3',
          fontFamily: 'Consolas, monospace',
          fontSize: 13,
          padding: '12px 16px',
          borderRadius: 8,
          height: 380,
          overflowY: 'auto',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-all',
        }}
      >
        {logs.length === 0 ? (
          <span style={{ color: '#666' }}>{running ? '等待日志...' : '点击启动后日志将显示在这里'}</span>
        ) : (
          logs.map((l, i) => (
            <div key={i} style={{ marginBottom: 2, color: LEVEL_COLORS[l.level] || '#e6edf3' }}>
              <span style={{ color: '#58a6ff', marginRight: 8 }}>[{l.time}]</span>
              {l.msg}
            </div>
          ))
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}