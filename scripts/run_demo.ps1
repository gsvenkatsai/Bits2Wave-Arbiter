# scripts/run_demo.ps1
# PROJECT ARBITER — 3D Real-time Demo Runner
# Starts Uvicorn server, polls /health until ready, launches browser to /dashboard/3d,
# and starts the simulation engine streaming real frames over WebSockets.

param(
    [int]$Ticks = 300,
    [float]$Pace = 1.0,
    [switch]$NoBrowser,
    [switch]$Scripted,
    [switch]$MockSink,
    [int]$Cells = 16
)

$ErrorActionPreference = "Stop"

$port = 8000
$healthUrl = "http://127.0.0.1:$port/api/v1/health"
$dashboardUrl = "http://127.0.0.1:$port/dashboard/3d"

# 0. Loud warnings for non-default switches
if ($Scripted) {
    Write-Warning "================================================================================"
    Write-Warning "[NON-DEFAULT MODE] -Scripted switch is ACTIVE! Using SCRIPTED TEST TWIN!"
    Write-Warning "================================================================================"
    $twinKind = "scripted"
} else {
    $twinKind = "real"
}

if ($MockSink) {
    Write-Warning "================================================================================"
    Write-Warning "[NON-DEFAULT MODE] -MockSink switch is ACTIVE! Using MOCK PROPOSAL SINK!"
    Write-Warning "================================================================================"
    $sinkKind = "mock"
} else {
    $sinkKind = "queue"
}

# 1. Stop any previous server using the PID file safely
$pidFile = "scripts/.server_pid"
if (Test-Path $pidFile) {
    try {
        $oldPid = Get-Content $pidFile -ErrorAction SilentlyContinue
        if ($oldPid) {
            Stop-Process -Id $oldPid -Force -ErrorAction SilentlyContinue
            Write-Host "[DEMO] Safely stopped previous server (PID $oldPid)."
        }
    } catch {}
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}

# 2. Check if server is already running on port
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
    Set-Content -Path $pidFile -Value $serverProc.Id

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

# 3. Print one-line ENGINE MODE banner (reading first frame's meta if available)
$clusterSize = 5
try {
    $firstFrame = Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/v1/frame/latest" -TimeoutSec 1
    if ($firstFrame) {
        if ($firstFrame.meta) {
            if ($firstFrame.meta.twin_kind) { $twinKind = $firstFrame.meta.twin_kind }
            if ($firstFrame.meta.sink) { $sinkKind = $firstFrame.meta.sink }
            if ($firstFrame.meta.cluster_size) { $clusterSize = $firstFrame.meta.cluster_size }
        }
        if ($firstFrame.cells -and $firstFrame.cells.Count) {
            $clusterSize = $firstFrame.cells.Count
        }
    }
} catch {}

Write-Host "================================================================================"
Write-Host "[ENGINE MODE] TWIN: $twinKind | SINK: $sinkKind | CLUSTER: $clusterSize cells"
Write-Host "================================================================================"

# 4. Open 3D Dashboard in browser
if (-not $NoBrowser) {
    Write-Host "[DEMO] Opening 3D visualization at $dashboardUrl..."
    Start-Process $dashboardUrl
}

# 5. Start engine with explicit --twin and queue options
$engineArgs = @("-m", "arbiter.simulator.engine", "--scenario", "2am_collision", "--stream-ws", "--ticks", "$Ticks", "--pace", "$Pace", "--twin", "$twinKind", "--cells", "$Cells")
if (-not $MockSink) {
    $engineArgs += "--use-real-queue"
}

Write-Host "[DEMO] Starting simulation engine: python $engineArgs"
python @engineArgs
