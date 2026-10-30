# Stop CompetePulse Backend (port 8792) and Frontend (ports 5174 and 5173) processes cleanly

$ports = @(8792, 5174, 5173)

foreach ($port in $ports) {
    Write-Host "Checking for process listening on port $port..." -ForegroundColor Cyan
    $connections = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
    if ($connections) {
        $pids = $connections | Select-Object -ExpandProperty OwningProcess -Unique
        foreach ($p in $pids) {
            try {
                $proc = Get-Process -Id $p -ErrorAction SilentlyContinue
                if ($proc) {
                    Write-Host "Stopping process $($proc.ProcessName) (PID: $p) on port $port..." -ForegroundColor Yellow
                    Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
                }
            } catch {
                Write-Host "Could not stop PID $p : $_" -ForegroundColor Red
            }
        }
    } else {
        Write-Host "No active process found on port $port." -ForegroundColor Gray
    }
}

Write-Host "CompetePulse servers stopped cleanly." -ForegroundColor Green
