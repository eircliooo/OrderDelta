import { describe, expect, it } from 'vitest'

import { API_BASE, API_V1, reportUrl } from '../src/api/client'

/** 用 Vite 的 glob 原样读 src 下的源码，不引入 node 类型依赖。 */
const sources = import.meta.glob('../src/**/*.{ts,tsx,css}', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

describe('API base（硬约束 #2）', () => {
  it('base 是相对路径 /api', () => {
    expect(API_BASE).toBe('/api')
    expect(API_V1).toBe('/api/v1')
  })

  it('报告导出 URL 是相对路径', () => {
    expect(reportUrl('p-1')).toBe('/api/v1/projects/p-1/report.html')
    expect(reportUrl('a/b')).toBe('/api/v1/projects/a%2Fb/report.html')
  })

  it('src 下没有任何硬编码的后端 origin', () => {
    // 硬编码 http://localhost:8000 后再改同源代理要动每个调用点，
    // 这是 SPEC §12.1 里唯一点名「有返工代价」的一条，机械守住。
    expect(Object.keys(sources).length).toBeGreaterThan(5)
    const offenders = Object.entries(sources)
      .filter(([, text]) => /https?:\/\/(localhost|127\.0\.0\.1)/.test(text))
      .map(([path]) => path)
    expect(offenders).toEqual([])
  })
})
