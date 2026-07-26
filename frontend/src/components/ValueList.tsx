import type { ValueOut } from '../api/types'
import { EMPTY_VALUE_TEXT, roleLabel, roleRank, valueSourceLabel } from '../labels/enums'

interface Props {
  values: Record<string, ValueOut>
}

/**
 * 各角色取值。硬约束 #7：**值必须标出来源**。
 *
 * `source = USER_CORRECTION` 的值旁边标「人工修正」，并且**同时显示 parser_value**
 * （机器原读数）。报告要发给老板和客户，「这个数是机器读的还是人填的」
 * 必须精确到字段——只显示最终值等于把人工断言伪装成机器读数。
 */
export function ValueList({ values }: Props) {
  const rows = Object.entries(values).sort(([a], [b]) => roleRank(a) - roleRank(b))

  if (rows.length === 0) {
    return <span className="muted">（无取值）</span>
  }

  return (
    <ul className="values">
      {rows.map(([role, value]) => {
        const corrected = value.source === 'USER_CORRECTION'
        return (
          <li key={role} className="value-row" data-testid={`value-${role}`}>
            <span className="value-role">{roleLabel(role)}</span>
            <span className={`value-text${value.value ? '' : ' muted'}`}>
              {value.value ?? EMPTY_VALUE_TEXT}
            </span>
            {corrected ? (
              <>
                <span className="tag tag-correction">{valueSourceLabel(value.source)}</span>
                <span className="value-parser">
                  机器原读数：{value.parser_value ?? EMPTY_VALUE_TEXT}
                </span>
              </>
            ) : null}
            {corrected && value.correction_reason ? (
              <span className="value-reason">修正理由：{value.correction_reason}</span>
            ) : null}
            {value.warning ? <span className="value-warning">⚠ {value.warning}</span> : null}
          </li>
        )
      })}
    </ul>
  )
}
