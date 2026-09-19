import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { cn } from '../../lib/utils'

export function MarkdownBody({ markdown }: { markdown: string }) {
  return (
    <div className="space-y-4 text-sm leading-7 text-zinc-300">
      <ReactMarkdown
        components={{
          a: ({ className, ...props }) => (
            <a
              className={cn('font-medium text-white underline decoration-white/20 underline-offset-4', className)}
              rel="noreferrer"
              target="_blank"
              {...props}
            />
          ),
          h1: ({ className, ...props }) => (
            <h1 className={cn('text-2xl font-semibold tracking-[-0.04em] text-white', className)} {...props} />
          ),
          h2: ({ className, ...props }) => (
            <h2 className={cn('text-xl font-semibold tracking-[-0.03em] text-white', className)} {...props} />
          ),
          h3: ({ className, ...props }) => (
            <h3 className={cn('text-base font-semibold text-white', className)} {...props} />
          ),
          p: ({ className, ...props }) => <p className={cn('text-zinc-300', className)} {...props} />,
          ul: ({ className, ...props }) => <ul className={cn('space-y-2 pl-5', className)} {...props} />,
          ol: ({ className, ...props }) => <ol className={cn('space-y-2 pl-5', className)} {...props} />,
          li: ({ className, ...props }) => <li className={cn('text-zinc-300', className)} {...props} />,
          pre: ({ className, ...props }) => (
            <pre
              className={cn(
                'overflow-x-auto rounded-2xl border border-white/8 bg-black/20 p-4 text-[13px] leading-6 text-zinc-100',
                className,
              )}
              {...props}
            />
          ),
          code: ({ className, ...props }) => (
            <code
              className={cn('rounded-md bg-white/[0.05] px-1.5 py-0.5 text-[0.92em] text-zinc-100', className)}
              {...props}
            />
          ),
        }}
        remarkPlugins={[remarkGfm]}
      >
        {markdown}
      </ReactMarkdown>
    </div>
  )
}
