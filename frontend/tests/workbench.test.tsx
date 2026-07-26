import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { App } from '../src/App'
import {
  CRITICAL_KEY,
  REVIEW_KEY,
  UNKNOWN_EXPLANATION_KEY,
  WARNING_KEY,
  makeDifferences,
  makeProject,
  makeTwoDocProject,
  makeUnknownExplanationDifference,
} from './fixtures'
import { installFetchMock, renderWithProviders } from './testUtils'

const PROJECT_URL = '/api/v1/projects/p-1'
const DIFF_URL = '/api/v1/projects/p-1/differences'

function mountWorkbench(options: { withUnknownKey?: boolean } = {}) {
  const items = options.withUnknownKey
    ? [...makeDifferences(), makeUnknownExplanationDifference()]
    : makeDifferences()

  const { calls } = installFetchMock({
    [`GET ${PROJECT_URL}`]: { body: makeProject() },
    [`GET ${DIFF_URL}`]: { body: { items, total: items.length } },
    [`PUT /api/v1/projects/p-1/reviews/${CRITICAL_KEY}`]: (call) => ({
      body: {
        ...items[0],
        review_status: (call.body as { review_status: string }).review_status,
        review_note: (call.body as { review_note: string | null }).review_note,
      },
    }),
  })

  renderWithProviders(<App />, '/projects/p-1')
  return { calls }
}

