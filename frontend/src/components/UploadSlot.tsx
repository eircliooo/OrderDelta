import { useId } from 'react'

import { ApiError } from '../api/client'
import { useUploadDocument } from '../api/queries'
import type { DocumentOut } from '../api/types'
import { isUsable } from '../api/types'
import {
  formatFileSize,
  parseReasonLabel,
  parseStatusLabel,
  roleLabel,
} from '../labels/enums'

interface Props {
  projectId: string
  role: string
  /** 报价单是可选的：正式报价单在中小外贸里常常根本不存在（SPEC §1.3）。 */
  optional?: boolean
  document: DocumentOut | undefined
}

function statusSlug(document: DocumentOut): string {
  if (isUsable(document)) {
    return document.parse_status === 'NEEDS_REVIEW' ? 'warn' : 'ok'
  }
  return 'bad'
}

/**
 * 单个上传槽位。上传、替换、解析状态、失败原因都在这一个盒子里。
 *
 * 解析失败**必须看得见且带错误码**：业务员看到「解析失败」会重传，
 * 看到「FORMULA_WITHOUT_CACHE / 公式没有缓存值」才知道要去 Excel 里按一次保存。
 */
export function UploadSlot({ projectId, role, optional = false, document }: Props) {
  const inputId = useId()
  const upload = useUploadDocument(projectId)

  const label = roleLabel(role)
  const error = upload.error

  return (
    <section className="slot" data-testid={`slot-${role}`}>
      <header className="slot-head">
        <h3 className="slot-title">{label}</h3>
        {optional ? <span className="tag tag-optional">可选</span> : null}
      </header>

      {document ? (
        <dl className="slot-meta">
          <div>
            <dt>文件名</dt>
            <dd data-testid={`filename-${role}`}>{document.original_filename}</dd>
          </div>
          <div>
            <dt>大小</dt>
            <dd>{formatFileSize(document.file_size)}</dd>
          </div>
          <div>
            <dt>解析状态</dt>
            <dd>
              <span className={`pstatus pstatus-${statusSlug(document)}`}>
                {parseStatusLabel(document.parse_status)}
              </span>
            </dd>
          </div>
        </dl>
      ) : (
        <p className="slot-empty">尚未上传{optional ? '（可以不传）' : ''}</p>
      )}

      {document && document.parse_reason_code ? (
        <p className="slot-problem" role="alert">
          <strong>{parseReasonLabel(document.parse_reason_code)}</strong>
          <code className="code">{document.parse_reason_code}</code>
          {document.parse_detail ? <span className="detail">{document.parse_detail}</span> : null}
        </p>
      ) : null}

      {error ? (
        <p className="slot-problem" role="alert">
          <strong>上传被拒绝</strong>
          {error instanceof ApiError ? <code className="code">{error.errorCode}</code> : null}
          <span className="detail">{error.message}</span>
          {error instanceof ApiError && error.detail ? (
            <span className="detail">{error.detail}</span>
          ) : null}
        </p>
      ) : null}

      <div className="slot-actions">
        <label className="file-label" htmlFor={inputId}>
          {label}：{document ? '替换文件' : '选择文件'}
        </label>
        <input
          id={inputId}
          data-testid={`upload-input-${role}`}
          type="file"
          accept=".xlsx"
          disabled={upload.isPending}
          onChange={(event) => {
            const file = event.target.files?.[0]
            event.target.value = ''
            if (!file) return
            upload.mutate({ role, file })
          }}
        />
        {upload.isPending ? <span className="hint">上传中…</span> : null}
      </div>
      <p className="hint">仅接受 .xlsx（不支持 .xls / .xlsm / PDF / 扫描件）</p>
    </section>
  )
}
