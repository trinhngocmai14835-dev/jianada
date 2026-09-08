import React, { useEffect, useState } from 'react'
import { BrowserRouter, Routes, Route, useNavigate, useLocation, Navigate } from 'react-router-dom'
import { Layout, Menu, Typography, Space, Tag, Spin } from 'antd'
import {
  DashboardOutlined,
  ThunderboltOutlined,
  FireOutlined,
  AimOutlined,
  TeamOutlined,
  KeyOutlined,
  SafetyOutlined,
  ProfileOutlined,
  RetweetOutlined,
} from '@ant-design/icons'
import { api } from './api/client'
import LicensePage from './pages/LicensePage'
import Dashboard from './pages/Dashboard'
import AutoBetPage from './pages/AutoBetPage'
import RushBetPage from './pages/RushBetPage'
import PickBetPage from './pages/PickBetPage'
import FollowBetPage from './pages/FollowBetPage'
import FlowPage from './pages/FlowPage'
import RotateBetPage from './pages/RotateBetPage'
import CustomRotateBetPage from './pages/CustomRotateBetPage'
import CustomWinBetPage from './pages/CustomWinBetPage'
import MainTrendBetPage from './pages/MainTrendBetPage'

const { Sider, Content, Header } = Layout
const { Text } = Typography

const NAV_ITEMS = [
  { key: '/', icon: <DashboardOutlined />, label: '控制台' },
  { key: '/autobet', icon: <ThunderboltOutlined />, label: '自动下注' },
  { key: '/rushbet', icon: <FireOutlined />, label: '赢冲输缩' },
  { key: '/pickbet', icon: <AimOutlined />, label: '自选号赢冲' },
  { key: '/followbet', icon: <TeamOutlined />, label: '多账号跟投' },
  { key: '/rotatebet', icon: <RetweetOutlined />, label: '轮换追损' },
  { key: '/custom-rotatebet', icon: <RetweetOutlined />, label: '自定义金额轮换追损' },
  { key: '/custom-winbet', icon: <RetweetOutlined />, label: '自定义金额轮换赢冲' },
  { key: '/main-trend-bet', icon: <RetweetOutlined />, label: '主势大小单双追损' },
  { key: '/flow', icon: <ProfileOutlined />, label: '投注流水' },
  { key: '/license', icon: <KeyOutlined />, label: '授权管理' },
]

function AppLayout({ licenseInfo, onReload }) {
  const navigate = useNavigate()
  const location = useLocation()

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider
        width={200}
        style={{ background: '#001529' }}
        breakpoint="lg"
        collapsedWidth="0"
      >
        <div style={{ padding: '20px 16px 12px', borderBottom: '1px solid rgba(255,255,255,0.1)' }}>
          <Text style={{ color: '#fff', fontWeight: 700, fontSize: 16 }}>自动下单 Pro</Text>
          <br />
          <Text style={{ color: 'rgba(255,255,255,0.45)', fontSize: 12 }}>
            {licenseInfo?.valid
              ? `剩余 ${Math.max(0, Math.ceil((new Date(licenseInfo.expiry?.replace(/(\d{4})(\d{2})(\d{2})/, '$1-$2-$3')) - new Date()) / 86400000))} 天`
              : '未激活'}
          </Text>
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[location.pathname]}
          items={NAV_ITEMS}
          onClick={({ key }) => navigate(key)}
          style={{ marginTop: 8 }}
        />
      </Sider>
      <Layout>
        <Content style={{ background: '#f0f2f5', overflowY: 'auto' }}>
          <Routes>
            <Route path="/" element={<Dashboard licenseInfo={licenseInfo} />} />
            <Route path="/autobet" element={<AutoBetPage />} />
            <Route path="/rushbet" element={<RushBetPage />} />
            <Route path="/pickbet" element={<PickBetPage />} />
            <Route path="/followbet" element={<FollowBetPage />} />
            <Route path="/rotatebet" element={<RotateBetPage />} />
            <Route path="/custom-rotatebet" element={<CustomRotateBetPage />} />
            <Route path="/custom-winbet" element={<CustomWinBetPage />} />
            <Route path="/main-trend-bet" element={<MainTrendBetPage />} />
            <Route path="/flow" element={<FlowPage />} />
            <Route path="/license" element={
              <div style={{ padding: 24 }}>
                <LicensePage licenseInfo={licenseInfo} onActivated={onReload} />
              </div>
            } />
            <Route path="*" element={<Navigate to="/" />} />
          </Routes>
        </Content>
      </Layout>
    </Layout>
  )
}

export default function App() {
  const [licenseInfo, setLicenseInfo] = useState(null)
  const [loading, setLoading] = useState(true)

  const loadLicense = async () => {
    const info = await api.getLicense()
    setLicenseInfo(info)
    setLoading(false)
  }

  useEffect(() => { loadLicense() }, [])

  if (loading) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '100vh' }}>
        <Spin size="large" tip="加载中..." />
      </div>
    )
  }

  if (!licenseInfo?.valid) {
    return <LicensePage licenseInfo={licenseInfo} onActivated={loadLicense} />
  }

  return (
    <BrowserRouter>
      <AppLayout licenseInfo={licenseInfo} onReload={loadLicense} />
    </BrowserRouter>
  )
}