describe('差异工作台', () => {
  it('免责声明常驻可见（硬约束 #5）', async () => {
    mountWorkbench()
    expect(
      await screen.findByText(/本工具只能辅助核对，不判断哪份文件正确/),
    ).toBeInTheDocument()
  })

  it('报告导出入口存在且指向 /api 相对路径', async () => {
    mountWorkbench()
    const link = await screen.findByTestId('export-report')
    expect(link).toHaveAttribute('href', '/api/v1/projects/p-1/report.html')
    expect(link).toHaveTextContent('导出报告')
  })

  it('三条差异按严重度从重到轻排列，最严重的在最前', async () => {
    mountWorkbench()
    await screen.findByTestId(`diff-${CRITICAL_KEY}`)
    const groups = document.querySelectorAll('.diff-group')
    expect(groups).toHaveLength(3)
    expect(groups[0]).toHaveAttribute('data-testid', `diff-${CRITICAL_KEY}`)
    expect(groups[1]).toHaveAttribute('data-testid', `diff-${WARNING_KEY}`)
    expect(groups[2]).toHaveAttribute('data-testid', `diff-${REVIEW_KEY}`)
  })

  it('点击 CRITICAL 芯片后只剩 CRITICAL 差异', async () => {
    const user = userEvent.setup()
    mountWorkbench()

    await screen.findByTestId(`diff-${CRITICAL_KEY}`)
    expect(screen.getByTestId(`diff-${WARNING_KEY}`)).toBeInTheDocument()
    expect(screen.getByTestId(`diff-${REVIEW_KEY}`)).toBeInTheDocument()

    await user.click(screen.getByTestId('chip-CRITICAL'))

    expect(screen.getByTestId(`diff-${CRITICAL_KEY}`)).toBeInTheDocument()
    expect(screen.queryByTestId(`diff-${WARNING_KEY}`)).not.toBeInTheDocument()
    expect(screen.queryByTestId(`diff-${REVIEW_KEY}`)).not.toBeInTheDocument()
    expect(screen.getByText('差异清单（显示 1 / 3 条）')).toBeInTheDocument()
    expect(screen.getByTestId('chip-CRITICAL')).toHaveAttribute('aria-pressed', 'true')
  })

  it('再次点击同一芯片取消筛选', async () => {
    const user = userEvent.setup()
    mountWorkbench()
    await screen.findByTestId(`diff-${CRITICAL_KEY}`)

    await user.click(screen.getByTestId('chip-CRITICAL'))
    expect(screen.queryByTestId(`diff-${WARNING_KEY}`)).not.toBeInTheDocument()

    await user.click(screen.getByTestId('chip-CRITICAL'))
    expect(screen.getByTestId(`diff-${WARNING_KEY}`)).toBeInTheDocument()
  })

  it('SKU 关键词筛选只保留匹配的行', async () => {
    const user = userEvent.setup()
    mountWorkbench()
    await screen.findByTestId(`diff-${CRITICAL_KEY}`)

    await user.type(screen.getByRole('searchbox', { name: /SKU 关键词/ }), 'ab-100')

    expect(screen.getByTestId(`diff-${WARNING_KEY}`)).toBeInTheDocument()
    expect(screen.queryByTestId(`diff-${CRITICAL_KEY}`)).not.toBeInTheDocument()
  })

  it('按文档角色筛选只保留该角色有取值的差异', async () => {
    const user = userEvent.setup()
    mountWorkbench()
    await screen.findByTestId(`diff-${CRITICAL_KEY}`)

    await user.selectOptions(screen.getByRole('combobox', { name: /文档角色/ }), 'QUOTATION')

    expect(screen.getByTestId(`diff-${WARNING_KEY}`)).toBeInTheDocument()
    expect(screen.queryByTestId(`diff-${CRITICAL_KEY}`)).not.toBeInTheDocument()
    expect(screen.queryByTestId(`diff-${REVIEW_KEY}`)).not.toBeInTheDocument()
  })

  it('证据默认收起，展开后能看到单元格地址与原文', async () => {
    const user = userEvent.setup()
    mountWorkbench()
    await screen.findByTestId(`diff-${CRITICAL_KEY}`)

    expect(screen.queryByTestId(`evidence-panel-${CRITICAL_KEY}`)).not.toBeInTheDocument()
    expect(screen.queryByText('E12')).not.toBeInTheDocument()

    await user.click(screen.getByTestId(`evidence-toggle-${CRITICAL_KEY}`))

    const panel = screen.getByTestId(`evidence-panel-${CRITICAL_KEY}`)
    expect(within(panel).getByText('E12')).toBeInTheDocument()
    expect(within(panel).getByText('E9')).toBeInTheDocument()
    expect(within(panel).getByText('purchase_order.xlsx')).toBeInTheDocument()
    expect(within(panel).getByText('PROFORMA INVOICE')).toBeInTheDocument()
    // 原文（未标准化）必须看得见
    expect(within(panel).getAllByText('2.40').length).toBeGreaterThan(0)
  })

  it('公式原文照原样显示，不被当成计算结果', async () => {
    const user = userEvent.setup()
    mountWorkbench()
    await screen.findByTestId(`diff-${REVIEW_KEY}`)

    await user.click(screen.getByTestId(`evidence-toggle-${REVIEW_KEY}`))
    const panel = screen.getByTestId(`evidence-panel-${REVIEW_KEY}`)
    expect(within(panel).getByText('=SUM(F9:F11)+100')).toBeInTheDocument()
  })

  it('主体列显示中文可读串，不露出内部身份串', async () => {
    mountWorkbench()
    const row = await screen.findByTestId(`diff-${REVIEW_KEY}`)
    const subject = row.querySelector('.subject')
    expect(subject).toHaveTextContent('形式发票')
    // 原始身份串留在 title 里备查，不占用视线
    expect(subject).toHaveAttribute('title', 'PROFORMA_INVOICE')
  })

  it('人工修正的值标「人工修正」并显示机器原读数（硬约束 #7）', async () => {
    mountWorkbench()
    const row = await screen.findByTestId(`diff-${WARNING_KEY}`)

    const poValue = within(row).getByTestId('value-PURCHASE_ORDER')
    expect(within(poValue).getByText('人工修正')).toBeInTheDocument()
    expect(within(poValue).getByText('机器原读数：1200')).toBeInTheDocument()
    expect(within(poValue).getByText('修正理由：客户改单')).toBeInTheDocument()
    expect(within(poValue).getByText('1300')).toBeInTheDocument()
  })

  it('NEEDS_CONFIRMATION 且有 stale_premise 时显示「你上次是基于 X 判断的」', async () => {
    mountWorkbench()
    const stale = await screen.findByTestId(`stale-${WARNING_KEY}`)
    expect(stale).toHaveTextContent('你上次是基于 报价单 1000、采购订单 1200 判断的')
  })

  it('没有 stale_premise 的差异不显示旧前提提示', async () => {
    mountWorkbench()
    await screen.findByTestId(`diff-${CRITICAL_KEY}`)
    expect(screen.queryByTestId(`stale-${CRITICAL_KEY}`)).not.toBeInTheDocument()
  })

  it('修改审核状态与备注后发出正确的 PUT 请求', async () => {
    const user = userEvent.setup()
    const { calls } = mountWorkbench()
    await screen.findByTestId(`diff-${CRITICAL_KEY}`)

    await user.selectOptions(
      screen.getByTestId(`review-status-${CRITICAL_KEY}`),
      'CONFIRMED_DIFFERENCE',
    )
    await user.type(screen.getByTestId(`review-note-${CRITICAL_KEY}`), '已与工厂核实')
    await user.click(screen.getByTestId(`review-save-${CRITICAL_KEY}`))

    await waitFor(() => {
      expect(calls.some((call) => call.method === 'PUT')).toBe(true)
    })
    const put = calls.find((call) => call.method === 'PUT')
    expect(put?.url).toBe(`/api/v1/projects/p-1/reviews/${CRITICAL_KEY}`)
    expect(put?.body).toEqual({
      review_status: 'CONFIRMED_DIFFERENCE',
      review_note: '已与工厂核实',
    })
  })

  it('未改动时保存按钮禁用，避免误发空 PUT', async () => {
    mountWorkbench()
    await screen.findByTestId(`diff-${CRITICAL_KEY}`)
    expect(screen.getByTestId(`review-save-${CRITICAL_KEY}`)).toBeDisabled()
  })

  it('未知 explanation_key 不白屏，差异照常出现且给出兜底文案（硬约束 #6）', async () => {
    mountWorkbench({ withUnknownKey: true })

    const row = await screen.findByTestId(`diff-${UNKNOWN_EXPLANATION_KEY}`)
    expect(within(row).getByText(/未登记的说明模板/)).toBeInTheDocument()
    expect(within(row).getByText(/brand_new_key_from_the_future/)).toBeInTheDocument()
    // 其余三条不受影响
    expect(screen.getByTestId(`diff-${CRITICAL_KEY}`)).toBeInTheDocument()
    expect(screen.getByText('差异清单（显示 4 / 4 条）')).toBeInTheDocument()
  })

  it('严重度同时有文字标签和形状记号，不只靠颜色', async () => {
    mountWorkbench()
    const row = await screen.findByTestId(`diff-${CRITICAL_KEY}`)
    const badge = row.querySelector('.sev')
    expect(badge).not.toBeNull()
    expect(badge).toHaveTextContent('严重')
    expect(badge?.querySelector('.sev-mark')?.textContent).toBe('▲')
  })
})

