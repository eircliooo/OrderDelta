/**
 * 测试夹具。**形状抄自 backend/tests/test_api_integration.py 的真实响应**，
 * 不依赖真实后端（fetch 被 mock）。
 */

import type {
  DifferenceOut,
  DocumentOut,
  EvidenceOut,
  ProjectOut,
  ValueOut,
} from '../src/api/types'

export function makeDocument(overrides: Partial<DocumentOut> = {}): DocumentOut {
  return {
    id: 'doc-1',
    role: 'PURCHASE_ORDER',
    original_filename: 'purchase_order.xlsx',
    file_size: 8123,
    sha256: 'a'.repeat(64),
    parse_status: 'OK',
    parse_reason_code: null,
    parse_detail: null,
    revision: 1,
    ...overrides,
  }
}

export function makeProject(overrides: Partial<ProjectOut> = {}): ProjectOut {
  return {
    id: 'p-1',
    name: '2026-07 陶瓷杯订单',
    status: 'COMPARED',
    created_at: '2026-07-20T09:00:00',
    updated_at: '2026-07-26T10:00:00',
    compared_at: '2026-07-26T10:00:00',
    documents: [
      makeDocument({ id: 'doc-q', role: 'QUOTATION', original_filename: 'quotation.xlsx' }),
      makeDocument({ id: 'doc-po', role: 'PURCHASE_ORDER' }),
      makeDocument({
        id: 'doc-pi',
        role: 'PROFORMA_INVOICE',
        original_filename: 'proforma_invoice.xlsx',
      }),
    ],
    compared_roles: ['PROFORMA_INVOICE', 'PURCHASE_ORDER', 'QUOTATION'],
    skipped_roles: [],
    severity_counts: { CRITICAL: 1, WARNING: 1, REVIEW: 1, INFO: 0 },
    open_count: 2,
    ...overrides,
  }
}

/** 两文件场景（SPEC §1.3）：只有 PO 与 PI，报价单缺席。 */
export function makeTwoDocProject(overrides: Partial<ProjectOut> = {}): ProjectOut {
  return makeProject({
    id: 'p-2',
    name: '只有 PO 和 PI 的项目',
    documents: [
      makeDocument({ id: 'doc-po', role: 'PURCHASE_ORDER' }),
      makeDocument({
        id: 'doc-pi',
        role: 'PROFORMA_INVOICE',
        original_filename: 'proforma_invoice.xlsx',
      }),
    ],
    compared_roles: ['PROFORMA_INVOICE', 'PURCHASE_ORDER'],
    skipped_roles: ['QUOTATION'],
    ...overrides,
  })
}

function value(overrides: Partial<ValueOut> = {}): ValueOut {
  return {
    value: null,
    source: 'PARSER',
    parser_value: null,
    correction_reason: null,
    warning: null,
    evidence_id: null,
    ...overrides,
  }
}

function evidence(overrides: Partial<EvidenceOut> = {}): EvidenceOut {
  return {
    evidence_id: 'ev-1',
    role: 'PURCHASE_ORDER',
    original_filename: 'purchase_order.xlsx',
    source_type: 'XLSX_CELL',
    sheet_name: 'PURCHASE ORDER',
    cell_reference: 'E12',
    raw_text: '1.25',
    derived_from: [],
    ...overrides,
  }
}

export const CRITICAL_KEY = '3f2a19c47b8e4d1096a5be21c7d40f88'
export const WARNING_KEY = '91bd0c7e5a4f42d3ae6c8b1207f5e3aa'
export const REVIEW_KEY = 'c07e5d3419ab4f628d5107ec9b3a6f21'
export const UNKNOWN_EXPLANATION_KEY = 'ab12cd34ef56ab78cd90ef12ab34cd56'
export const AMBIGUOUS_KEY = '7d19f4ba2c634e8fb0a37c5619e2d40b'

/**
 * AMBIGUOUS_MATCH。**不是边角情况**：CLAUDE.md 明确「多成员组一律 AMBIGUOUS_MATCH
 * 交人工」，所以任何有重复型号的单据都会走到这条。
 *
 * 它的 `signature` 参数是 `Q1:P2:I0`（SPEC §3.2 冻结形状），必须被翻成中文——
 * 参数形状抄自 backend/app/comparison/engine.py 的 ambiguous_match 分支。
 */
export function makeAmbiguousMatchDifference(): DifferenceOut {
  return {
    difference_key: AMBIGUOUS_KEY,
    scope: 'LINE_ITEM',
    subject_kind: 'MATCH_GROUP',
    subject_key: 'SKU:AB-300',
    field_name: null,
    difference_type: 'AMBIGUOUS_MATCH',
    severity: 'REVIEW',
    severity_rule_id: 'match@multi_per_role',
    chain_stage: 'WITHIN_DOCUMENT',
    baseline_role: null,
    target_role: null,
    identity_strength: 'STRONG',
    values_by_document: {
      QUOTATION: value({ value: 'AB-300' }),
      PURCHASE_ORDER: value({ value: 'AB-300' }),
    },
    explanation_key: 'ambiguous_match',
    explanation_params: {
      group: 'SKU:AB-300',
      signature: 'Q1:P2:I0',
      reason: 'PURCHASE_ORDER 上同一型号出现 2 行',
    },
    evidence: [],
    has_user_input: false,
    review_status: 'OPEN',
    review_note: null,
    stale_premise: null,
  }
}

