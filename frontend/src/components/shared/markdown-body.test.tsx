import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { MarkdownBody } from './markdown-body'

describe('MarkdownBody', () => {
  it('labels fenced code blocks with the fence tag in the top-right corner', () => {
    const { container } = render(<MarkdownBody markdown={'```python\nprint("hi")\n```'} />)

    expect(screen.getByText('python')).toBeInTheDocument()

    const pre = container.querySelector('pre')
    expect(pre?.className).toContain('bg-surface-muted')
    expect(pre?.className).toContain('border-border')
    expect(pre?.className).toContain('overflow-x-auto')
    expect(pre?.className).toContain('font-mono')
  })

  it('renders notes content in the Lexend reading face', () => {
    const { container } = render(<MarkdownBody markdown="A paragraph of notes." />)

    expect(container.firstElementChild?.className).toContain('font-notes')
    expect(container.firstElementChild?.className).toContain('text-notes')
  })

  describe('AI output is untrusted (XSS)', () => {
    const PAYLOAD = [
      '# Notes',
      '<script>window.__xss = 1</script>',
      '<img src="x" onerror="window.__xss = 2">',
      '<iframe src="https://evil.example"></iframe>',
      '<a href="javascript:window.__xss=3">raw html link</a>',
      '[md link](javascript:window.__xss=4)',
      '[mixed case](JaVaScRiPt:window.__xss=5)',
      '[data url](data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==)',
      '![img](javascript:window.__xss=6)',
      '<div style="background:url(javascript:alert(1))" onclick="alert(1)">styled</div>',
      '[safe](https://example.com/docs)',
    ].join('\n\n')

    it('renders raw HTML as inert text, never as elements', () => {
      const { container } = render(<MarkdownBody markdown={PAYLOAD} />)

      for (const tag of ['script', 'iframe', 'img[onerror]', 'div[onclick]', '[style]']) {
        expect(container.querySelector(tag)).toBeNull()
      }
      const handlers = Array.from(container.querySelectorAll('*')).flatMap((el) =>
        Array.from(el.attributes).filter((attr) => attr.name.startsWith('on')),
      )
      expect(handlers).toEqual([])
      expect((window as { __xss?: number }).__xss).toBeUndefined()
    })

    it('strips javascript: and data: URLs from links and images', () => {
      const { container } = render(<MarkdownBody markdown={PAYLOAD} />)

      const urls = [
        ...Array.from(container.querySelectorAll('a')).map((a) => a.getAttribute('href') ?? ''),
        ...Array.from(container.querySelectorAll('img')).map(
          (img) => img.getAttribute('src') ?? '',
        ),
      ]
      for (const url of urls) expect(url).not.toMatch(/^\s*(javascript|data|vbscript):/i)
      expect(screen.getByRole('link', { name: 'safe' })).toHaveAttribute(
        'href',
        'https://example.com/docs',
      )
    })

    it('opens links in a new tab without handing over window.opener', () => {
      render(<MarkdownBody markdown="[safe](https://example.com/docs)" />)

      const link = screen.getByRole('link', { name: 'safe' })
      expect(link).toHaveAttribute('target', '_blank')
      expect(link.getAttribute('rel')).toContain('noopener')
      expect(link.getAttribute('rel')).toContain('noreferrer')
    })
  })
})
