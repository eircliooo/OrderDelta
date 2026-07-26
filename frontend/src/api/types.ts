/**
 * API 线上类型。**形状以 backend/app/api/schemas.py 为准**，字段名逐一对应。
 *
 * 设计取舍：枚举字段一律声明成 `string` 而不是字面量联合。
 * 后端枚举是冻结词表，但前端**收到未登记的值时不得白屏**（硬约束 #6 的同类风险）：
 * 所有「枚举 -> 中文」查表都走 src/labels 里的兜底函数。
 * 只有前端自己产生的值（上传槽位的角色、芯片的严重度）才用字面量联合。
 */

export interface Envelope<T> {
  items: T[]
  total: number
}

export interface ErrorBody {
  error_code: string
  message: string
  detail?: string | null
}

export interface DocumentOut {
  id: string
  role: string
  original_filename: string
  file_size: number
  sha256: string
  parse_status: string
  parse_reason_code: string | null
  parse_detail: string | null
  revision: number
}

export interface ProjectOut {
  id: string
  name: string
  status: string
  created_at: string
  updated_at: string
  compared_at: string | null
  documents: DocumentOut[]
  /** 参与比较的角色。未上传/解析失败的角色进 skipped_roles，**不产生任何差异**。 */
  compared_roles: string[]
  skipped_roles: string[]
  severity_counts: Record<string, number>
  open_count: number
}

export interface ValueOut {
  value: string | null
  source: string
  parser_value: string | null
  correction_reason: string | null
  warning: string | null
  evidence_id: string | null
}

export interface EvidenceOut {
  evidence_id: string
  role: string
  original_filename: string
  source_type: string
  sheet_name: string | null
  cell_reference: string | null
  raw_text: string | null
  derived_from: string[]
}

export interface DifferenceOut {
  difference_key: string
  scope: string
  subject_kind: string
  subject_key: string
  field_name: string | null
  difference_type: string
  severity: string
  severity_rule_id: string
  chain_stage: string
  baseline_role: string | null
  target_role: string | null
  identity_strength: string
  values_by_document: Record<string, ValueOut>
  explanation_key: string
  explanation_params: Record<string, string>
  evidence: EvidenceOut[]
  has_user_input: boolean
  review_status: string
  review_note: string | null
  /** 前提已变时展示「你上次是基于 X 判断的」 */
  stale_premise: Record<string, string> | null
}

export interface ReviewIn {
  review_status: string
  review_note?: string | null
}

/** 前端自己产生的值，用字面量联合以获得穷尽性检查。 */
export const DOCUMENT_ROLES = ['QUOTATION', 'PURCHASE_ORDER', 'PROFORMA_INVOICE'] as const
export type DocumentRole = (typeof DOCUMENT_ROLES)[number]

export const SEVERITIES = ['CRITICAL', 'WARNING', 'REVIEW', 'INFO'] as const
export type Severity = (typeof SEVERITIES)[number]

/** SPEC §12.3 冻结词表。 */
export const REVIEW_STATUSES = [
  'OPEN',
  'CONFIRMED_DIFFERENCE',
  'ACCEPTED_DIFFERENCE',
  'NEEDS_CONFIRMATION',
  'IGNORED',
  'RESOLVED',
] as const
export type ReviewStatus = (typeof REVIEW_STATUSES)[number]

export const DIFFERENCE_TYPES = [
  'VALUE_CONFLICT',
  'MISSING_VALUE',
  'CALCULATION_ERROR',
  'UNMATCHED_LINE_ITEM',
  'AMBIGUOUS_MATCH',
  'SEMANTIC_DIFFERENCE',
  'EXTRACTION_UNCERTAIN',
  'INCOMPARABLE',
] as const
export type DifferenceType = (typeof DIFFERENCE_TYPES)[number]

/** 解析成功、可参与比较的状态（与后端 _project_out 的 usable 判定一致）。 */
export const USABLE_PARSE_STATUSES: readonly string[] = ['OK', 'NEEDS_REVIEW']

export function isUsable(document: DocumentOut): boolean {
  return USABLE_PARSE_STATUSES.includes(document.parse_status)
}
