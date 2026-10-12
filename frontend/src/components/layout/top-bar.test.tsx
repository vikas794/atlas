import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'

import { TopBar } from './top-bar'

function renderBar() {
  return render(
    <MemoryRouter>
      <TopBar />
    </MemoryRouter>,
  )
}

describe('theme toggle', () => {
  it('offers System, Light and Dark and defaults to System', () => {
    renderBar()

    const group = screen.getByRole('radiogroup', { name: 'Theme' })
    expect(group).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'System' })).toBeChecked()
    expect(screen.getByRole('radio', { name: 'Light' })).not.toBeChecked()
    expect(screen.getByRole('radio', { name: 'Dark' })).not.toBeChecked()
    expect(document.documentElement.dataset.theme).toBeUndefined()
  })

  it('applies and persists each state, and System clears the override', async () => {
    const user = userEvent.setup()
    renderBar()

    await user.click(screen.getByRole('radio', { name: 'Dark' }))
    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(window.localStorage.getItem('atlas.theme')).toBe('dark')
    expect(screen.getByRole('radio', { name: 'Dark' })).toBeChecked()

    await user.click(screen.getByRole('radio', { name: 'Light' }))
    expect(document.documentElement.dataset.theme).toBe('light')
    expect(window.localStorage.getItem('atlas.theme')).toBe('light')

    await user.click(screen.getByRole('radio', { name: 'System' }))
    expect(document.documentElement.dataset.theme).toBeUndefined()
    expect(window.localStorage.getItem('atlas.theme')).toBeNull()
    expect(screen.getByRole('radio', { name: 'System' })).toBeChecked()
  })

  it('restores the persisted choice on mount', () => {
    window.localStorage.setItem('atlas.theme', 'dark')
    renderBar()

    expect(screen.getByRole('radio', { name: 'Dark' })).toBeChecked()
  })
})