export function makeDifferences(): DifferenceOut[] {
  return [
    {
      difference_key: CRITICAL_KEY,
      scope: 'LINE_ITEM',
      subject_kind: 'MATCH_GROUP',
      subject_key: 'SKU:AB-200',
      field_name: 'unit_price',
      difference_type: 'VALUE_CONFLICT',
      severity: 'CRITICAL',
      severity_rule_id: 'unit_price@ORDER_TO_CONFIRMATION',
      chain_stage: 'ORDER_TO_CONFIRMATION',
      baseline_role: 'PURCHASE_ORDER',
      target_role: 'PROFORMA_INVOICE',
      identity_strength: 'STRONG',
      values_by_document: {
        PURCHASE_ORDER: value({ value: '2.40', parser_value: '2.40', evidence_id: 'ev-po' }),
        PROFORMA_INVOICE: value({
          value: '2.50',
          parser_value: '2.50',
          evidence_id: 'ev-pi',
        }),
      },
      explanation_key: 'value_conflict',
      explanation_params: {
        field: '单价',
        buckets: 'PURCHASE_ORDER=2.40 | PROFORMA_INVOICE=2.50',
      },
      evidence: [
        evidence({ evidence_id: 'ev-po', cell_reference: 'E12', raw_text: '2.40' }),
        evidence({
          evidence_id: 'ev-pi',
          role: 'PROFORMA_INVOICE',
          original_filename: 'proforma_invoice.xlsx',
          sheet_name: 'PROFORMA INVOICE',
          cell_reference: 'E9',
          raw_text: '2.50',
        }),
      ],
      has_user_input: false,
      review_status: 'OPEN',
      review_note: null,
      stale_premise: null,
    },
    {
      difference_key: WARNING_KEY,
      scope: 'LINE_ITEM',
      subject_kind: 'MATCH_GROUP',
      subject_key: 'SKU:AB-100',
      field_name: 'quantity',
      difference_type: 'VALUE_CONFLICT',
      severity: 'WARNING',
      severity_rule_id: 'quantity@OFFER_TO_ORDER',
      chain_stage: 'OFFER_TO_ORDER',
      baseline_role: 'QUOTATION',
      target_role: 'PURCHASE_ORDER',
      identity_strength: 'STRONG',
      values_by_document: {
        QUOTATION: value({ value: '1000', parser_value: '1000', evidence_id: 'ev-q2' }),
        PURCHASE_ORDER: value({
          value: '1300',
          source: 'USER_CORRECTION',
          parser_value: '1200',
          correction_reason: '客户改单',
          evidence_id: 'ev-po2',
        }),
      },
      explanation_key: 'value_conflict',
      explanation_params: {
        field: '数量',
        buckets: 'QUOTATION=1000 | PURCHASE_ORDER=1300',
      },
      evidence: [
        evidence({
          evidence_id: 'ev-q2',
          role: 'QUOTATION',
          original_filename: 'quotation.xlsx',
          sheet_name: 'QUOTATION',
          cell_reference: 'C7',
          raw_text: '1,000',
        }),
      ],
      has_user_input: true,
      review_status: 'NEEDS_CONFIRMATION',
      review_note: '客户已口头加单',
      stale_premise: { QUOTATION: '1000', PURCHASE_ORDER: '1200' },
    },
    {
      difference_key: REVIEW_KEY,
      scope: 'CALCULATION',
      subject_kind: 'DOCUMENT_ROLE',
      subject_key: 'PROFORMA_INVOICE',
      field_name: 'grand_total',
      difference_type: 'CALCULATION_ERROR',
      severity: 'REVIEW',
      severity_rule_id: 'grand_total@unexplained_delta',
      chain_stage: 'WITHIN_DOCUMENT',
      baseline_role: null,
      target_role: null,
      identity_strength: 'STRONG',
      values_by_document: {
        PROFORMA_INVOICE: value({ value: '3120.00', parser_value: '3120.00' }),
      },
      explanation_key: 'unexplained_total_delta',
      explanation_params: {
        role: 'PROFORMA_INVOICE',
        sum_of_lines: '3020.00',
        grand_total: '3120.00',
        delta: '100.00',
      },
      evidence: [
        evidence({
          evidence_id: 'ev-total',
          role: 'PROFORMA_INVOICE',
          original_filename: 'proforma_invoice.xlsx',
          sheet_name: 'PROFORMA INVOICE',
          cell_reference: 'F20',
          raw_text: '=SUM(F9:F11)+100',
        }),
      ],
      has_user_input: false,
      review_status: 'CONFIRMED_DIFFERENCE',
      review_note: '含模具费',
      stale_premise: null,
    },
  ]
}

/**
 * 后端将来新增一个 explanation_key、前端模板表还没跟上时的样子。
 * 这条**必须照常出现在表里**（硬约束 #6：未知 key 要有兜底，不得白屏）。
 */
export function makeUnknownExplanationDifference(): DifferenceOut {
  return {
    difference_key: UNKNOWN_EXPLANATION_KEY,
    scope: 'DOCUMENT',
    subject_kind: 'DOCUMENT_ROLE',
    subject_key: 'PURCHASE_ORDER',
    field_name: 'payment_terms',
    difference_type: 'SEMANTIC_DIFFERENCE',
    severity: 'INFO',
    severity_rule_id: 'payment_terms@OFFER_TO_ORDER',
    chain_stage: 'OFFER_TO_ORDER',
    baseline_role: 'QUOTATION',
    target_role: 'PURCHASE_ORDER',
    identity_strength: 'WEAK',
    values_by_document: {
      QUOTATION: value({ value: 'T/T 30% deposit' }),
      PURCHASE_ORDER: value({ value: 'TT 30 percent' }),
    },
    explanation_key: 'brand_new_key_from_the_future',
    explanation_params: { field: '付款条件', detail: 'PURCHASE_ORDER 的表述不同' },
    evidence: [],
    has_user_input: false,
    review_status: 'OPEN',
    review_note: null,
    stale_premise: null,
  }
}
