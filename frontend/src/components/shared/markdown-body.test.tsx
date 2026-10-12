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
})
