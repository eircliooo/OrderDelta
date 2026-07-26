/**
 * 「英文枚举 -> 中文」映射表（硬约束 #1：界面中文单语，禁止 i18n 框架）。
 *
 * 与 backend/app/exports/html.py 的标签表逐条对齐，报告和界面上同一个枚举
 * 必须是同一个中文词——业务员对着报告核对界面时词不一样会当成两件事。
 *
 * 所有查表函数遇到**未登记的标识符一律回退成原始标识符**，绝不抛异常：
 * 显示一个英文词很难看，但静默丢一条差异是本产品最危险的失败方式。
 */

function lookup(table: Readonly<Record<string, string>>, key: string | null | undefined): string {
  if (key === null || key === undefined || key === '') return '—'
  return table[key] ?? key
}

export const ROLE_LABEL: Readonly<Record<string, string>> = {
  QUOTATION: '报价单',
  PURCHASE_ORDER: '采购订单',
  PROFORMA_INVOICE: '形式发票',
}

export function roleLabel(role: string | null | undefined): string {
  return lookup(ROLE_LABEL, role)
}

/** 角色在贸易链条上的顺序，用于稳定排序（报价单 -> 采购订单 -> 形式发票）。 */
export const ROLE_ORDER: Readonly<Record<string, number>> = {
  QUOTATION: 0,
  PURCHASE_ORDER: 1,
  PROFORMA_INVOICE: 2,
}

export function roleRank(role: string): number {
  return ROLE_ORDER[role] ?? 99
}

export const SEVERITY_LABEL: Readonly<Record<string, string>> = {
  CRITICAL: '严重',
  WARNING: '警告',
  REVIEW: '待复核',
  INFO: '提示',
}

export function severityLabel(severity: string): string {
  return lookup(SEVERITY_LABEL, severity)
}

/**
 * 严重度**不靠颜色单独承载**（考虑色觉障碍）：形状记号 + 中文标签 + 颜色三重冗余。
 * 整页去掉颜色后依然可读。
 */
export const SEVERITY_MARK: Readonly<Record<string, string>> = {
  CRITICAL: '▲',
  WARNING: '◆',
  REVIEW: '■',
  INFO: '●',
}

export function severityMark(severity: string): string {
  return SEVERITY_MARK[severity] ?? '○'
}

/** CSS 修饰类后缀。未知严重度落到 unknown，不会顶掉样式。 */
export function severitySlug(severity: string): string {
  const known: Readonly<Record<string, string>> = {
    CRITICAL: 'critical',
    WARNING: 'warning',
    REVIEW: 'review',
    INFO: 'info',
  }
  return known[severity] ?? 'unknown'
}

export const SEVERITY_RANK: Readonly<Record<string, number>> = {
  CRITICAL: 0,
  WARNING: 1,
  REVIEW: 2,
  INFO: 3,
}

export function severityRank(severity: string): number {
  return SEVERITY_RANK[severity] ?? 99
}

export const DIFFERENCE_TYPE_LABEL: Readonly<Record<string, string>> = {
  VALUE_CONFLICT: '取值冲突',
  MISSING_VALUE: '字段缺失',
  CALCULATION_ERROR: '金额计算不符',
  UNMATCHED_LINE_ITEM: '行项目未对齐',
  AMBIGUOUS_MATCH: '匹配存在歧义',
  SEMANTIC_DIFFERENCE: '表述差异',
  EXTRACTION_UNCERTAIN: '提取不确定',
  INCOMPARABLE: '无法比较',
}

export function differenceTypeLabel(value: string): string {
  return lookup(DIFFERENCE_TYPE_LABEL, value)
}

export const SCOPE_LABEL: Readonly<Record<string, string>> = {
  DOCUMENT: '文档级',
  LINE_ITEM: '行项目',
  CALCULATION: '单据内计算',
}

export function scopeLabel(value: string): string {
  return lookup(SCOPE_LABEL, value)
}

export const CHAIN_STAGE_LABEL: Readonly<Record<string, string>> = {
  OFFER_TO_ORDER: '报价单 → 采购订单',
  ORDER_TO_CONFIRMATION: '采购订单 → 形式发票',
  OFFER_TO_CONFIRMATION: '报价单 → 形式发票',
  WITHIN_DOCUMENT: '单份单据内部',
}

export function chainStageLabel(value: string): string {
  return lookup(CHAIN_STAGE_LABEL, value)
}

export const VALUE_SOURCE_LABEL: Readonly<Record<string, string>> = {
  PARSER: '机器读取',
  USER_CORRECTION: '人工修正',
}

export function valueSourceLabel(value: string): string {
  return lookup(VALUE_SOURCE_LABEL, value)
}

