/**
 * 界面中文单语的**机械守卫**（硬约束 #1 / SPEC §13.1）。
 *
 * 逐个组件人眼检查一遍不够：后端新增一个枚举值、或某个 explanation 参数忘了登记
 * 本地化策略，界面上就会冒出一串 `VALUE_CONFLICT` / `Q1:P2:I0`，而所有既有测试
 * 照样全绿——这正是 `signature` 参数漏登记时发生的事。
 *
 * 做法：真渲染两个路由，把可见文本整段抓下来，断言里面不含任何**英文枚举标识符**。
 *
 * 三类文本必须排除在扫描之外，它们出现英文是正确行为：
 *   ① `.code`     —— 诊断用错误码（`FORMULA_WITHOUT_CACHE`），旁边一定配了中文标签
 *   ② `.raw-text` —— 单据原文（`=SUM(F9:F11)+100`），翻译它就是篡改证据
 *   ③ `.value-text` / 文件名 —— 单据里读出来的值与原始文件名，同样是原文
 */

import { screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { App } from '../src/App'
import {
  CHAIN_STAGE_LABEL,
  DIFFERENCE_TYPE_LABEL,
  EVIDENCE_SOURCE_LABEL,
  IDENTITY_STRENGTH_LABEL,
  PARSE_STATUS_LABEL,
  PROJECT_STATUS_LABEL,
  REVIEW_STATUS_LABEL,
  ROLE_LABEL,
  SCOPE_LABEL,
  SEVERITY_LABEL,
  VALUE_SOURCE_LABEL,
} from '../src/labels/enums'
import {
  AMBIGUOUS_KEY,
  makeAmbiguousMatchDifference,
  makeDifferences,
  makeProject,
  makeTwoDocProject,
  makeUnknownExplanationDifference,
} from './fixtures'
import { installFetchMock, renderWithProviders } from './testUtils'

/** 所有「英文枚举 -> 中文」表的键：这些串一个都不该出现在可见文本里。 */
const ENUM_IDENTIFIERS: readonly string[] = [
  ...Object.keys(ROLE_LABEL),
  ...Object.keys(SEVERITY_LABEL),
  ...Object.keys(DIFFERENCE_TYPE_LABEL),
  ...Object.keys(SCOPE_LABEL),
  ...Object.keys(CHAIN_STAGE_LABEL),
  ...Object.keys(VALUE_SOURCE_LABEL),
  ...Object.keys(EVIDENCE_SOURCE_LABEL),
  ...Object.keys(PARSE_STATUS_LABEL),
  ...Object.keys(REVIEW_STATUS_LABEL),
  ...Object.keys(PROJECT_STATUS_LABEL),
  ...Object.keys(IDENTITY_STRENGTH_LABEL),
]

/** 内部身份串 / 紧凑编码：业务员读不懂，同样不得直接示人。 */
const INTERNAL_ENCODINGS: readonly RegExp[] = [
  /Q\d+:P\d+:I\d+/, // role_signature
  /SKU:/, // group_key 前缀
  /NOSKU:/,
  /\bsku:[^\s]/, // line_key 前缀
  /\bcpn:[^\s]/,
  /\bpos:[^\s]/,
]

/** 抓可见文本，剔除「英文是正确行为」的三类节点。 */
function visibleText(root: HTMLElement): string {
  const clone = root.cloneNode(true) as HTMLElement
  for (const node of clone.querySelectorAll('.code, .raw-text, .value-text, .slot-meta')) {
    node.remove()
  }
  return clone.textContent ?? ''
}

function expectNoEnglishLeftover(text: string): void {
  const leaked = ENUM_IDENTIFIERS.filter((identifier) => text.includes(identifier))
  expect(leaked).toEqual([])
  const encodings = INTERNAL_ENCODINGS.filter((pattern) => pattern.test(text))
  expect(encodings.map(String)).toEqual([])
}

describe('界面中文单语（硬约束 #1）', () => {
  it('自检：守卫真的抓得到英文枚举，不是空断言', () => {
    expect(() => expectNoEnglishLeftover('风险等级：VALUE_CONFLICT')).toThrow()
    expect(() => expectNoEnglishLeftover('成员分布 Q1:P2:I0')).toThrow()
    expect(() => expectNoEnglishLeftover('全部都是中文，没有问题')).not.toThrow()
  })

  it('差异工作台（含歧义匹配与未知模板）没有英文枚举残留', async () => {
    const items = [
      ...makeDifferences(),
      makeAmbiguousMatchDifference(),
      makeUnknownExplanationDifference(),
    ]
    installFetchMock({
      'GET /api/v1/projects/p-1': { body: makeProject() },
      'GET /api/v1/projects/p-1/differences': { body: { items, total: items.length } },
    })
    renderWithProviders(<App />, '/projects/p-1')

    await screen.findByTestId(`diff-${AMBIGUOUS_KEY}`)
    expectNoEnglishLeftover(visibleText(document.body))
  })

  it('歧义匹配的说明句读得懂：成员分布是中文，不是 Q1:P2:I0', async () => {
    const items = [makeAmbiguousMatchDifference()]
    installFetchMock({
      'GET /api/v1/projects/p-1': { body: makeProject() },
      'GET /api/v1/projects/p-1/differences': { body: { items, total: 1 } },
    })
    renderWithProviders(<App />, '/projects/p-1')

    const row = await screen.findByTestId(`diff-${AMBIGUOUS_KEY}`)
    const explain = within(row).getByText(/成员分布/)
    expect(explain).toHaveTextContent('报价单 1 行 / 采购订单 2 行 / 形式发票 0 行')
    expect(explain).toHaveTextContent('采购订单 上同一型号出现 2 行')
    expect(explain.textContent ?? '').not.toContain('Q1:P2:I0')
  })

  it('展开证据后的表格也没有英文枚举残留', async () => {
    const items = makeDifferences()
    installFetchMock({
      'GET /api/v1/projects/p-1': { body: makeProject() },
      'GET /api/v1/projects/p-1/differences': { body: { items, total: items.length } },
    })
    renderWithProviders(<App />, '/projects/p-1')

    for (const toggle of await screen.findAllByRole('button', { name: /展开证据/ })) {
      toggle.click()
    }
    expectNoEnglishLeftover(visibleText(document.body))
  })

  it('两文件场景（覆盖横幅可见）也没有英文枚举残留', async () => {
    installFetchMock({
      'GET /api/v1/projects/p-2': { body: makeTwoDocProject() },
      'GET /api/v1/projects/p-2/differences': { body: { items: [], total: 0 } },
    })
    renderWithProviders(<App />, '/projects/p-2')

    await screen.findByTestId('coverage-banner')
    expectNoEnglishLeftover(visibleText(document.body))
  })

  it('项目列表页没有英文枚举残留', async () => {
    installFetchMock({
      'GET /api/v1/projects': { body: { items: [makeProject()], total: 1 } },
    })
    renderWithProviders(<App />, '/projects')

    await screen.findByTestId('project-p-1')
    expectNoEnglishLeftover(visibleText(document.body))
  })
})
