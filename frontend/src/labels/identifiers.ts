/**
 * 内部标识符 -> 中文（角色名单、身份串、匹配组键）。
 *
 * **这一层的分工是前端最容易写错的地方**，与 backend/app/exports/html.py 同一套规则。
 * 引擎产出的 explanation_params 里混着三类东西：
 *   ① 引擎自己拼的英文角色标识符（`missing_roles` = "QUOTATION、PROFORMA_INVOICE"）
 *   ② 引擎自己拼的中文句子里嵌的角色标识符（`detail` = "QUOTATION 的数量无法解析"）
 *   ③ **单据原文**（`buckets` 的等号右边、`sku`、`field`）
 * 对三类一视同仁地做 `replace('QUOTATION', '报价单')` 会把 ③ 一起改掉：
 * 备注写 "AS PER QUOTATION Q2026-001" 的单据（外贸单据里极常见）会在界面上变成
 * "AS PER 报价单 Q2026-001" —— 界面恰恰宣称自己在**引用原文**。
 * 因此按**参数名**分派策略，未登记的参数一律原样输出。
 */

import { ROLE_LABEL } from './enums'

/** 引擎拼接角色名单用的分隔符（见 backend comparison/engine.py 的 "、".join(...)）。 */
const ROLE_JOINER = '、'

/** 引擎拼接 buckets 用的分隔符：`ROLE、ROLE=值 | ROLE=值`。 */
const BUCKET_JOINER = ' | '

function partition(text: string, sep: string): [string, string, string] {
  const index = text.indexOf(sep)
  if (index === -1) return [text, '', '']
  return [text.slice(0, index), sep, text.slice(index + sep.length)]
}

function rpartition(text: string, sep: string): [string, string, string] {
  const index = text.lastIndexOf(sep)
  if (index === -1) return ['', '', text]
  return [text.slice(0, index), sep, text.slice(index + sep.length)]
}

/** 精确匹配角色标识符；不是角色的 token 原样返回。 */
export function roleLabelOf(name: string): string {
  return ROLE_LABEL[name] ?? name
}

/** 「、」分隔的角色标识符串 -> 中文标签。**逐 token 精确匹配，不做子串替换**。 */
export function localizeRoleList(text: string): string {
  return text
    .split(ROLE_JOINER)
    .map((token) => roleLabelOf(token.trim()))
    .join(ROLE_JOINER)
}

/**
 * 引擎自己拼的中文句子里嵌了英文角色标识符，**只有这里**允许子串替换。
 *
 * 绝不用于任何单据原文（备注 / 产品描述 / 买方名称里出现 QUOTATION 一词完全正常，
 * 替换掉就是篡改原文）。三个角色标识符互不为子串，替换是确定的。
 */
export function localizeProse(text: string): string {
  let out = text
  for (const [identifier, label] of Object.entries(ROLE_LABEL)) {
    out = out.split(identifier).join(label)
  }
  return out
}

/**
 * `ROLE、ROLE=值 | ROLE=值` -> `中文、中文=值 | 中文=值`。
 *
 * **只翻译等号左边的角色名单，等号右边是单据原文，一字不动。**
 */
export function localizeBuckets(text: string): string {
  return text
    .split(BUCKET_JOINER)
    .map((chunk) => {
      const [roles, sep, value] = partition(chunk, '=')
      return sep ? `${localizeRoleList(roles)}${sep}${value}` : chunk
    })
    .join(BUCKET_JOINER)
}

/**
 * role_signature 的角色缩写与固定顺序（SPEC §3.2 定死的 `Q1:P2:I0` 形状）。
 * 与 backend/app/domain/enums.py 的 `SIGNATURE_TAGS` 同序同内容。
 */
const SIGNATURE_TAGS: ReadonlyArray<readonly [string, string]> = [
  ['Q', 'QUOTATION'],
  ['P', 'PURCHASE_ORDER'],
  ['I', 'PROFORMA_INVOICE'],
]

