import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { App } from '../src/App'
import { makeProject } from './fixtures'
import { installFetchMock, renderWithProviders } from './testUtils'

const LIST_URL = '/api/v1/projects'

describe('项目列表', () => {
  it('显示名称、状态、创建时间、各严重度计数与待处理数', async () => {
    installFetchMock({
      [`GET ${LIST_URL}`]: {
        body: { items: [makeProject()], total: 1 },
      },
    })
    renderWithProviders(<App />, '/projects')

    const row = await screen.findByTestId('project-p-1')
    expect(within(row).getByRole('link', { name: '2026-07 陶瓷杯订单' })).toHaveAttribute(
      'href',
      '/projects/p-1',
    )
    expect(within(row).getByText('已检查')).toBeInTheDocument()
    expect(within(row).getByText('2026-07-20 09:00:00')).toBeInTheDocument()
    // 严重 1 / 警告 1 / 待复核 1 / 提示 0 / 待处理 2
    expect(within(row).getByText('2')).toBeInTheDocument()
    expect(within(row).getAllByText('1')).toHaveLength(3)
    expect(within(row).getByText('0')).toBeInTheDocument()
  })

  it('创建项目发出 POST 并带上名称', async () => {
    const user = userEvent.setup()
    const { calls } = installFetchMock({
      [`GET ${LIST_URL}`]: { body: { items: [], total: 0 } },
      [`POST ${LIST_URL}`]: { status: 201, body: makeProject({ name: '新项目' }) },
    })
    renderWithProviders(<App />, '/projects')

    await screen.findByText('还没有项目。先创建一个，再上传单据。')
    await user.type(screen.getByRole('textbox', { name: /项目名称/ }), '新项目')
    await user.click(screen.getByRole('button', { name: '创建项目' }))

    await waitFor(() => {
      expect(calls.some((call) => call.method === 'POST')).toBe(true)
    })
    expect(calls.find((call) => call.method === 'POST')?.body).toEqual({ name: '新项目' })
  })

  it('名称为空时不能创建', async () => {
    installFetchMock({ [`GET ${LIST_URL}`]: { body: { items: [], total: 0 } } })
    renderWithProviders(<App />, '/projects')
    expect(await screen.findByRole('button', { name: '创建项目' })).toBeDisabled()
  })

  it('删除需要二次确认，且措辞只说删库删盘（不得写「安全擦除」「不可恢复」）', async () => {
    const user = userEvent.setup()
    const { calls } = installFetchMock({
      [`GET ${LIST_URL}`]: { body: { items: [makeProject()], total: 1 } },
      'DELETE /api/v1/projects/p-1': { status: 204, body: null },
    })
    renderWithProviders(<App />, '/projects')

    const row = await screen.findByTestId('project-p-1')
    await user.click(within(row).getByRole('button', { name: '删除' }))

    const confirm = screen.getByTestId('confirm-p-1')
    expect(confirm).toHaveTextContent('将删除数据库记录与磁盘上的原始文件。')
    expect(confirm.textContent ?? '').not.toMatch(/安全擦除|不可恢复|无法恢复/)

    // 只点「删除」不会真删
    expect(calls.some((call) => call.method === 'DELETE')).toBe(false)

    await user.click(within(confirm).getByRole('button', { name: '确认删除' }))
    await waitFor(() => {
      expect(
        calls.some(
          (call) => call.method === 'DELETE' && call.url === '/api/v1/projects/p-1',
        ),
      ).toBe(true)
    })
  })

  it('二次确认可以取消', async () => {
    const user = userEvent.setup()
    const { calls } = installFetchMock({
      [`GET ${LIST_URL}`]: { body: { items: [makeProject()], total: 1 } },
    })
    renderWithProviders(<App />, '/projects')

    const row = await screen.findByTestId('project-p-1')
    await user.click(within(row).getByRole('button', { name: '删除' }))
    await user.click(screen.getByRole('button', { name: '取消' }))

    expect(screen.queryByTestId('confirm-p-1')).not.toBeInTheDocument()
    expect(calls.some((call) => call.method === 'DELETE')).toBe(false)
  })

  it('列表页也常驻显示免责声明', async () => {
    installFetchMock({ [`GET ${LIST_URL}`]: { body: { items: [], total: 0 } } })
    renderWithProviders(<App />, '/projects')
    expect(
      await screen.findByText(/本工具只能辅助核对，不判断哪份文件正确/),
    ).toBeInTheDocument()
  })
})
