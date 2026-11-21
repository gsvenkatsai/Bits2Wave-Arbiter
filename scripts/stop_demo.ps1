# scripts/stop_demo.ps1
# PROJECT ARBITER — Stop background demo server

$pidFile = "scripts/.server_pid"
if (Test-Path $pidFile) {
    $pidVal = Get-Content $pidFile
    try {
        Stop-Process -Id $pidVal -Force -ErrorAction SilentlyContinue
        Write-Host "[DEMO] Stopped Uvicorn server (PID $pidVal)"
    } catch {}
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
} else {
    Write-Host "[DEMO] No saved server PID found in $pidFile."
}
