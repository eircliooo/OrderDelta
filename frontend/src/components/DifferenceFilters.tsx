import { DIFFERENCE_TYPES, DOCUMENT_ROLES, REVIEW_STATUSES } from '../api/types'
import { differenceTypeLabel, reviewStatusLabel, roleLabel } from '../labels/enums'

export interface FilterState {
  differenceType: string
  sku: string
  reviewStatus: string
  role: string
}

export const EMPTY_FILTERS: FilterState = {
  differenceType: 'ALL',
  sku: '',
  reviewStatus: 'ALL',
  role: 'ALL',
}

interface Props {
  value: FilterState
  onChange: (next: FilterState) => void
  onReset: () => void
}

export function DifferenceFilters({ value, onChange, onReset }: Props) {
  return (
    <div className="filters" role="group" aria-label="差异筛选">
      <label className="filter">
        <span>差异类型</span>
        <select
          value={value.differenceType}
          onChange={(event) => onChange({ ...value, differenceType: event.target.value })}
        >
          <option value="ALL">全部类型</option>
          {DIFFERENCE_TYPES.map((type) => (
            <option key={type} value={type}>
              {differenceTypeLabel(type)}
            </option>
          ))}
        </select>
      </label>

      <label className="filter">
        <span>SKU 关键词</span>
        <input
          type="search"
          placeholder="例如 AB-100"
          value={value.sku}
          onChange={(event) => onChange({ ...value, sku: event.target.value })}
        />
      </label>

      <label className="filter">
        <span>审核状态</span>
        <select
          value={value.reviewStatus}
          onChange={(event) => onChange({ ...value, reviewStatus: event.target.value })}
        >
          <option value="ALL">全部状态</option>
          {REVIEW_STATUSES.map((status) => (
            <option key={status} value={status}>
              {reviewStatusLabel(status)}
            </option>
          ))}
        </select>
      </label>

      <label className="filter">
        <span>文档角色</span>
        <select
          value={value.role}
          onChange={(event) => onChange({ ...value, role: event.target.value })}
        >
          <option value="ALL">全部角色</option>
          {DOCUMENT_ROLES.map((role) => (
            <option key={role} value={role}>
              {roleLabel(role)}
            </option>
          ))}
        </select>
      </label>

      <button type="button" className="btn btn-quiet" onClick={onReset}>
        清空筛选
      </button>
    </div>
  )
}
