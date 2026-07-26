/**
 * 测试脚手架：Provider 包装 + 手写 fetch mock。
 *
 * 不依赖真实后端，也不引入 msw —— 我们只需要断言「发出了哪个请求、带什么 body」，
 * 手写 mock 足够，且能把请求记录直接摊在断言里。
 */

import type { ReactElement } from 'react'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import type { RenderResult } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { vi } from 'vitest'

export function renderWithProviders(ui: ReactElement, route = '/'): RenderResult {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
      mutations: { retry: false },
    },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[route]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  )
}

export interface RecordedCall {
  method: string
  url: string
  /** JSON 请求体解析后的对象；FormData 请求体为 FormData 本身。 */
  body: unknown
}

export interface MockResponse {
  status?: number
  body?: unknown
}

export type Responder = MockResponse | ((call: RecordedCall) => MockResponse)

/**
 * 路由表的键是 `"METHOD /api/v1/..."`。未登记的请求直接抛错，
 * 让「悄悄多打了一个接口」在测试里立刻可见。
 */
export function installFetchMock(routes: Record<string, Responder>): {
  calls: RecordedCall[]
} {
  const calls: RecordedCall[] = []

  const mock = vi.fn((input: unknown, init?: RequestInit) => {
    const url = String(input)
    const method = (init?.method ?? 'GET').toUpperCase()

    let body: unknown = null
    if (init?.body instanceof FormData) {
      body = init.body
    } else if (typeof init?.body === 'string') {
      body = JSON.parse(init.body) as unknown
    }

    const call: RecordedCall = { method, url, body }
    calls.push(call)

    const responder = routes[`${method} ${url}`]
    if (responder === undefined) {
      return Promise.reject(new Error(`测试里没有登记这个请求：${method} ${url}`))
    }

    const result = typeof responder === 'function' ? responder(call) : responder
    const status = result.status ?? 200
    return Promise.resolve({
      ok: status >= 200 && status < 300,
      status,
      json: () => Promise.resolve(result.body),
    })
  })

  vi.stubGlobal('fetch', mock)
  return { calls }
}

export function xlsxFile(name = 'purchase_order.xlsx'): File {
  return new File([new Uint8Array([0x50, 0x4b, 0x03, 0x04])], name, {
    type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  })
}
