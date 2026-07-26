/**
 * 唯一的 HTTP 出口。
 *
 * 硬约束 #2：**API base 是相对路径 `/api`**，由 Vite dev proxy 转发到
 * 127.0.0.1:8000（见 vite.config.ts）。这里不出现任何 origin，生产同源部署时
 * 一行不用改。eslint 的 no-restricted-syntax 规则机械地守着这条。
 */

import type {
  DifferenceOut,
  DocumentOut,
  Envelope,
  ErrorBody,
  ProjectOut,
  ReviewIn,
} from './types'

export const API_BASE = '/api'
export const API_V1 = `${API_BASE}/v1`

/** 报告导出入口。返回给 <a href>，浏览器直接下载后端生成的自包含 HTML。 */
export function reportUrl(projectId: string): string {
  return `${API_V1}/projects/${encodeURIComponent(projectId)}/report.html`
}

export class ApiError extends Error {
  readonly errorCode: string
  readonly detail: string | null
  readonly status: number

  constructor(status: number, body: ErrorBody) {
    super(body.message)
    this.name = 'ApiError'
    this.status = status
    this.errorCode = body.error_code
    this.detail = body.detail ?? null
  }
}

async function toApiError(response: Response): Promise<ApiError> {
  try {
    const body = (await response.json()) as Partial<ErrorBody>
    return new ApiError(response.status, {
      error_code: body.error_code ?? 'UNKNOWN',
      message: body.message ?? `请求失败（HTTP ${response.status}）`,
      detail: body.detail ?? null,
    })
  } catch {
    return new ApiError(response.status, {
      error_code: 'UNKNOWN',
      message: `请求失败（HTTP ${response.status}）`,
    })
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_V1}${path}`, init)
  if (!response.ok) {
    throw await toApiError(response)
  }
  if (response.status === 204) {
    return undefined as T
  }
  return (await response.json()) as T
}

function jsonInit(method: string, body: unknown): RequestInit {
  return {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }
}

// ------------------------------------------------------------------ 项目

export function listProjects(): Promise<Envelope<ProjectOut>> {
  return request<Envelope<ProjectOut>>('/projects')
}

export function getProject(projectId: string): Promise<ProjectOut> {
  return request<ProjectOut>(`/projects/${encodeURIComponent(projectId)}`)
}

export function createProject(name: string): Promise<ProjectOut> {
  return request<ProjectOut>('/projects', jsonInit('POST', { name }))
}

export function deleteProject(projectId: string): Promise<void> {
  return request<void>(`/projects/${encodeURIComponent(projectId)}`, { method: 'DELETE' })
}

// ------------------------------------------------------------------ 文档

export function uploadDocument(
  projectId: string,
  role: string,
  file: File,
): Promise<DocumentOut> {
  const form = new FormData()
  form.append('role', role)
  form.append('file', file)
  return request<DocumentOut>(`/projects/${encodeURIComponent(projectId)}/documents`, {
    method: 'POST',
    body: form,
  })
}

// ------------------------------------------------------------------ 比较

export function runCompare(projectId: string): Promise<ProjectOut> {
  return request<ProjectOut>(`/projects/${encodeURIComponent(projectId)}/compare`, {
    method: 'POST',
  })
}

/**
 * 差异全量拉取，筛选放在前端做。
 *
 * 后端同样支持 severity/type/sku/review_status/role 查询参数，但芯片筛选要
 * 「点一下立刻变」，且总览计数必须始终是**全量**的——服务端筛选会让计数随筛选漂移，
 * 那正是业务员最容易误判「没差异了」的地方。
 */
export function listDifferences(projectId: string): Promise<Envelope<DifferenceOut>> {
  return request<Envelope<DifferenceOut>>(
    `/projects/${encodeURIComponent(projectId)}/differences`,
  )
}

export function setReview(
  projectId: string,
  differenceKey: string,
  body: ReviewIn,
): Promise<DifferenceOut> {
  return request<DifferenceOut>(
    `/projects/${encodeURIComponent(projectId)}/reviews/${encodeURIComponent(differenceKey)}`,
    jsonInit('PUT', body),
  )
}
