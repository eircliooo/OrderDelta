import type { EvidenceOut } from '../api/types'
import { evidenceSourceLabel, roleLabel, roleRank } from '../labels/enums'

interface Props {
  evidence: EvidenceOut[]
}

/**
 * 证据面板：文件名 / 角色 / 工作表 / 单元格地址 / 原文。
 *
 * 「原文」是**未经标准化的单元格文本**。业务员拿着单元格地址能直接回 Excel 里核对，
 * 这是本工具与「凭感觉说有问题」的唯一区别，因此地址和原文都不能省。
 */
export function EvidencePanel({ evidence }: Props) {
  if (evidence.length === 0) {
    return <p className="muted">这条差异没有附带证据记录。</p>
  }

  const rows = [...evidence].sort((a, b) => roleRank(a.role) - roleRank(b.role))

  return (
    <table className="evidence-table">
      <thead>
        <tr>
          <th scope="col">角色</th>
          <th scope="col">文件名</th>
          <th scope="col">来源</th>
          <th scope="col">工作表</th>
          <th scope="col">单元格</th>
          <th scope="col">原文</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((item) => (
          <tr key={item.evidence_id}>
            <td>{roleLabel(item.role)}</td>
            <td>{item.original_filename || '—'}</td>
            <td>{evidenceSourceLabel(item.source_type)}</td>
            <td>{item.sheet_name ?? '—'}</td>
            <td>
              <code className="code">{item.cell_reference ?? '—'}</code>
            </td>
            <td>
              <span className="raw-text">{item.raw_text ?? '—'}</span>
              {item.derived_from.length > 0 ? (
                <span className="hint">推导自：{item.derived_from.join('、')}</span>
              ) : null}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
