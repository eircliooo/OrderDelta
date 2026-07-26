import '@testing-library/jest-dom/vitest'

import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

// globals: false，所以 RTL 的自动 cleanup 不会生效，必须手动挂。
afterEach(() => {
  cleanup()
})
