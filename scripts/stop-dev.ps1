$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$runtimeRoot = Join-Path $repoRoot ".runtime"
$backendHealthUrl = "http://127.0.0.1:8000/api/health"
$backendShutdownUrl = "http://127.0.0.1:8000/api/dev/shutdown"
$backendShutdownTokenFile = Join-Path $runtimeRoot "backend-shutdown.token"

$services = @(
    @{ Port = 8000; Name = "Atlas backend"; ExpectedProcesses = @("python"); PidFile = (Join-Path $runtimeRoot "backend.pid") },
    @{ Port = 5173; Name = "Atlas frontend"; ExpectedProcesses = @("node"); PidFile = (Join-Path $runtimeRoot "frontend.pid") }
)

function Get-AtlasRootProcessId {
    param([int]$ProcessId)

    $rootProcessId = $ProcessId
    try {
        $current = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction Stop
        while ($current -and $current.ParentProcessId -gt 0) {
            $parent = Get-CimInstance Win32_Process -Filter "ProcessId = $($current.ParentProcessId)" -ErrorAction Stop
            if ($parent.CommandLine -match "uvicorn.*backend\.main:app" -or $parent.CommandLine -match "npm(\.cmd)?\s+run\s+dev") {
                return $parent.ProcessId
            }
            $current = $parent
        }
    } catch {
        # The port listener remains a safe fallback when parent process inspection is unavailable.
    }
    return $rootProcessId
}

function Stop-ProcessTree {
    param([int]$ProcessId, [string]$ServiceName)

    $rootProcessId = Get-AtlasRootProcessId -ProcessId $ProcessId
    & "$env:SystemRoot\System32\taskkill.exe" /PID $rootProcessId /T /F | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Unable to stop $ServiceName process tree (root PID $rootProcessId)."
        return $false
    }

    Write-Host "Stopped $ServiceName process tree (root PID $rootProcessId)."
    return $true
}

function Get-ListeningProcessIds {
    param([int]$Port)

    $pattern = "^\s*TCP\s+\S+:$Port\s+\S+\s+LISTENING\s+(\d+)\s*$"
    return @(
        netstat.exe -ano -p tcp |
            Select-String -Pattern $pattern |
            ForEach-Object { [int]$_.Matches[0].Groups[1].Value } |
            Sort-Object -Unique
    )
}

function Test-BackendHealth {
    try {
        Invoke-WebRequest -UseBasicParsing -Uri $backendHealthUrl -TimeoutSec 2 | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Wait-ForBackendStopped {
    param([int]$TimeoutSeconds = 10)

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (-not (Test-BackendHealth)) {
            return $true
        }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Request-BackendShutdown {
    if (-not (Test-BackendHealth) -or -not (Test-Path -LiteralPath $backendShutdownTokenFile)) {
        return $false
    }

    $token = (Get-Content -LiteralPath $backendShutdownTokenFile -Raw).Trim()
    if (-not $token) {
        return $false
    }

    try {
        Invoke-WebRequest -UseBasicParsing -Method Post -Uri $backendShutdownUrl -Headers @{ "X-Atlas-Dev-Token" = $token } -TimeoutSec 2 | Out-Null
        return Wait-ForBackendStopped
    } catch {
        return $false
    }
}

if (Test-BackendHealth) {
    Write-Host "Atlas backend health endpoint is responding; stopping it now."
    if (Request-BackendShutdown) {
        Write-Host "Atlas backend stopped through the local shutdown endpoint."
    }
} else {
    Write-Host "Atlas backend health endpoint is already unreachable."
}

foreach ($service in $services) {
    if ($service.Port -eq 8000 -and -not (Test-BackendHealth)) {
        Remove-Item -LiteralPath $service.PidFile -Force -ErrorAction SilentlyContinue
        continue
    }
    if (Test-Path -LiteralPath $service.PidFile) {
        $trackedPid = Get-Content -LiteralPath $service.PidFile -Raw | Select-Object -First 1
        $parsedPid = 0
        $listenerProcessIds = Get-ListeningProcessIds -Port $service.Port
        if ([int]::TryParse($trackedPid.Trim(), [ref]$parsedPid) -and $parsedPid -in $listenerProcessIds) {
            if (Stop-ProcessTree -ProcessId $parsedPid -ServiceName $service.Name) {
                Remove-Item -LiteralPath $service.PidFile -Force
                continue
            }
        } else {
            Write-Warning "Ignoring stale $($service.Name) PID file; it does not own port $($service.Port)."
        }
        Remove-Item -LiteralPath $service.PidFile -Force
    }

    $listenerProcessIds = Get-ListeningProcessIds -Port $service.Port

    if (-not $listenerProcessIds) {
        Write-Host "$($service.Name) is not running on port $($service.Port)."
        continue
    }

    foreach ($listenerProcessId in $listenerProcessIds) {
        Stop-ProcessTree -ProcessId $listenerProcessId -ServiceName $service.Name | Out-Null
    }
}

Start-Sleep -Milliseconds 500
$backendStopped = Wait-ForBackendStopped
if (-not $backendStopped) {
    throw "Atlas backend is still responding at $backendHealthUrl after the stop attempt."
}

Remove-Item -LiteralPath $backendShutdownTokenFile -Force -ErrorAction SilentlyContinue

$remainingPorts = 8000, 5173 | Where-Object {
    (Get-ListeningProcessIds -Port $_).Count -gt 0
}
if ($remainingPorts) {
    throw "Atlas still has listeners on port(s): $($remainingPorts -join ', '). Run this script from an elevated PowerShell window."
}

Write-Host "Atlas backend is stopped: $backendHealthUrl is unreachable."
