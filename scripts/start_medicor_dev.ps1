<#
.SYNOPSIS
  Start MediCOR AI for local development: FastAPI backend (port 8000) and
  the Vite frontend (port 5173). Optionally the Twilio voice gateway (8080).

.DESCRIPTION
  Each service opens in its own PowerShell window so you can read its log.
  Close a window (or run scripts\stop_medicor_dev.ps1) to stop it.

  This script only STARTS processes. It never creates, migrates, seeds,
  resets or deletes anything in the database, and never prints secrets.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\scripts\start_medicor_dev.ps1

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\scripts\start_medicor_dev.ps1 -WithVoiceGateway
#>
param(
    # Also start the Twilio + Deepgram voice gateway (voice-agent/twilio_deepgram).
    [switch]$WithVoiceGateway
)

$ErrorActionPreference = "Stop"

$Root     = Split-Path -Parent $PSScriptRoot
$Backend  = Join-Path $Root "MediCOR_AI-backend\MediCOR_AI-backend"
$Frontend = Join-Path $Root "MediCOR_AI-frontend\MediCOR_AI-frontend"
$Voice    = Join-Path $Backend "voice-agent"
$Python   = Join-Path $Backend "venv\Scripts\python.exe"

function Fail($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }
function Ok($msg)   { Write-Host "  ok  $msg" -ForegroundColor Green }
function PortInUse($port) { [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) }

Write-Host "`nMediCOR AI - pre-flight checks" -ForegroundColor Cyan

if (-not (Test-Path $Python)) { Fail "Backend virtual environment not found at $Python. See MEDICOR_AI_RUN_COMMANDS.md section 3." }
Ok "backend venv: $Python"

if (-not (Test-Path (Join-Path $Backend ".env"))) { Fail "Missing $Backend\.env - copy .env.example to .env and fill it in (section 5)." }
Ok "backend .env present"

if (-not (Test-Path (Join-Path $Frontend "node_modules"))) { Fail "Frontend dependencies missing. Run: cd `"$Frontend`"; npm install" }
Ok "frontend node_modules present"

# SQL Server reachable? Read host/port from .env (non-secret keys only).
$envLines = Get-Content (Join-Path $Backend ".env")
$dbServer = (($envLines | Where-Object { $_ -match '^DB_SERVER=' }) -replace '^DB_SERVER=', '').Trim()
$dbPort   = (($envLines | Where-Object { $_ -match '^DB_PORT=' }) -replace '^DB_PORT=', '').Trim()
if (-not $dbServer) { $dbServer = "localhost" }
if (-not $dbPort)   { $dbPort = "1433" }
$dbHost = ($dbServer -split '\\')[0]
$tcp = New-Object System.Net.Sockets.TcpClient
try {
    $iar = $tcp.BeginConnect($dbHost, [int]$dbPort, $null, $null)
    if (-not $iar.AsyncWaitHandle.WaitOne(3000) -or -not $tcp.Connected) { throw "timeout" }
    Ok "SQL Server reachable at ${dbHost}:${dbPort}"
} catch {
    Fail "Cannot reach SQL Server at ${dbHost}:${dbPort}. Start the 'SQL Server (SQLEXPRESS)' service (section 6)."
} finally { $tcp.Close() }

$ports = @(8000, 5173)
if ($WithVoiceGateway) { $ports += 8080 }
foreach ($p in $ports) {
    if (PortInUse $p) { Fail "Port $p is already in use. Run scripts\stop_medicor_dev.ps1 first, or close whatever is using it." }
}
Ok "ports free: $($ports -join ', ')"

Write-Host "`nStarting services (each in its own window)..." -ForegroundColor Cyan

Start-Process powershell -WorkingDirectory $Backend -ArgumentList @(
    "-NoExit", "-Command",
    "`$host.UI.RawUI.WindowTitle = 'MediCOR backend :8000'; & '.\venv\Scripts\python.exe' -m uvicorn app.main:app --reload --port 8000"
)
Start-Process powershell -WorkingDirectory $Frontend -ArgumentList @(
    "-NoExit", "-Command",
    "`$host.UI.RawUI.WindowTitle = 'MediCOR frontend :5173'; npm run dev"
)
if ($WithVoiceGateway) {
    Start-Process powershell -WorkingDirectory $Voice -ArgumentList @(
        "-NoExit", "-Command",
        "`$host.UI.RawUI.WindowTitle = 'MediCOR voice gateway :8080'; & '..\venv\Scripts\python.exe' -m twilio_deepgram.run"
    )
}

function WaitFor($name, $url, $seconds) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 3
            if ($r.StatusCode -eq 200) { Ok "$name is up ($url)"; return $true }
        } catch { Start-Sleep -Milliseconds 800 }
    }
    Write-Host "  !!  $name did not answer at $url within $seconds s - check its window for errors." -ForegroundColor Yellow
    return $false
}

Write-Host "`nWaiting for services..." -ForegroundColor Cyan
$null = WaitFor "Backend"  "http://localhost:8000/health" 60
$null = WaitFor "Frontend" "http://localhost:5173/" 60
if ($WithVoiceGateway) { $null = WaitFor "Voice gateway" "http://localhost:8080/health" 45 }

Write-Host "`nMediCOR AI is running:" -ForegroundColor Cyan
Write-Host "  App (clinic dashboard):  http://localhost:5173/login"
Write-Host "  Platform admin console:  http://localhost:5173/admin/login"
Write-Host "  API health:              http://localhost:8000/health"
Write-Host "  API docs (dev only):     http://localhost:8000/docs"
if ($WithVoiceGateway) { Write-Host "  Voice gateway health:    http://localhost:8080/health" }
Write-Host "`nStop everything with:  powershell -ExecutionPolicy Bypass -File .\scripts\stop_medicor_dev.ps1`n"