/**
 * `Q1:P2:I0` -> `报价单 1 行 / 采购订单 2 行 / 形式发票 0 行`。
 *
 * role_signature 是给调试和 DB 查询用的紧凑编码，业务员读不懂 `Q1:P2:I0`。
 * 它是 `ambiguous_match` 说明句里的 `{signature}` 参数，而 AMBIGUOUS_MATCH 是
 * CLAUDE.md 规定的主路径（多成员组一律交人工），不是边角情况。
 *
 * 形状不符合预期时**原样输出**——猜错比不翻译更糟。与后端 localize_signature 同规则。
 *
 * 「符合预期」= 与 `SIGNATURE_TAGS` 逐段同序对齐，一段不多一段不少。
 * 宽松地接受 `Q1:P2` 会把「形式发票那段丢了」渲染成「报价单 1 行 / 采购订单 2 行」，
 * 读者只会理解成形式发票不参与该组——而 AMBIGUOUS_MATCH 恰恰是最需要人工看准的场景。
 */
export function localizeSignature(text: string): string {
  const tokens = text.split(':')
  if (tokens.length !== SIGNATURE_TAGS.length) return text
  const parts: string[] = []
  // 遍历 SIGNATURE_TAGS 而不是 tokens：长度上面已经卡死，这样索引 tokens 时
  // 不需要为「可能越界」再写一次防御（tsconfig 开了 noUncheckedIndexedAccess）。
  for (const [index, [tag, roleName]] of SIGNATURE_TAGS.entries()) {
    const token = tokens[index] ?? ''
    const count = token.slice(1)
    if (token.slice(0, 1) !== tag || count === '' || !/^\d+$/.test(count)) return text
    parts.push(`${ROLE_LABEL[roleName] ?? roleName} ${count} 行`)
  }
  return parts.join(' / ')
}

/** line_key -> 中文可读串。内部身份串（`sku:AB-200#1`）不该原样示人。 */
export function lineKeyLabel(key: string): string {
  if (key.startsWith('sku:')) {
    const [body, , ordinal] = rpartition(key.slice(4), '#')
    const sku = body || key.slice(4)
    return ordinal === '' || ordinal === '1' ? sku : `${sku}（第 ${ordinal} 次出现）`
  }
  if (key.startsWith('cpn:')) {
    const [body, , ordinal] = rpartition(key.slice(4), '#')
    const cpn = body || key.slice(4)
    const suffix = ordinal === '' || ordinal === '1' ? '' : `（第 ${ordinal} 次出现）`
    return `客户料号 ${cpn}${suffix}`
  }
  if (key.startsWith('pos:')) {
    const [sheet, , row] = partition(key.slice(4), '!')
    return `工作表 ${sheet} 第 ${row} 行`
  }
  return key
}

/**
 * group_key（`SKU:AB-100` / `NOSKU:PURCHASE_ORDER:16`）-> 中文可读串。
 *
 * 它会同时出现在「主体」列与说明句里，两处必须说同一句话。
 */
export function localizeGroupKey(key: string): string {
  if (key.startsWith('SKU:')) {
    // SKU 段是单据原文，**不做任何替换**；`#attr=值` 是 MVP-1 的消歧后缀
    const [sku, sep, attr] = partition(key.slice(4), '#')
    return sep ? `${sku}（${attr}）` : sku
  }
  if (key.startsWith('NOSKU:')) {
    const [roleName, , row] = partition(key.slice(6), ':')
    return `${roleLabelOf(roleName)}第 ${row} 行（未标型号）`
  }
  return localizeProse(key)
}

/** 差异表「主体」列：型号 / 单据 / 全局，一律显示成中文可读串。 */
export function subjectLabel(subjectKind: string, subjectKey: string): string {
  if (subjectKind === 'DOCUMENT_ROLE') {
    if (subjectKey === 'PROJECT') return '全部单据'
    const [head, sep, rest] = partition(subjectKey, '#')
    return sep ? `${roleLabelOf(head)}·${lineKeyLabel(rest)}` : roleLabelOf(head)
  }
  return localizeGroupKey(subjectKey)
}
