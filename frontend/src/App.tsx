import { Outlet } from 'react-router-dom'

import { TopBar } from './components/layout/top-bar'
import { ToastProvider } from './components/shared/toast'

/** Shared frame for every route: top bar, then the routed screen (design.md 6). */
function App() {
  return (
    <ToastProvider>
      <div className="min-h-screen w-full max-w-full overflow-x-hidden text-text">
        <TopBar />

        {/* Section 5: 1280px max width, centered, 24px side padding. */}
        <main className="mx-auto w-full max-w-page px-5 pb-32 pt-6">
          <Outlet />
        </main>
      </div>
    </ToastProvider>
  )
}

export default App
