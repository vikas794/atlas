import { AppConfigPanel } from './app-config-panel'
import { GoogleAuthPanel } from './google-auth-panel'
import { UsageDashboard } from '../usage/usage-dashboard'

export function SettingsScreen() {
  return (
    <div className="space-y-5">
      <h1 className="text-page text-text">
        <span className="text-gradient">Settings</span>
      </h1>
      <GoogleAuthPanel />
      <AppConfigPanel />
      <UsageDashboard />
    </div>
  )
}
