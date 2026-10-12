import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertCircle,
  CheckCircle2,
  Link2,
  LoaderCircle,
  LogIn,
  RefreshCw,
  UploadCloud,
} from 'lucide-react'
import { useRef, useState, type ChangeEvent } from 'react'

import { Button } from '../../components/ui/button'
import { Card } from '../../components/ui/card'
import { authenticateDrive, getDriveStatus, uploadDriveCredentials } from '../../lib/api'
import { errorMessage, failureMessage } from '../../lib/errors'
import { cn } from '../../lib/utils'
import { useToast } from '../../components/shared/toast'

const DRIVE_STATUS_KEY = ['drive', 'status'] as const

/**
 * Section 6.4 / API contract: connect Google Drive so approved notes can be
 * published as Google Docs. Two steps — upload `credentials.json`, then run the
 * local consent flow — with the resulting state always spelled out in words.
 */
export function GoogleAuthPanel() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const [uploadStatus, setUploadStatus] = useState<string | null>(null)
  const [uploadOk, setUploadOk] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const driveStatusQuery = useQuery({
    queryKey: DRIVE_STATUS_KEY,
    queryFn: getDriveStatus,
  })

  const uploadMutation = useMutation({
    mutationFn: uploadDriveCredentials,
    onSuccess: (result) => {
      setUploadOk(true)
      setUploadStatus(result.message || 'Credentials uploaded successfully.')
      toast('Google credentials uploaded. Next: authenticate.')
      void queryClient.invalidateQueries({ queryKey: DRIVE_STATUS_KEY })
    },
    onError: (error) => {
      setUploadOk(false)
      setUploadStatus(errorMessage(error, 'Upload failed.'))
      toast(failureMessage('Could not upload the credentials', error), 'error')
    },
  })

  const authMutation = useMutation({
    mutationFn: authenticateDrive,
    onSuccess: () => {
      toast('Google Drive connected.')
      void queryClient.invalidateQueries({ queryKey: DRIVE_STATUS_KEY })
    },
    onError: (error) => toast(failureMessage('Could not connect Google Drive', error), 'error'),
  })

  const handleFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    // Reset so choosing the same file again still fires `change`.
    event.target.value = ''
    if (!file) return
    // Google names the download client_secret_<id>.json; the backend only needs a .json file.
    if (!file.name.toLowerCase().endsWith('.json')) {
      setUploadOk(false)
      setUploadStatus('Choose the OAuth client file from Google Cloud Console (a .json file).')
      return
    }
    uploadMutation.mutate(file)
  }

  const isConfigured = driveStatusQuery.data?.configured === true
  const isBusy = driveStatusQuery.isPending

  return (
    <section id="google-auth" className="space-y-4">
      <Card className="p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h2 className="flex items-center gap-2 text-section text-text">
              <Link2 className="size-icon-lg text-accent" aria-hidden="true" />
              Google Drive integration
            </h2>
            <p className="mt-1 text-body text-text-muted">
              Connect a Google account so approved notes can be published as Google Docs.
            </p>
          </div>

          <div
            className={cn(
              'flex items-center gap-2 rounded border px-3 py-1 text-label font-medium',
              isConfigured
                ? 'border-border bg-st-success-bg text-st-success-fg'
                : 'border-border bg-st-neutral-bg text-st-neutral-fg',
            )}
          >
            {isConfigured ? (
              <CheckCircle2 className="size-icon" aria-hidden="true" />
            ) : (
              <AlertCircle className="size-icon" aria-hidden="true" />
            )}
            <span>{isConfigured ? 'Connected' : 'Not connected'}</span>
          </div>
        </div>

        {isBusy ? <p className="mt-4 text-body text-text-muted">Checking Drive status…</p> : null}

        {driveStatusQuery.isError ? (
          <p role="alert" className="mt-4 text-body text-st-error-fg">
            Could not check Google Drive: {errorMessage(driveStatusQuery.error)}
          </p>
        ) : null}

        {driveStatusQuery.isSuccess ? (
          <p className="mt-4 text-body text-text-muted">{driveStatusQuery.data.message}</p>
        ) : null}

        <div className="mt-5 grid gap-4 md:grid-cols-2">
          <div className="rounded border border-border bg-surface-muted p-4">
            <h3 className="text-body font-medium text-text">Step 1: Upload credentials</h3>
            <p className="mt-1 text-label text-text-muted">
              Upload the OAuth client JSON file (for example{' '}
              <code className="font-mono text-code">client_secret_….json</code>) from the Google
              Cloud Console.
            </p>
            <input
              ref={fileInputRef}
              type="file"
              accept=".json,application/json"
              className="hidden"
              onChange={handleFileChange}
            />
            <Button
              variant="secondary"
              className="mt-3 w-full justify-start"
              onClick={() => fileInputRef.current?.click()}
              disabled={uploadMutation.isPending}
            >
              {uploadMutation.isPending ? (
                <LoaderCircle className="size-icon animate-spin" aria-hidden="true" />
              ) : (
                <UploadCloud className="size-icon" aria-hidden="true" />
              )}
              {uploadMutation.isPending ? 'Uploading…' : 'Select credentials file'}
            </Button>
            {uploadStatus ? (
              <p
                className={cn(
                  'mt-2 text-label',
                  uploadOk ? 'text-st-success-fg' : 'text-st-error-fg',
                )}
                role="status"
              >
                {uploadStatus}
              </p>
            ) : null}
          </div>

          <div className="rounded border border-border bg-surface-muted p-4">
            <h3 className="text-body font-medium text-text">Step 2: Authenticate</h3>
            <p className="mt-1 text-label text-text-muted">
              Opens the Google consent screen in your local browser and stores a token for
              publishing.
            </p>
            <Button
              className="mt-3 w-full justify-start"
              onClick={() => authMutation.mutate()}
              disabled={authMutation.isPending}
            >
              {authMutation.isPending ? (
                <LoaderCircle className="size-icon animate-spin" aria-hidden="true" />
              ) : (
                <LogIn className="size-icon" aria-hidden="true" />
              )}
              {authMutation.isPending ? 'Authenticating…' : 'Authenticate account'}
            </Button>
            {authMutation.isError ? (
              <p className="mt-2 text-label text-st-error-fg" role="alert">
                Failed to authenticate: {errorMessage(authMutation.error)}
              </p>
            ) : null}
          </div>
        </div>

        <div className="mt-4 flex items-center gap-3">
          <Button
            variant="secondary"
            onClick={() => queryClient.invalidateQueries({ queryKey: DRIVE_STATUS_KEY })}
            disabled={isBusy}
          >
            <RefreshCw className={cn('size-icon', isBusy && 'animate-spin')} aria-hidden="true" />
            Verify status
          </Button>
        </div>
      </Card>
    </section>
  )
}
