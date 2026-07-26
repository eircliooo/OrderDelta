import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { App } from '../src/App'
import { makeDocument, makeProject } from './fixtures'
import { installFetchMock, renderWithProviders, xlsxFile } from './testUtils'

const PROJECT_URL = '/api/v1/projects/p-1'
const DIFF_URL = '/api/v1/projects/p-1/differences'
const UPLOAD_URL = '/api/v1/projects/p-1/documents'

describe('上传槽位', () => {
  it('上传成功后显示文件名、大小与解析状态', async () => {
    const user = userEvent.setup()
    let project = makeProject({
      status: 'DRAFT',
      compared_at: null,
      documents: [makeDocument({ id: 'doc-po', role: 'PURCHASE_ORDER' })],
      compared_roles: ['PURCHASE_ORDER'],
      skipped_roles: ['PROFORMA_INVOICE', 'QUOTATION'],
    })

    const { calls } = installFetchMock({
      [`GET ${PROJECT_URL}`]: () => ({ body: project }),
      [`GET ${DIFF_URL}`]: { body: { items: [], total: 0 } },
      [`POST ${UPLOAD_URL}`]: () => {
        project = {
          ...project,
          documents: [
            ...project.documents,
            makeDocument({
              id: 'doc-pi',
              role: 'PROFORMA_INVOICE',
              original_filename: 'proforma_invoice.xlsx',
              file_size: 20480,
            }),
          ],
          compared_roles: ['PROFORMA_INVOICE', 'PURCHASE_ORDER'],
          skipped_roles: ['QUOTATION'],
        }
        return { status: 201, body: project.documents[project.documents.length - 1] }
      },
    })

    renderWithProviders(<App />, '/projects/p-1')

    const input = await screen.findByTestId('upload-input-PROFORMA_INVOICE')
    await user.upload(input, xlsxFile('proforma_invoice.xlsx'))

    expect(await screen.findByTestId('filename-PROFORMA_INVOICE')).toHaveTextContent(
      'proforma_invoice.xlsx',
    )
    const slot = screen.getByTestId('slot-PROFORMA_INVOICE')
    expect(within(slot).getByText('解析成功')).toBeInTheDocument()
    expect(within(slot).getByText('20.0 KB')).toBeInTheDocument()

    const uploadCall = calls.find((call) => call.method === 'POST')
    expect(uploadCall?.url).toBe(UPLOAD_URL)
    expect(uploadCall?.body).toBeInstanceOf(FormData)
    expect((uploadCall?.body as FormData).get('role')).toBe('PROFORMA_INVOICE')
  })

  it('解析失败时显示中文原因、错误码与明细', async () => {
    const project = makeProject({
      documents: [
        makeDocument({
          id: 'doc-q',
          role: 'QUOTATION',
          original_filename: 'quotation.xlsx',
          parse_status: 'FAILED',
          parse_reason_code: 'FORMULA_WITHOUT_CACHE',
          parse_detail: 'Sheet1!F12 是公式但没有缓存值，请在 Excel 中保存一次后重传',
        }),
        makeDocument({ id: 'doc-po', role: 'PURCHASE_ORDER' }),
        makeDocument({ id: 'doc-pi', role: 'PROFORMA_INVOICE' }),
      ],
      compared_roles: ['PROFORMA_INVOICE', 'PURCHASE_ORDER'],
      skipped_roles: ['QUOTATION'],
    })

    installFetchMock({
      [`GET ${PROJECT_URL}`]: { body: project },
      [`GET ${DIFF_URL}`]: { body: { items: [], total: 0 } },
    })

    renderWithProviders(<App />, '/projects/p-1')

    const slot = await screen.findByTestId('slot-QUOTATION')
    expect(within(slot).getByText('解析失败')).toBeInTheDocument()
    expect(within(slot).getByText('公式没有缓存值')).toBeInTheDocument()
    expect(within(slot).getByText('FORMULA_WITHOUT_CACHE')).toBeInTheDocument()
    expect(
      within(slot).getByText(/Sheet1!F12 是公式但没有缓存值/),
    ).toBeInTheDocument()
    // 失败提示必须是 alert，不能只是灰字
    expect(within(slot).getAllByRole('alert').length).toBeGreaterThan(0)
  })

  it('上传被后端拒绝时显示 error_code 与中文消息', async () => {
    const user = userEvent.setup()
    const project = makeProject({
      documents: [makeDocument({ id: 'doc-po', role: 'PURCHASE_ORDER' })],
      compared_roles: ['PURCHASE_ORDER'],
      skipped_roles: ['PROFORMA_INVOICE', 'QUOTATION'],
    })

    installFetchMock({
      [`GET ${PROJECT_URL}`]: { body: project },
      [`GET ${DIFF_URL}`]: { body: { items: [], total: 0 } },
      [`POST ${UPLOAD_URL}`]: {
        status: 400,
        body: { error_code: 'UNSUPPORTED_EXT', message: '不支持 PDF，请上传 .xlsx 文件' },
      },
    })

    renderWithProviders(<App />, '/projects/p-1')

    const input = await screen.findByTestId('upload-input-QUOTATION')
    await user.upload(input, xlsxFile('scan.xlsx'))

    const slot = screen.getByTestId('slot-QUOTATION')
    expect(await within(slot).findByText('UNSUPPORTED_EXT')).toBeInTheDocument()
    expect(within(slot).getByText(/不支持 PDF/)).toBeInTheDocument()
  })

  it('报价单标为可选，另外两个槽位不标', async () => {
    installFetchMock({
      [`GET ${PROJECT_URL}`]: { body: makeProject() },
      [`GET ${DIFF_URL}`]: { body: { items: [], total: 0 } },
    })
    renderWithProviders(<App />, '/projects/p-1')

    const quotation = await screen.findByTestId('slot-QUOTATION')
    expect(within(quotation).getByText('可选')).toBeInTheDocument()
    expect(
      within(screen.getByTestId('slot-PURCHASE_ORDER')).queryByText('可选'),
    ).not.toBeInTheDocument()
  })
})

