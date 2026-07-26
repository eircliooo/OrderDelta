import { useState } from 'react'

import type { DifferenceOut } from '../api/types'
import {
  chainStageLabel,
  differenceTypeLabel,
  fieldLabel,
  identityStrengthLabel,
  reviewStatusLabel,
  scopeLabel,
} from '../labels/enums'
import { renderExplanation } from '../labels/explanations'
import { subjectLabel } from '../labels/identifiers'
import { EvidencePanel } from './EvidencePanel'
import { ReviewControls } from './ReviewControls'
import { SeverityBadge } from './SeverityBadge'
import { ValueList } from './ValueList'

interface Props {
  differences: DifferenceOut[]
  onSaveReview: (differenceKey: string, reviewStatus: string, reviewNote: string) => void
  savingKey: string | null
}

export function DifferenceTable({ differences, onSaveReview, savingKey }: Props) {
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set())

  const toggle = (key: string) => {
    setExpanded((current) => {
      const next = new Set(current)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  if (differences.length === 0) {
    return (
      <p className="empty-state" data-testid="differences-empty">
        当前筛选条件下没有差异。请注意：<strong>没有差异不等于没有问题</strong>，
        未参与比较的单据完全没有被检查。
      </p>
    )
  }

  return (
    <table className="diff-table">
      <caption className="sr-only">差异清单</caption>
      <thead>
        <tr>
          <th scope="col" className="col-sev">
            风险等级
          </th>
          <th scope="col" className="col-type">
            差异类型
          </th>
          <th scope="col" className="col-subject">
            主体
          </th>
          <th scope="col" className="col-field">
            字段
          </th>
          <th scope="col" className="col-values">
            各角色取值
          </th>
          <th scope="col" className="col-explain">
            说明
          </th>
          <th scope="col" className="col-review">
            审核
          </th>
        </tr>
      </thead>

      {differences.map((diff) => {
        const key = diff.difference_key
        const open = expanded.has(key)
        return (
          <tbody key={key} className="diff-group" data-testid={`diff-${key}`}>
            <tr className={`diff-row row-${diff.severity.toLowerCase()}`}>
              <td>
                <SeverityBadge severity={diff.severity} />
              </td>
              <td>
                <div>{differenceTypeLabel(diff.difference_type)}</div>
                <div className="hint">{scopeLabel(diff.scope)}</div>
                <div className="hint">{chainStageLabel(diff.chain_stage)}</div>
              </td>
              <td>
                {/* 主体列显示中文可读串（与报告一致）；原始身份串留在 title 里备查。 */}
                <span className="subject" title={diff.subject_key}>
                  {subjectLabel(diff.subject_kind, diff.subject_key)}
                </span>
                <div className="hint">{identityStrengthLabel(diff.identity_strength)}</div>
              </td>
              <td>{fieldLabel(diff.field_name)}</td>
              <td>
                <ValueList values={diff.values_by_document} />
              </td>
              <td className="explain">
                {renderExplanation(diff.explanation_key, diff.explanation_params)}
              </td>
              <td>
                <div className="review-status-now">
                  当前：{reviewStatusLabel(diff.review_status)}
                </div>
                <ReviewControls
                  difference={diff}
                  onSave={onSaveReview}
                  isPending={savingKey === key}
                />
              </td>
            </tr>
            <tr className={`diff-evidence-row row-${diff.severity.toLowerCase()}`}>
              <td colSpan={7}>
                <button
                  type="button"
                  className="btn btn-quiet"
                  aria-expanded={open}
                  data-testid={`evidence-toggle-${key}`}
                  onClick={() => toggle(key)}
                >
                  {open ? '收起证据' : `展开证据（${diff.evidence.length} 条）`}
                </button>
                {open ? (
                  <div className="evidence-box" data-testid={`evidence-panel-${key}`}>
                    <EvidencePanel evidence={diff.evidence} />
                  </div>
                ) : null}
              </td>
            </tr>
          </tbody>
        )
      })}
    </table>
  )
}