describe('覆盖横幅（硬约束 #4）', () => {
  it('三份齐全时不显示横幅', async () => {
    mountWorkbench()
    await screen.findByTestId(`diff-${CRITICAL_KEY}`)
    expect(screen.queryByTestId('coverage-banner')).not.toBeInTheDocument()
  })

  it('两文件场景渲染出覆盖横幅并列出缺席角色', async () => {
    installFetchMock({
      'GET /api/v1/projects/p-2': { body: makeTwoDocProject() },
      'GET /api/v1/projects/p-2/differences': { body: { items: [], total: 0 } },
    })
    renderWithProviders(<App />, '/projects/p-2')

    const banner = await screen.findByTestId('coverage-banner')
    expect(banner).toHaveTextContent('缺席角色 = 未检查，不等于无差异')
    expect(banner).toHaveTextContent('缺席角色：报价单')
    expect(banner).toHaveTextContent('采购订单、形式发票')
    expect(banner).toHaveAttribute('role', 'alert')
  })

  it('两文件场景下「无差异」也不会读成「都没问题」', async () => {
    installFetchMock({
      'GET /api/v1/projects/p-2': { body: makeTwoDocProject() },
      'GET /api/v1/projects/p-2/differences': { body: { items: [], total: 0 } },
    })
    renderWithProviders(<App />, '/projects/p-2')

    expect(await screen.findByTestId('differences-empty')).toHaveTextContent(
      '没有差异不等于没有问题',
    )
    expect(screen.getByTestId('coverage-banner')).toBeInTheDocument()
  })
})
