import { motion } from 'framer-motion'
import { useState, useRef } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link2, AlertCircle, CheckCircle2, UploadCloud, RefreshCw, LogIn } from 'lucide-react'

import { Button } from '../../components/ui/button'
import { Card } from '../../components/ui/card'
import { getDriveStatus, uploadCredentials, authenticateDrive } from '../../lib/api'

export function GoogleAuthPanel() {
  const queryClient = useQueryClient()
  const [uploadStatus, setUploadStatus] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const driveStatusQuery = useQuery({
    queryKey: ['drive', 'status'],
    queryFn: getDriveStatus,
  })

  const uploadMutation = useMutation({
    mutationFn: (file: File) => uploadCredentials(file),
    onSuccess: () => {
      setUploadStatus('Credentials uploaded successfully.')
      void queryClient.invalidateQueries({ queryKey: ['drive', 'status'] })
    },
    onError: (error) => {
      setUploadStatus(`Upload failed: ${error instanceof Error ? error.message : 'Unknown error'}`)
    },
  })

  const authMutation = useMutation({
    mutationFn: authenticateDrive,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['drive', 'status'] })
    },
  })

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) {
      if (file.name !== 'credentials.json') {
        setUploadStatus('Please upload a file named credentials.json')
        return
      }
      uploadMutation.mutate(file)
    }
  }

  const isConfigured = driveStatusQuery.data?.configured

  return (
    <section id="google-auth">
      <motion.div
        animate={{ opacity: 1, y: 0 }}
        initial={{ opacity: 0, y: 20 }}
        transition={{ duration: 0.4 }}
      >
        <Card className="mx-auto max-w-6xl overflow-hidden border-white/8 bg-[linear-gradient(180deg,rgba(24,27,33,0.96),rgba(16,18,22,0.92))] p-6 md:p-8">
          <div className="mb-6 flex flex-wrap items-center justify-between gap-4">
            <div>
              <h3 className="text-xl font-semibold tracking-[-0.03em] text-white flex items-center gap-2">
                <Link2 className="size-5 text-blue-400" />
                Google Drive Integration
              </h3>
              <p className="mt-2 text-sm text-zinc-400">
                Connect your Google Account to automatically export quizzes to Google Docs.
              </p>
            </div>
            {isConfigured ? (
              <div className="flex items-center gap-2 rounded-xl bg-emerald-500/10 px-4 py-2 border border-emerald-500/20 text-emerald-400">
                <CheckCircle2 className="size-5" />
                <span className="text-sm font-medium">Connected</span>
              </div>
            ) : (
              <div className="flex items-center gap-2 rounded-xl bg-red-500/10 px-4 py-2 border border-red-500/20 text-red-400">
                <AlertCircle className="size-5" />
                <span className="text-sm font-medium">Not Connected</span>
              </div>
            )}
          </div>

          {!isConfigured && (
            <div className="grid gap-6 md:grid-cols-2">
              <div className="rounded-[20px] border border-white/8 bg-black/15 p-5">
                <h4 className="text-sm font-medium text-white mb-2">Step 1: Upload Credentials</h4>
                <p className="text-xs text-zinc-400 mb-4">
                  Upload your <code className="text-zinc-300">credentials.json</code> file from the Google Cloud Console.
                </p>
                <div className="flex flex-col gap-3">
                  <input
                    type="file"
                    accept=".json"
                    className="hidden"
                    ref={fileInputRef}
                    onChange={handleFileChange}
                  />
                  <Button 
                    variant="secondary" 
                    className="w-full justify-start gap-2 border-white/10"
                    onClick={() => fileInputRef.current?.click()}
                    disabled={uploadMutation.isPending}
                  >
                    {uploadMutation.isPending ? (
                      <RefreshCw className="size-4 animate-spin text-zinc-400" />
                    ) : (
                      <UploadCloud className="size-4 text-zinc-400" />
                    )}
                    Select credentials.json
                  </Button>
                  {uploadStatus && (
                    <p className={`text-xs ${uploadStatus.includes('success') ? 'text-emerald-400' : 'text-red-400'}`}>
                      {uploadStatus}
                    </p>
                  )}
                </div>
              </div>

              <div className="rounded-[20px] border border-white/8 bg-black/15 p-5">
                <h4 className="text-sm font-medium text-white mb-2">Step 2: Authenticate</h4>
                <p className="text-xs text-zinc-400 mb-4">
                  After uploading, click below to open the Google consent screen in your local browser and generate a token.
                </p>
                <Button 
                  className="w-full gap-2"
                  onClick={() => authMutation.mutate()}
                  disabled={authMutation.isPending}
                >
                  {authMutation.isPending ? (
                     <RefreshCw className="size-4 animate-spin" />
                  ) : (
                     <LogIn className="size-4" />
                  )}
                  {authMutation.isPending ? 'Authenticating...' : 'Authenticate Account'}
                </Button>
                {authMutation.isError && (
                  <p className="mt-3 text-xs text-red-400">
                    Failed to authenticate: {authMutation.error instanceof Error ? authMutation.error.message : 'Unknown error'}
                  </p>
                )}
              </div>
            </div>
          )}

          {isConfigured && (
            <div className="rounded-[20px] border border-white/8 bg-white/[0.02] p-5">
              <p className="text-sm text-zinc-300">
                Your Google Drive account is fully configured. The Quiz Pipeline will dynamically create folders and export quizzes into your Drive.
              </p>
              <div className="mt-4 flex items-center gap-3">
                <Button 
                  variant="secondary" 
                  className="gap-2 border-white/10"
                  onClick={() => queryClient.invalidateQueries({ queryKey: ['drive', 'status'] })}
                >
                  <RefreshCw className="size-4" />
                  Verify Status
                </Button>
              </div>
            </div>
          )}
        </Card>
      </motion.div>
    </section>
  )
}
