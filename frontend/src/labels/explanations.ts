/**
 * explanation_key + explanation_params -> 中文句子（硬约束 #6 / SPEC §13.1）。
 *
 * 后端**不存拼好的句子**，只给 key 和参数；句子在这一层拼。
 * 模板与 backend/app/exports/html.py 的 EXPLANATION_TEMPLATES 逐条一致，
 * 参数本地化策略与其 PARAM_LOCALIZERS 逐条一致——同一条差异在界面和报告里
 * 必须说同一句话，两处各写一份翻译迟早会漂移。
 *
 * 三条兜底（都必须有，否则一个未登记的 key 就是一次白屏）：
 *   1. **未知 key**   -> 「（未登记的说明模板 X）参数…」，差异照常出现在表里。
 *   2. **缺参数**     -> 占位「（未提供）」，不整条渲染失败。
 *   3. **未登记参数** -> **一个字符都不动**（可能是单据原文）。
 */

import {
  lineKeyLabel,
  localizeBuckets,
  localizeGroupKey,
  localizeProse,
  localizeRoleList,
  localizeSignature,
} from './identifiers'

export const EXPLANATION_TEMPLATES: Readonly<Record<string, string>> = {
  value_conflict: '{field}在各单据上取值不一致：{buckets}。',
  missing_value: '{field}只在 {present_roles} 上提取到，{missing_roles} 未提取到该字段。',
  unmatched_line_item:
    '该行项目只出现在 {present_roles}，{missing_roles} 上没有对应行。比对分组：{group}。',
  ambiguous_match:
    '同一型号在单份单据里出现多行（成员分布 {signature}），本工具不做求和、' +
    '不做拆合推断，请人工确认。比对分组：{group}；匹配理由：{reason}。',
  // `{sku}` 可能是型号，也可能是「工作表 X 第 N 行」（无型号行），
  // 所以后面不能再补一个「行」字，否则会出现「第 16 行 行：」。
  line_arithmetic_mismatch: '{role} 的 {sku}：数量 × 单价 应为 {expected}，表上写的是 {actual}。',
  unexplained_total_delta:
    '{role} 的合计与行金额之和存在未解释差额 {delta}' +
    '（行金额合计 {sum_of_lines}，表上总金额 {grand_total}），' +
    '可能来自运费 / 折扣 / 税费，需人工确认。',
  incomparable_units: '{field}无法直接比较：{detail}',
  incomparable_currency: '{field}无法直接比较：{detail}',
  incomparable_delivery_terms: '{field}需人工换算后才能比较：{detail}',
  unstructured_payment_terms: '{field}无法可靠结构化：{detail}',
  unparsable_number: '{field}的提取结果无法解析为数值：{detail}',
  ambiguous_currency_symbol: '{field}存在币种歧义：{detail}',
  ambiguous_date: '{field}存在日期歧义：{detail}',
  incomparable: '{field}在结构上无法比较：{detail}',
}

/** 模板缺参数时的占位。缺参数是代码缺陷，但界面不能因此整条渲染失败。 */
export const MISSING_PARAM_TEXT = '（未提供）'

/** 未登记参数的默认策略：**一个字符都不动。** */
function verbatim(value: string): string {
  return value
}

/**
 * 参数名 -> 本地化策略。**未登记的参数原样输出**。
 *
 * 登记新参数前先回答一个问题：它的内容是引擎拼的，还是从单据里读出来的？
 */
export const PARAM_LOCALIZERS: Readonly<Record<string, (value: string) => string>> = {
  // ① 纯角色标识符（单个或「、」分隔）
  role: localizeRoleList,
  present_roles: localizeRoleList,
  missing_roles: localizeRoleList,
  // ② 引擎拼的中文句子，里面嵌了角色标识符
  detail: localizeProse,
  reason: localizeProse,
  // ③ 结构化串：角色名单要翻，单据原文不能动
  buckets: localizeBuckets,
  group: localizeGroupKey,
  signature: localizeSignature,
  sku: lineKeyLabel,
}

export function localizeParams(params: Record<string, string>): Record<string, string> {
  const out: Record<string, string> = {}
  for (const [name, value] of Object.entries(params)) {
    out[name] = (PARAM_LOCALIZERS[name] ?? verbatim)(value)
  }
  return out
}

function joinParams(params: Record<string, string>): string {
  return Object.keys(params)
    .sort()
    .map((name) => `${name}=${params[name] ?? ''}`)
    .join('；')
}

/**
 * 渲染说明。未知 key 不抛异常、不返回空串——**宁可难看也要让用户看见有这么一条差异**。
 */
export function renderExplanation(
  key: string,
  params: Record<string, string> | null | undefined,
): string {
  const localized = localizeParams(params ?? {})
  const template = EXPLANATION_TEMPLATES[key]
  if (template === undefined) {
    const detail = joinParams(localized)
    return detail ? `（未登记的说明模板 ${key}）${detail}` : `（未登记的说明模板 ${key}）`
  }
  return template.replace(/\{(\w+)\}/g, (_match, name: string) => {
    const value = localized[name]
    return value === undefined ? MISSING_PARAM_TEXT : value
  })
}
