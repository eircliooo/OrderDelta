import { useState } from 'react'

import type { DifferenceOut } from '../api/types'
import { REVIEW_STATUSES } from '../api/types'
import { reviewStatusLabel, roleLabel, roleRank } from '../labels/enums'

interface Props {
  difference: DifferenceOut
  onSave: (differenceKey: string, reviewStatus: string, reviewNote: string) => void
  isPending: boolean
}

/** 把 stale_premise（角色 -> 当初的值）拼成「报价单 1200、采购订单 1300」。 */
export function formatStalePremise(premise: Record<string, string>): string {
  return Object.entries(premise)
    .sort(([a], [b]) => roleRank(a) - roleRank(b))
    .map(([role, value]) => `${roleLabel(role)} ${value}`)
    .join('、')
}

/**
 * 审核裁决：六种状态 + 备注。
 *
 * `NEEDS_CONFIRMATION` + `stale_premise` 时必须显示**「你上次是基于 X 判断的」**：
 * 重跑后前提变了却沿用旧裁决，是这套「裁决可继承」机制唯一会伤人的地方，
 * 必须把旧前提摆在人眼前，而不是安静地把状态改回待确认。
 */
export function ReviewControls({ difference, onSave, isPending }: Props) {
  const serverStatus = difference.review_status
  const serverNote = difference.review_note ?? ''

  const [status, setStatus] = useState(serverStatus)
  const [note, setNote] = useState(serverNote)
  const [seen, setSeen] = useState({ status: serverStatus, note: serverNote })

  // 服务端值变化（重跑 / 保存成功后）时同步草稿，但不打断正在输入的人。
  if (seen.status !== serverStatus || seen.note !== serverNote) {
    setSeen({ status: serverStatus, note: serverNote })
    setStatus(serverStatus)
    setNote(serverNote)
  }

  const dirty = status !== serverStatus || note !== serverNote
  const stale = difference.stale_premise

  return (
    <div className="review">
      <label className="review-field">
        <span className="review-label">审核状态</span>
        <select
          data-testid={`review-status-${difference.difference_key}`}
          value={status}
          onChange={(event) => setStatus(event.target.value)}
        >
          {REVIEW_STATUSES.map((value) => (
            <option key={value} value={value}>
              {reviewStatusLabel(value)}
            </option>
          ))}
          {REVIEW_STATUSES.includes(status as (typeof REVIEW_STATUSES)[number]) ? null : (
            // 后端将来加了新状态时，下拉里也得有它，否则界面会把当前状态显示成别的。
            // 走同一张查表函数（未登记时回退成原标识符），不写死英文。
            <option value={status}>{reviewStatusLabel(status)}</option>
          )}
        </select>
      </label>

      <label className="review-field">
        <span className="review-label">备注</span>
        <input
          type="text"
          data-testid={`review-note-${difference.difference_key}`}
          value={note}
          placeholder="写清依据，便于下次重跑时回看"
          onChange={(event) => setNote(event.target.value)}
        />
      </label>

      <button
        type="button"
        className="btn btn-primary"
        data-testid={`review-save-${difference.difference_key}`}
        disabled={!dirty || isPending}
        onClick={() => onSave(difference.difference_key, status, note)}
      >
        保存裁决
      </button>

      {serverStatus === 'NEEDS_CONFIRMATION' && stale && Object.keys(stale).length > 0 ? (
        <p className="stale" role="note" data-testid={`stale-${difference.difference_key}`}>
          你上次是基于 {formatStalePremise(stale)} 判断的，现在这些值已经变了，请重新确认。
        </p>
      ) : null}
    </div>
  )
}
