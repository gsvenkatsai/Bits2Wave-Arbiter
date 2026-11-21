# scripts/run_demo.ps1
# PROJECT ARBITER — 3D Real-time Demo Runner
# Starts Uvicorn server, polls /health until ready, launches browser to /dashboard/3d,
# and starts the simulation engine streaming real frames over WebSockets.

param(
    [int]$Ticks = 300,
    [float]$Pace = 1.0,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"

$port = 8000
$healthUrl = "http://127.0.0.1:$port/api/v1/health"
$dashboardUrl = "http://127.0.0.1:$port/dashboard/3d"

# 1. Check if server is already running
$serverRunning = $false
try {
    $resp = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 1
    if ($resp.status -eq "OK") {
        $serverRunning = $true
        Write-Host "[DEMO] Uvicorn server is already running on port $port."
    }
} catch {
    $serverRunning = $false
}

if (-not $serverRunning) {
    Write-Host "[DEMO] Starting Uvicorn API server on port $port..."
    $serverProc = Start-Process -FilePath "python" -ArgumentList "-m", "uvicorn", "arbiter.api.server:app", "--host", "127.0.0.1", "--port", "$port" -PassThru
    Set-Content -Path "scripts/.server_pid" -Value $serverProc.Id

    # Wait for /health probe
    $retries = 25
    $ready = $false
    while ($retries -gt 0) {
        Start-Sleep -Milliseconds 400
        try {
            $resp = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 1
            if ($resp.status -eq "OK") {
                $ready = $true
                Write-Host "[DEMO] Uvicorn server is ready and healthy!"
                break
            }
        } catch {
            $retries--
        }
    }
    if (-not $ready) {
        Write-Error "[DEMO] Uvicorn failed to start within timeout."
        exit 1
    }
}

# 2. Open 3D Dashboard in browser
if (-not $NoBrowser) {
    Write-Host "[DEMO] Opening 3D visualization at $dashboardUrl..."
    Start-Process $dashboardUrl
}

# 3. Start engine with --stream-ws
Write-Host "[DEMO] Starting simulation engine with --stream-ws --scenario 2am_collision --pace $Pace --ticks $Ticks..."
python -m arbiter.simulator.engine --scenario 2am_collision --stream-ws --ticks $Ticks --pace $Pace
