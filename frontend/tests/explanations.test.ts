import { describe, expect, it } from 'vitest'

import {
  EXPLANATION_TEMPLATES,
  MISSING_PARAM_TEXT,
  PARAM_LOCALIZERS,
  localizeParams,
  renderExplanation,
} from '../src/labels/explanations'

describe('explanation 渲染（硬约束 #6）', () => {
  it('已登记的 key 渲染成中文句子', () => {
    const text = renderExplanation('value_conflict', {
      field: '单价',
      buckets: 'PURCHASE_ORDER=2.40 | PROFORMA_INVOICE=2.50',
    })
    expect(text).toContain('单价在各单据上取值不一致')
    expect(text).toContain('采购订单=2.40')
    expect(text).toContain('形式发票=2.50')
  })

  it('界面上不残留英文角色标识符', () => {
    const text = renderExplanation('missing_value', {
      field: '币种',
      present_roles: 'PURCHASE_ORDER',
      missing_roles: 'QUOTATION、PROFORMA_INVOICE',
    })
    expect(text).not.toMatch(/QUOTATION|PURCHASE_ORDER|PROFORMA_INVOICE/)
  })

  it('未登记的参数一字不动 —— 单据原文不得被「翻译」', () => {
    // 备注写 "AS PER QUOTATION Q2026-001" 的单据在外贸里极常见。
    // 无脑子串替换会把它改成 "AS PER 报价单 Q2026-001"，
    // 而界面恰恰宣称自己在引用原文。
    const localized = localizeParams({ remarks: 'AS PER QUOTATION Q2026-001' })
    expect(localized['remarks']).toBe('AS PER QUOTATION Q2026-001')
  })

  it('buckets 的等号右边（单据原文）不被翻译', () => {
    const text = renderExplanation('value_conflict', {
      field: '备注',
      buckets: 'QUOTATION=AS PER QUOTATION | PURCHASE_ORDER=SEE PO',
    })
    expect(text).toContain('报价单=AS PER QUOTATION')
    expect(text).toContain('采购订单=SEE PO')
  })

  it('未知 key 不抛异常，回退成「未登记的说明模板」并带上原始参数', () => {
    const text = renderExplanation('brand_new_key_from_the_future', {
      field: '付款条件',
      detail: 'PURCHASE_ORDER 的表述不同',
    })
    expect(text).toContain('未登记的说明模板')
    expect(text).toContain('brand_new_key_from_the_future')
    expect(text).toContain('付款条件')
    // detail 是引擎拼的中文句子，兜底路径同样要翻角色标识符
    expect(text).toContain('采购订单 的表述不同')
  })

  it('未知 key 且无参数时也不返回空串', () => {
    expect(renderExplanation('nope', {})).toBe('（未登记的说明模板 nope）')
    expect(renderExplanation('nope', null)).toBe('（未登记的说明模板 nope）')
    expect(renderExplanation('nope', undefined)).toBe('（未登记的说明模板 nope）')
  })

  it('缺参数用占位符补齐，不整条渲染失败', () => {
    const text = renderExplanation('line_arithmetic_mismatch', { role: 'PURCHASE_ORDER' })
    expect(text).toContain('采购订单')
    expect(text).toContain(MISSING_PARAM_TEXT)
  })

  it('sku 参数走 line_key 本地化，不再多补一个「行」字', () => {
    const text = renderExplanation('line_arithmetic_mismatch', {
      role: 'PROFORMA_INVOICE',
      sku: 'pos:Sheet1!16',
      expected: '1250.00',
      actual: '1200.00',
    })
    expect(text).toBe('形式发票 的 工作表 Sheet1 第 16 行：数量 × 单价 应为 1250.00，表上写的是 1200.00。')
  })

  it('ambiguous_match 的 signature 参数被翻成中文，不露出 Q1:P2:I0', () => {
    // AMBIGUOUS_MATCH 是主路径（CLAUDE.md：多成员组一律交人工），
    // 漏登记 signature 就等于每条歧义差异都在界面上印一串业务员读不懂的编码。
    const text = renderExplanation('ambiguous_match', {
      group: 'SKU:AB-300',
      signature: 'Q1:P2:I0',
      reason: 'PURCHASE_ORDER 上同一型号出现 2 行',
    })
    expect(text).toContain('报价单 1 行 / 采购订单 2 行 / 形式发票 0 行')
    expect(text).not.toContain('Q1:P2:I0')
    expect(text).not.toMatch(/QUOTATION|PURCHASE_ORDER|PROFORMA_INVOICE/)
  })

  it('参数本地化策略与后端 PARAM_LOCALIZERS 登记的参数名一一对应', () => {
    // 后端 backend/app/exports/html.py::PARAM_LOCALIZERS 的键集合。
    // 两边不一致 = 同一条差异在界面和报告上说的不是同一句话。
    const backendLocalizedParams = [
      'role',
      'present_roles',
      'missing_roles',
      'detail',
      'reason',
      'buckets',
      'group',
      'signature',
      'sku',
    ]
    expect(Object.keys(PARAM_LOCALIZERS).sort()).toEqual([...backendLocalizedParams].sort())
  })

  it('模板表覆盖后端 EXPLANATION_TEMPLATES 的全部 key', () => {
    // 与 backend/app/exports/html.py 手工对齐；漏一个就会在界面上露出英文 key。
    const backendKeys = [
      'value_conflict',
      'missing_value',
      'unmatched_line_item',
      'ambiguous_match',
      'line_arithmetic_mismatch',
      'unexplained_total_delta',
      'incomparable_units',
      'incomparable_currency',
      'incomparable_delivery_terms',
      'unstructured_payment_terms',
      'unparsable_number',
      'ambiguous_currency_symbol',
      'ambiguous_date',
      'incomparable',
    ]
    for (const key of backendKeys) {
      expect(Object.keys(EXPLANATION_TEMPLATES)).toContain(key)
    }
  })
})
