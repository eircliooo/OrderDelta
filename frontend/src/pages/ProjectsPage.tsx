import { useState } from 'react'
import { Link } from 'react-router-dom'

import { useCreateProject, useDeleteProject, useProjects } from '../api/queries'
import { SEVERITIES } from '../api/types'
import type { ProjectOut } from '../api/types'
import {
  DELETE_PROJECT_WARNING,
  formatDateTime,
  projectStatusLabel,
  severityLabel,
  severityMark,
  severitySlug,
} from '../labels/enums'

function CountCells({ project }: { project: ProjectOut }) {
  return (
    <>
      {SEVERITIES.map((severity) => {
        const count = project.severity_counts[severity] ?? 0
        return (
          <td key={severity} className="num">
            <span
              className={`mini mini-${severitySlug(severity)}${count > 0 ? '' : ' mini-zero'}`}
              title={severityLabel(severity)}
            >
              <span aria-hidden="true">{severityMark(severity)}</span>
              <span className="sr-only">{severityLabel(severity)}</span>
              <span className="mini-count">{count}</span>
            </span>
          </td>
        )
      })}
    </>
  )
}

export function ProjectsPage() {
  const projects = useProjects()
  const createProject = useCreateProject()
  const deleteProject = useDeleteProject()

  const [name, setName] = useState('')
  const [confirmingId, setConfirmingId] = useState<string | null>(null)

  const items = projects.data?.items ?? []

  return (
    <div className="page">
      <h2>项目列表</h2>

      <form
        className="create-form"
        onSubmit={(event) => {
          event.preventDefault()
          const trimmed = name.trim()
          if (trimmed === '') return
          createProject.mutate(trimmed, { onSuccess: () => setName('') })
        }}
      >
        <label className="filter">
          <span>项目名称</span>
          <input
            type="text"
            value={name}
            placeholder="例如：2026-07 陶瓷杯订单"
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <button
          type="submit"
          className="btn btn-primary"
          disabled={name.trim() === '' || createProject.isPending}
        >
          创建项目
        </button>
      </form>

      {createProject.error ? (
        <p className="slot-problem" role="alert">
          <strong>创建失败</strong>
          <span className="detail">{createProject.error.message}</span>
        </p>
      ) : null}
      {deleteProject.error ? (
        <p className="slot-problem" role="alert">
          <strong>删除失败</strong>
          <span className="detail">{deleteProject.error.message}</span>
        </p>
      ) : null}

      {projects.isPending ? <p className="muted">加载中…</p> : null}
      {projects.error ? (
        <p className="slot-problem" role="alert">
          <strong>读取项目列表失败</strong>
          <span className="detail">{projects.error.message}</span>
        </p>
      ) : null}

      {!projects.isPending && items.length === 0 ? (
        <p className="empty-state">还没有项目。先创建一个，再上传单据。</p>
      ) : null}

      {items.length > 0 ? (
        <table className="list-table">
          <caption className="sr-only">项目列表</caption>
          <thead>
            <tr>
              <th scope="col">名称</th>
              <th scope="col">状态</th>
              <th scope="col">创建时间</th>
              <th scope="col" className="num">
                严重
              </th>
              <th scope="col" className="num">
                警告
              </th>
              <th scope="col" className="num">
                待复核
              </th>
              <th scope="col" className="num">
                提示
              </th>
              <th scope="col" className="num">
                待处理
              </th>
              <th scope="col">操作</th>
            </tr>
          </thead>
          <tbody>
            {items.map((project) => (
              <tr key={project.id} data-testid={`project-${project.id}`}>
                <td>
                  <Link to={`/projects/${project.id}`}>{project.name}</Link>
                </td>
                <td>{projectStatusLabel(project.status)}</td>
                <td>{formatDateTime(project.created_at)}</td>
                <CountCells project={project} />
                <td className="num">
                  <strong>{project.open_count}</strong>
                </td>
                <td>
                  {confirmingId === project.id ? (
                    <div className="confirm" data-testid={`confirm-${project.id}`}>
                      <p className="confirm-text">
                        确认删除「{project.name}」？{DELETE_PROJECT_WARNING}
                      </p>
                      <button
                        type="button"
                        className="btn btn-danger"
                        disabled={deleteProject.isPending}
                        onClick={() => {
                          deleteProject.mutate(project.id, {
                            onSettled: () => setConfirmingId(null),
                          })
                        }}
                      >
                        确认删除
                      </button>
                      <button
                        type="button"
                        className="btn btn-quiet"
                        onClick={() => setConfirmingId(null)}
                      >
                        取消
                      </button>
                    </div>
                  ) : (
                    <button
                      type="button"
                      className="btn btn-quiet"
                      onClick={() => setConfirmingId(project.id)}
                    >
                      删除
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </div>
  )
}
