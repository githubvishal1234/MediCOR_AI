<#
.SYNOPSIS
  Build a submission zip of MediCOR AI that contains no secrets.

.DESCRIPTION
  Copies the project to a temp folder, leaving out everything that must
  never be handed in:
    - live .env files (database password, SECRET_KEY, provider API keys)
    - _backups\ and _twilio-migration-backup\ (old copies of those .env files)
    - venv, node_modules, build output and caches (rebuildable, and huge)
    - uploads\ (clinic documents may contain patient data)
  It then refuses to zip if any .env-style file slipped through.
  The .env.example templates are kept so the project can be configured.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\scripts\make_submission_zip.ps1
#>
param(
    [string]$Out = (Join-Path ([Environment]::GetFolderPath("Desktop")) "MediCOR_AI_submission.zip")
)

$ErrorActionPreference = "Stop"
$Root  = Split-Path -Parent $PSScriptRoot
$Temp  = Join-Path $env:TEMP ("medicor_submission_" + (Get-Date -Format "yyyyMMddHHmmss"))
$Stage = Join-Path $Temp "MediCOR_AI"

$excludeDirs  = @("venv", "node_modules", "dist", ".smoke-out", "uploads", ".model_cache",
                  "__pycache__", ".pytest_cache", "_backups", "_twilio-migration-backup",
                  "graphify-out", ".claude", ".git")
$excludeFiles = @(".env", ".env.local", ".env.production", "*.bak", "*.bak-*", "*.pyc", "*.log")

robocopy $Root $Stage /E /XD $excludeDirs /XF $excludeFiles /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed with exit code $LASTEXITCODE" }

# Belt and braces: nothing env-like except the templates may be in the bundle.
$leaks = Get-ChildItem $Stage -Recurse -Force -File |
    Where-Object { $_.Name -like ".env*" -and $_.Name -notlike "*.example" }
if ($leaks) {
    Remove-Item -Recurse -Force $Temp
    throw "Refusing to zip - secret-bearing files found: $($leaks.FullName -join ', ')"
}

if (Test-Path $Out) { Remove-Item -Force $Out }
Compress-Archive -Path $Stage -DestinationPath $Out
Remove-Item -Recurse -Force $Temp

$sizeMb = [math]::Round((Get-Item $Out).Length / 1MB, 1)
Write-Host "Submission zip written: $Out ($sizeMb MB)" -ForegroundColor Green
Write-Host "Excluded: .env files, backups, venv, node_modules, build output, uploads."
Write-Host "To run it, copy each .env.example to .env and fill in real values (see MEDICOR_AI_RUN_COMMANDS.md)."
