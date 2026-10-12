/* eslint-disable react-refresh/only-export-components */
import { createContext, useContext, useState, useCallback, useMemo, type ReactNode } from 'react'
import { AlertCircle, CheckCircle2, Info, X } from 'lucide-react'
import { cn } from '../../lib/utils'

export type ToastType = 'success' | 'error' | 'info'

export interface Toast {
  id: string
  message: string
  type: ToastType
}

interface ToastContextValue {
  toasts: Toast[]
  toast: (message: string, type?: ToastType) => void
  removeToast: (id: string) => void
}

const ToastContext = createContext<ToastContextValue | null>(null)

/** design.md 6.4: success and info auto-dismiss after 4s; errors stay until dismissed. */
export const TOAST_DISMISS_MS = 4000

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])

  const removeToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id))
  }, [])

  const toast = useCallback(
    (message: string, type: ToastType = 'success') => {
      const id = `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`
      setToasts((prev) => [...prev, { id, message, type }])

      if (type !== 'error') {
        setTimeout(() => {
          removeToast(id)
        }, TOAST_DISMISS_MS)
      }
    },
    [removeToast],
  )

  // Stable identity so screens can depend on `toast` without re-running effects.
  const value = useMemo(() => ({ toasts, toast, removeToast }), [toasts, toast, removeToast])

  return (
    <ToastContext.Provider value={value}>
      {children}
      {/* Toast container: bottom-right */}
      <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex max-w-sm flex-col gap-2">
        {toasts.map((t) => (
          <div
            key={t.id}
            // Errors interrupt (alert); success and info are polite status updates.
            role={t.type === 'error' ? 'alert' : 'status'}
            className={cn(
              'pointer-events-auto flex items-start gap-3 rounded border p-3 shadow-popover',
              t.type === 'error' && 'border-st-error-fg/40 bg-st-error-bg text-st-error-fg',
              t.type === 'success' && 'border-st-success-fg/40 bg-st-success-bg text-st-success-fg',
              t.type === 'info' && 'border-border bg-surface text-text',
            )}
          >
            {t.type === 'error' && <AlertCircle aria-hidden="true" className="size-icon-lg shrink-0" />}
            {t.type === 'success' && <CheckCircle2 aria-hidden="true" className="size-icon-lg shrink-0" />}
            {t.type === 'info' && <Info aria-hidden="true" className="size-icon-lg shrink-0 text-accent" />}
            <p className="flex-1 text-body font-medium">{t.message}</p>
            <button
              type="button"
              onClick={() => removeToast(t.id)}
              className="shrink-0 rounded opacity-70 transition hover:opacity-100"
              aria-label="Dismiss notification"
            >
              <X aria-hidden="true" className="size-icon" />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

export function useToast() {
  const ctx = useContext(ToastContext)
  if (!ctx) {
    throw new Error('useToast must be used within a ToastProvider')
  }
  return ctx
}
