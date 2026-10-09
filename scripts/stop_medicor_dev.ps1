<#
.SYNOPSIS
  Stop the MediCOR AI development servers started by start_medicor_dev.ps1
  (or by hand): the FastAPI backend, the Vite frontend and the voice gateway.

.DESCRIPTION
  Finds processes by their command line (uvicorn app.main:app,
  twilio_deepgram.run, and the Vite dev server of THIS project) and stops
  each process tree. It does not touch SQL Server, the database, or any
  other Python / Node process on the machine.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\scripts\stop_medicor_dev.ps1
#>

$Root = Split-Path -Parent $PSScriptRoot
$FrontendDir = Join-Path $Root "MediCOR_AI-frontend\MediCOR_AI-frontend"

$targets = Get-CimInstance Win32_Process | Where-Object {
    $cmd = $_.CommandLine
    $cmd -and (
        ($cmd -like "*uvicorn app.main:app*") -or
        ($cmd -like "*twilio_deepgram.run*") -or
        ($cmd -like "*vite*" -and $cmd -like "*$FrontendDir*") -or
        ($_.Name -eq "node.exe" -and $cmd -like "*vite*" -and (Get-NetTCPConnection -OwningProcess $_.ProcessId -LocalPort 5173 -State Listen -ErrorAction SilentlyContinue))
    )
}

if (-not $targets) {
    Write-Host "No MediCOR dev servers are running."
} else {
    foreach ($p in $targets) {
        $label = if ($p.CommandLine -like "*uvicorn*") { "backend" } elseif ($p.CommandLine -like "*twilio_deepgram*") { "voice gateway" } else { "frontend" }
        Write-Host "Stopping $label (PID $($p.ProcessId))"
        # /T also stops the process's children (uvicorn --reload runs a worker child).
        & taskkill.exe /PID $p.ProcessId /T /F 2>$null | Out-Null
    }
}

# Anything still bound to the MediCOR ports?
Start-Sleep -Milliseconds 800
foreach ($port in 8000, 5173, 8080) {
    $l = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($l) {
        $owner = Get-Process -Id $l[0].OwningProcess -ErrorAction SilentlyContinue
        Write-Host "Port $port is still in use by $($owner.ProcessName) (PID $($l[0].OwningProcess)). It was not started by MediCOR's scripts, so it was left alone." -ForegroundColor Yellow
    }
}
Write-Host "Done. (The service windows stay open showing that the process ended - close them normally.)"
