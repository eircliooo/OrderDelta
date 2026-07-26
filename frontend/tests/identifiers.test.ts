import { describe, expect, it } from 'vitest'

import {
  lineKeyLabel,
  localizeBuckets,
  localizeGroupKey,
  localizeProse,
  localizeRoleList,
  localizeSignature,
  subjectLabel,
} from '../src/labels/identifiers'

describe('标识符本地化', () => {
  it('角色名单逐 token 精确匹配', () => {
    expect(localizeRoleList('QUOTATION、PROFORMA_INVOICE')).toBe('报价单、形式发票')
    expect(localizeRoleList('PURCHASE_ORDER')).toBe('采购订单')
  })

  it('不是角色标识符的 token 原样保留', () => {
    expect(localizeRoleList('QUOTATION、AB-100')).toBe('报价单、AB-100')
  })

  it('buckets 只翻等号左边，等号右边的单据原文一字不动', () => {
    expect(localizeBuckets('QUOTATION、PURCHASE_ORDER=AS PER QUOTATION Q2026-001')).toBe(
      '报价单、采购订单=AS PER QUOTATION Q2026-001',
    )
    expect(localizeBuckets('PURCHASE_ORDER=2.40 | PROFORMA_INVOICE=2.50')).toBe(
      '采购订单=2.40 | 形式发票=2.50',
    )
  })

  it('引擎拼的中文句子里才做子串替换', () => {
    expect(localizeProse('QUOTATION 的数量无法解析')).toBe('报价单 的数量无法解析')
  })

  it('line_key 转成中文可读串', () => {
    expect(lineKeyLabel('sku:AB-200#1')).toBe('AB-200')
    expect(lineKeyLabel('sku:AB-200#2')).toBe('AB-200（第 2 次出现）')
    expect(lineKeyLabel('cpn:C-77#1')).toBe('客户料号 C-77')
    expect(lineKeyLabel('pos:Sheet1!16')).toBe('工作表 Sheet1 第 16 行')
  })

  it('group_key 转成中文可读串，SKU 段不做任何替换', () => {
    expect(localizeGroupKey('SKU:AB-100')).toBe('AB-100')
    expect(localizeGroupKey('NOSKU:PURCHASE_ORDER:16')).toBe('采购订单第 16 行（未标型号）')
    // SKU 里恰好含角色词也不能被改
    expect(localizeGroupKey('SKU:QUOTATION-KIT')).toBe('QUOTATION-KIT')
  })

  it('role_signature 翻成中文，与后端 localize_signature 逐字一致', () => {
    // backend/app/exports/html.py::localize_signature 的 docstring 就是这个例子。
    expect(localizeSignature('Q1:P2:I0')).toBe('报价单 1 行 / 采购订单 2 行 / 形式发票 0 行')
  })

  it('signature 形状不对时原样输出，不瞎猜', () => {
    expect(localizeSignature('Q1:P2')).toBe('Q1:P2') // 少一段
    expect(localizeSignature('Q1:P2:I0:X3')).toBe('Q1:P2:I0:X3') // 多一段
    expect(localizeSignature('P1:Q2:I0')).toBe('P1:Q2:I0') // 顺序不对
    expect(localizeSignature('Q1:Q2:Q0')).toBe('Q1:Q2:Q0') // 同一角色重复
    expect(localizeSignature('X1:P2:I0')).toBe('X1:P2:I0') // 未知缩写
    expect(localizeSignature('Qx:P2:I0')).toBe('Qx:P2:I0') // 计数不是数字
    expect(localizeSignature('')).toBe('')
    expect(localizeSignature('Q:P2:I0')).toBe('Q:P2:I0') // 缺计数
  })

  it('主体列显示中文可读串', () => {
    expect(subjectLabel('MATCH_GROUP', 'SKU:AB-200')).toBe('AB-200')
    expect(subjectLabel('DOCUMENT_ROLE', 'PROFORMA_INVOICE')).toBe('形式发票')
    expect(subjectLabel('DOCUMENT_ROLE', 'PROFORMA_INVOICE#sku:AB-200#1')).toBe(
      '形式发票·AB-200',
    )
    expect(subjectLabel('DOCUMENT_ROLE', 'PROJECT')).toBe('全部单据')
  })
})
