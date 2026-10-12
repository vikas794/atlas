import { QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { createBrowserRouter, RouterProvider } from 'react-router-dom'

// design.md section 4: self-hosted Inter (UI), Lexend (notes), JetBrains Mono (code).
import '@fontsource/inter/400.css'
import '@fontsource/inter/500.css'
import '@fontsource/inter/600.css'
import '@fontsource/lexend/400.css'
import '@fontsource/lexend/600.css'
import '@fontsource/jetbrains-mono/400.css'

import './index.css'
import { createQueryClient } from './lib/query-client'
import { initTheme } from './lib/theme'
import { routes } from './routes'

// Restore the saved theme preference (default: follow the OS) before React
// paints, so the app never flashes the wrong palette on load.
initTheme()

const queryClient = createQueryClient()
const router = createBrowserRouter(routes)

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
)
