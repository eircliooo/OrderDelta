import { Link, Navigate, Route, Routes } from 'react-router-dom'

import { Disclaimer } from './components/Disclaimer'
import { ProjectsPage } from './pages/ProjectsPage'
import { WorkbenchPage } from './pages/WorkbenchPage'

/**
 * 布局 + 路由。
 *
 * 免责声明放在 header 里（硬约束 #5：常驻可见），不做成可关闭的提示条。
 */
export function App() {
  return (
    <div className="app">
      <header className="app-head">
        <Link className="brand" to="/projects">
          外贸订单差异雷达
        </Link>
        <span className="brand-sub">MVP-0 · 仅支持 .xlsx</span>
        <Disclaimer />
      </header>

      <main>
        <Routes>
          <Route path="/" element={<Navigate to="/projects" replace />} />
          <Route path="/projects" element={<ProjectsPage />} />
          <Route path="/projects/:projectId" element={<WorkbenchPage />} />
          {/* SPEC §13.2 写的是 /projects/:id/differences，保留为同页别名。 */}
          <Route path="/projects/:projectId/differences" element={<WorkbenchPage />} />
          <Route
            path="*"
            element={
              <div className="page">
                <p className="empty-state">页面不存在。</p>
                <Link to="/projects">返回项目列表</Link>
              </div>
            }
          />
        </Routes>
      </main>
    </div>
  )
}
