import '@testing-library/jest-dom/vitest'
import { cleanup, configure, queryAllByRole } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

// Screens fetch through several mocked requests; CI machines can be slow.
configure({ asyncUtilTimeout: 5000 })

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  document.documentElement.removeAttribute('data-theme')
  window.localStorage.clear()
})

// jsdom does not implement matchMedia; the theme module and the top-bar
// toggle both feature-detect it, so give them a controllable stub.
if (typeof window.matchMedia !== 'function') {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }),
  })
}

// Radix (dropdown menu, dialog) relies on browser APIs jsdom does not implement.
if (typeof Element.prototype.hasPointerCapture !== 'function') {
  Element.prototype.hasPointerCapture = () => false
  Element.prototype.setPointerCapture = () => {}
  Element.prototype.releasePointerCapture = () => {}
}
if (typeof Element.prototype.scrollIntoView !== 'function') {
  Element.prototype.scrollIntoView = () => {}
}
if (typeof globalThis.ResizeObserver === 'undefined') {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
}

// react-router's data router builds a native `Request` with jsdom's AbortSignal, which
// undici rejects. Drop the signal in tests; navigation does not depend on it.
const NativeRequest = globalThis.Request
globalThis.Request = class extends NativeRequest {
  constructor(input: RequestInfo | URL, init?: RequestInit) {
    if (init?.signal) {
      const { signal: _signal, ...rest } = init
      void _signal
      super(input, rest)
    } else {
      super(input, init)
    }
  }
} as typeof Request

// The first getComputedStyle / accessible-name computation in a fresh jsdom costs
// ~0.2-0.6 s (cssstyle and dom-accessibility-api initialise lazily), and many
// times that when every worker is busy. Each test file gets a fresh jsdom, so
// that cost landed inside the first `findByRole` of every file and made the
// first test (e.g. "#001 Video 1", "In progress") time out under full-suite
// load. Pay it here, during setup, instead.
{
  const warmup = document.createElement('div')
  warmup.innerHTML = '<h1>Warm up</h1><button type="button">Warm up</button>'
  document.body.append(warmup)
  queryAllByRole(warmup, 'heading', { name: 'Warm up' })
  queryAllByRole(warmup, 'button', { name: 'Warm up' })
  warmup.remove()
}
