import { SEVERITIES } from '../api/types'
import type { Severity } from '../api/types'
import { severityLabel, severityMark, severitySlug } from '../labels/enums'

export type SeverityFilter = Severity | 'ALL'

interface Props {
  counts: Record<string, number>
  total: number
  selected: SeverityFilter
  onSelect: (value: SeverityFilter) => void
  pendingCount: number
  pendingOnly: boolean
  onTogglePending: () => void
}

/**
 * 总览计数芯片，**兼作筛选器**（SPEC §13.2）。
 *
 * 顺序固定按严重度从重到轻，最严重的永远在最左边——业务员的眼睛从左上角开始。
 * 芯片同时带形状记号和中文文字，不靠颜色单独承载信息。
 */
export function SeverityChips({
  counts,
  total,
  selected,
  onSelect,
  pendingCount,
  pendingOnly,
  onTogglePending,
}: Props) {
  return (
    <div className="chips-area">
      <div className="chips" role="group" aria-label="按风险等级筛选">
        <button
          type="button"
          className={`chip chip-all${selected === 'ALL' ? ' chip-on' : ''}`}
          aria-pressed={selected === 'ALL'}
          onClick={() => onSelect('ALL')}
        >
          <span className="chip-label">全部</span>
          <span className="chip-count">{total}</span>
        </button>

        {SEVERITIES.map((severity) => {
          const on = selected === severity
          return (
            <button
              key={severity}
              type="button"
              data-testid={`chip-${severity}`}
              className={`chip chip-${severitySlug(severity)}${on ? ' chip-on' : ''}`}
              aria-pressed={on}
              onClick={() => onSelect(on ? 'ALL' : severity)}
            >
              <span className="chip-mark" aria-hidden="true">
                {severityMark(severity)}
              </span>
              <span className="chip-label">{severityLabel(severity)}</span>
              <span className="chip-count">{counts[severity] ?? 0}</span>
            </button>
          )
        })}
      </div>

      <div className="chips" role="group" aria-label="按处理状态筛选">
        <button
          type="button"
          data-testid="chip-PENDING"
          className={`chip chip-pending${pendingOnly ? ' chip-on' : ''}`}
          aria-pressed={pendingOnly}
          onClick={onTogglePending}
        >
          <span className="chip-label">待处理</span>
          <span className="chip-count">{pendingCount}</span>
        </button>
      </div>
    </div>
  )
}