export const EVIDENCE_SOURCE_LABEL: Readonly<Record<string, string>> = {
  XLSX_CELL: 'Excel 单元格',
  XLSX_RANGE: 'Excel 区域',
  PDF_TEXT: 'PDF 文本',
  DERIVED: '推导（算式）',
}

export function evidenceSourceLabel(value: string): string {
  return lookup(EVIDENCE_SOURCE_LABEL, value)
}

export const PARSE_STATUS_LABEL: Readonly<Record<string, string>> = {
  PENDING: '待处理',
  OK: '解析成功',
  NEEDS_REVIEW: '需人工复核',
  REJECTED: '已拒绝',
  FAILED: '解析失败',
}

export function parseStatusLabel(value: string): string {
  return lookup(PARSE_STATUS_LABEL, value)
}

export const PARSE_REASON_LABEL: Readonly<Record<string, string>> = {
  UNSUPPORTED_EXT: '不支持的文件类型',
  ENCRYPTED: '文件已加密',
  CORRUPT: '文件损坏',
  INSUFFICIENT_TEXT: '可用文本过少',
  UNSUPPORTED_TEXT_LAYER: '文本层不可用',
  ROW_LIMIT: '超出行数上限',
  SHEET_LIMIT: '超出工作表数上限',
  NO_TABLE_FOUND: '未定位到订单表格',
  FORMULA_WITHOUT_CACHE: '公式没有缓存值',
  FILE_TOO_LARGE: '文件过大',
}

export function parseReasonLabel(value: string | null): string {
  return lookup(PARSE_REASON_LABEL, value)
}

export const REVIEW_STATUS_LABEL: Readonly<Record<string, string>> = {
  OPEN: '未处理',
  CONFIRMED_DIFFERENCE: '确认存在差异',
  ACCEPTED_DIFFERENCE: '接受该差异',
  NEEDS_CONFIRMATION: '待重新确认',
  IGNORED: '忽略',
  RESOLVED: '已解决',
}

export function reviewStatusLabel(value: string): string {
  return lookup(REVIEW_STATUS_LABEL, value)
}

export const PROJECT_STATUS_LABEL: Readonly<Record<string, string>> = {
  DRAFT: '草稿',
  READY: '待检查',
  COMPARED: '已检查',
}

export function projectStatusLabel(value: string): string {
  return lookup(PROJECT_STATUS_LABEL, value)
}

export const IDENTITY_STRENGTH_LABEL: Readonly<Record<string, string>> = {
  STRONG: '强身份',
  WEAK: '弱身份',
}

export function identityStrengthLabel(value: string): string {
  return lookup(IDENTITY_STRENGTH_LABEL, value)
}

/** 字段 key -> 中文名。与 backend/app/domain/fields.py 的 label_zh 一致。 */
export const FIELD_LABEL: Readonly<Record<string, string>> = {
  internal_sku: '内部型号',
  customer_part_number: '客户料号',
  description: '产品描述',
  specification: '规格',
  color: '颜色',
  quantity: '数量',
  unit: '单位',
  unit_price: '单价',
  currency: '币种',
  line_total: '行金额',
  packaging_quantity: '装箱数量',
  carton_count: '箱数',
  remarks: '备注',
  document_number: '单据号',
  document_date: '单据日期',
  buyer_name: '买方',
  seller_name: '卖方',
  incoterm: '贸易术语',
  incoterm_named_place: '贸易术语地点',
  incoterm_version: '贸易术语版本',
  payment_terms: '付款条件',
  delivery_terms: '交期',
  destination: '目的地',
  shipping_method: '运输方式',
  grand_total: '总金额',
}

export function fieldLabel(value: string | null): string {
  return lookup(FIELD_LABEL, value)
}

/** 没有取到值时的占位。空白在表格里必须**看得见**。 */
export const EMPTY_VALUE_TEXT = '（未提取到）'

/** SPEC §1.3：集合驱动比较引入的新失效模式，不得隐藏。逐字与报告一致。 */
export const COVERAGE_WARNING = '缺席角色 = 未检查，不等于无差异'

/** SPEC §1.2「不是什么」。必须常驻可见。 */
export const DISCLAIMER = '本工具只能辅助核对，不判断哪份文件正确，不构成贸易、法律或财务结论。'

/**
 * 删除项目的措辞。CLAUDE.md 安全红线：只能说「删除数据库记录与磁盘文件」，
 * **不得宣称「安全擦除」「不可恢复」**。
 */
export const DELETE_PROJECT_WARNING = '将删除数据库记录与磁盘上的原始文件。'

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/** 后端返回 ISO 字符串；无效值原样显示，不做时区推断。 */
export function formatDateTime(value: string | null): string {
  if (!value) return '—'
  return value.replace('T', ' ').slice(0, 19)
}
