import { GoogleAuthPanel } from './google-auth-panel'
import { UsageDashboard } from '../usage/usage-dashboard'

export function SettingsScreen() {
  return (
    <div className="space-y-5">
      <h1 className="text-page text-text">Settings</h1>
      <GoogleAuthPanel />
      <UsageDashboard />
    </div>
  )
}
