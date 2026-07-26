import { useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { reportUrl } from '../api/client'
import { useDifferences, useProject, useRunCompare, useSetReview } from '../api/queries'
import { DOCUMENT_ROLES, isUsable } from '../api/types'
import type { DifferenceOut } from '../api/types'
import { CoverageBanner } from '../components/CoverageBanner'
import { DifferenceFilters, EMPTY_FILTERS } from '../components/DifferenceFilters'
import type { FilterState } from '../components/DifferenceFilters'
import { DifferenceTable } from '../components/DifferenceTable'
import { SeverityChips } from '../components/SeverityChips'
import type { SeverityFilter } from '../components/SeverityChips'
import { UploadSlot } from '../components/UploadSlot'
import { formatDateTime, projectStatusLabel, roleLabel, severityRank } from '../labels/enums'
import { subjectLabel } from '../labels/identifiers'

/** 与后端 open_count 的口径一致：还需要人处理的两种状态。 */
const PENDING_STATUSES: readonly string[] = ['OPEN', 'NEEDS_CONFIRMATION']

/** SPEC §1.3：|R| >= 2 即可运行检查。**这里不是 3**。 */
const MIN_DOCUMENTS_TO_COMPARE = 2

function matches(
  diff: DifferenceOut,
  severity: SeverityFilter,
  filters: FilterState,
  pendingOnly: boolean,
): boolean {
  if (severity !== 'ALL' && diff.severity !== severity) return false
  if (filters.differenceType !== 'ALL' && diff.difference_type !== filters.differenceType) {
    return false
  }
  const sku = filters.sku.trim().toUpperCase()
  if (sku !== '') {
    // 后端按 subject_key 匹配；这里额外匹配界面上显示的中文主体串，
    // 免得用户照着屏幕上的字搜反而搜不到。
    const label = subjectLabel(diff.subject_kind, diff.subject_key).toUpperCase()
    if (!diff.subject_key.toUpperCase().includes(sku) && !label.includes(sku)) return false
  }
  if (filters.reviewStatus !== 'ALL' && diff.review_status !== filters.reviewStatus) return false
  if (filters.role !== 'ALL' && !(filters.role in diff.values_by_document)) return false
  if (pendingOnly && !PENDING_STATUSES.includes(diff.review_status)) return false
  return true
}

export function WorkbenchPage() {
  const params = useParams<{ projectId: string }>()
  const projectId = params.projectId ?? ''

  const project = useProject(projectId)
  const differences = useDifferences(projectId)
  const runCompare = useRunCompare(projectId)
  const setReview = useSetReview(projectId)

  const [severity, setSeverity] = useState<SeverityFilter>('ALL')
  const [pendingOnly, setPendingOnly] = useState(false)
  const [filters, setFilters] = useState<FilterState>(EMPTY_FILTERS)
  const [savingKey, setSavingKey] = useState<string | null>(null)

  const all = useMemo(() => differences.data?.items ?? [], [differences.data])

  const counts = useMemo(() => {
    const result: Record<string, number> = {}
    for (const diff of all) {
      result[diff.severity] = (result[diff.severity] ?? 0) + 1
    }
    return result
  }, [all])

  const pendingCount = useMemo(
    () => all.filter((diff) => PENDING_STATUSES.includes(diff.review_status)).length,
    [all],
  )

  const visible = useMemo(
    () =>
      all
        .filter((diff) => matches(diff, severity, filters, pendingOnly))
        // 最严重的排最前。同级保持后端给的稳定顺序。
        .sort((a, b) => severityRank(a.severity) - severityRank(b.severity)),
    [all, severity, filters, pendingOnly],
  )

  if (project.isPending) {
    return <p className="muted">加载中…</p>
  }

  if (project.error || !project.data) {
    return (
      <div className="page">
        <p className="slot-problem" role="alert">
          <strong>打开项目失败</strong>
          <span className="detail">{project.error?.message ?? '项目不存在'}</span>
        </p>
        <Link to="/projects">返回项目列表</Link>
      </div>
    )
  }

  const data = project.data
  const documentByRole = new Map(data.documents.map((doc) => [doc.role, doc]))
  // SPEC §1.3 的 |R| 是**角色集合**的大小，不是文档条数。后端只返回未被替换的
  // 文档（每个角色至多一份），但按角色去重才是规格原文，多返回一份历史版本
  // 也不会把「1 个角色」错算成「够 2 份可以跑了」。
  const usableRoles = new Set(data.documents.filter(isUsable).map((doc) => doc.role))
  const usableCount = usableRoles.size
  const canCompare = usableCount >= MIN_DOCUMENTS_TO_COMPARE

  return (
    <div className="page">
      <div className="crumbs">
        <Link to="/projects">← 项目列表</Link>
      </div>

      <header className="work-head">
        <div>
          <h2>{data.name}</h2>
          <p className="hint">
            状态：{projectStatusLabel(data.status)} ｜ 上次检查：
            {formatDateTime(data.compared_at)} ｜ 待处理 {pendingCount} 条
          </p>
        </div>
        <a
          className="btn btn-secondary"
          href={reportUrl(data.id)}
          data-testid="export-report"
          target="_blank"
          rel="noreferrer"
        >
          导出报告（HTML）
        </a>
      </header>

      <section className="slots" aria-label="单据上传">
        {DOCUMENT_ROLES.map((role) => (
          <UploadSlot
            key={role}
            projectId={data.id}
            role={role}
            optional={role === 'QUOTATION'}
            document={documentByRole.get(role)}
          />
        ))}
      </section>

      <div className="run-bar">
        <button
          type="button"
          className="btn btn-primary btn-run"
          data-testid="run-compare"
          disabled={!canCompare || runCompare.isPending}
          onClick={() => runCompare.mutate()}
        >
          {runCompare.isPending ? '检查中…' : '运行检查'}
        </button>
        <span className="hint">
          已上传可解析文档 {usableCount} 份。达到 {MIN_DOCUMENTS_TO_COMPARE} 份即可运行检查
          （报价单可以缺席）。
        </span>
      </div>

      {runCompare.error ? (
        <p className="slot-problem" role="alert">
          <strong>运行检查失败</strong>
          <span className="detail">{runCompare.error.message}</span>
        </p>
      ) : null}

      <CoverageBanner comparedRoles={data.compared_roles} skippedRoles={data.skipped_roles} />

      <section aria-label="差异总览">
        <h3>差异总览</h3>
        <SeverityChips
          counts={counts}
          total={all.length}
          selected={severity}
          onSelect={setSeverity}
          pendingCount={pendingCount}
          pendingOnly={pendingOnly}
          onTogglePending={() => setPendingOnly((value) => !value)}
        />
      </section>

      <section aria-label="差异清单">
        <h3>
          差异清单（显示 {visible.length} / {all.length} 条）
        </h3>
        <DifferenceFilters
          value={filters}
          onChange={setFilters}
          onReset={() => {
            setFilters(EMPTY_FILTERS)
            setSeverity('ALL')
            setPendingOnly(false)
          }}
        />

        {differences.error ? (
          <p className="slot-problem" role="alert">
            <strong>读取差异失败</strong>
            <span className="detail">{differences.error.message}</span>
          </p>
        ) : null}
        {setReview.error ? (
          <p className="slot-problem" role="alert">
            <strong>保存裁决失败</strong>
            <span className="detail">{setReview.error.message}</span>
          </p>
        ) : null}

        {data.skipped_roles.length > 0 ? (
          <p className="hint">
            未参与比较：{data.skipped_roles.map(roleLabel).join('、')}
            （这些单据上的内容一条都没有被检查）
          </p>
        ) : null}

        <DifferenceTable
          differences={visible}
          savingKey={savingKey}
          onSaveReview={(differenceKey, reviewStatus, reviewNote) => {
            setSavingKey(differenceKey)
            setReview.mutate(
              {
                differenceKey,
                body: { review_status: reviewStatus, review_note: reviewNote || null },
              },
              { onSettled: () => setSavingKey(null) },
            )
          }}
        />
      </section>
    </div>
  )
}