describe('运行检查按钮（|R| >= 2，不是 3）', () => {
  it('只有 1 份可解析文档时禁用', async () => {
    installFetchMock({
      [`GET ${PROJECT_URL}`]: {
        body: makeProject({
          documents: [makeDocument({ id: 'doc-po', role: 'PURCHASE_ORDER' })],
          compared_roles: ['PURCHASE_ORDER'],
          skipped_roles: ['PROFORMA_INVOICE', 'QUOTATION'],
        }),
      },
      [`GET ${DIFF_URL}`]: { body: { items: [], total: 0 } },
    })
    renderWithProviders(<App />, '/projects/p-1')
    expect(await screen.findByTestId('run-compare')).toBeDisabled()
  })

  it('两份可解析文档即可点击，并发出 compare 请求', async () => {
    const user = userEvent.setup()
    const project = makeProject({
      documents: [
        makeDocument({ id: 'doc-po', role: 'PURCHASE_ORDER' }),
        makeDocument({ id: 'doc-pi', role: 'PROFORMA_INVOICE' }),
      ],
      compared_roles: ['PROFORMA_INVOICE', 'PURCHASE_ORDER'],
      skipped_roles: ['QUOTATION'],
    })

    const { calls } = installFetchMock({
      [`GET ${PROJECT_URL}`]: { body: project },
      [`GET ${DIFF_URL}`]: { body: { items: [], total: 0 } },
      [`POST /api/v1/projects/p-1/compare`]: { body: project },
    })

    renderWithProviders(<App />, '/projects/p-1')

    const button = await screen.findByTestId('run-compare')
    expect(button).toBeEnabled()
    await user.click(button)

    await waitFor(() => {
      expect(
        calls.some(
          (call) => call.method === 'POST' && call.url === '/api/v1/projects/p-1/compare',
        ),
      ).toBe(true)
    })
  })

  it('解析失败的文档不计入可用份数', async () => {
    installFetchMock({
      [`GET ${PROJECT_URL}`]: {
        body: makeProject({
          documents: [
            makeDocument({ id: 'doc-po', role: 'PURCHASE_ORDER' }),
            makeDocument({
              id: 'doc-pi',
              role: 'PROFORMA_INVOICE',
              parse_status: 'FAILED',
              parse_reason_code: 'CORRUPT',
            }),
          ],
          compared_roles: ['PURCHASE_ORDER'],
          skipped_roles: ['PROFORMA_INVOICE', 'QUOTATION'],
        }),
      },
      [`GET ${DIFF_URL}`]: { body: { items: [], total: 0 } },
    })
    renderWithProviders(<App />, '/projects/p-1')
    expect(await screen.findByTestId('run-compare')).toBeDisabled()
  })
})
