$ErrorActionPreference = "Stop"

$services = @(
    @{ Port = 8000; Name = "Atlas backend"; ExpectedProcesses = @("python") },
    @{ Port = 5173; Name = "Atlas frontend"; ExpectedProcesses = @("node") }
)

foreach ($service in $services) {
    $listeners = Get-NetTCPConnection -LocalPort $service.Port -State Listen -ErrorAction SilentlyContinue

    if (-not $listeners) {
        Write-Host "$($service.Name) is not running on port $($service.Port)."
        continue
    }

    foreach ($listener in $listeners) {
        $process = Get-Process -Id $listener.OwningProcess -ErrorAction Stop

        if ($process.ProcessName -notin $service.ExpectedProcesses) {
            Write-Warning "Port $($service.Port) is owned by '$($process.ProcessName)', not the expected Atlas process. It was left running."
            continue
        }

        Stop-Process -Id $process.Id -Force
        Write-Host "Stopped $($service.Name) (PID $($process.Id))."
    }
}
