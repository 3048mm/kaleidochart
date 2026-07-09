# deploy_after_merge.ps1 - one-command data promotion after merging a type-B change
# (schema / indicator / pipeline). See doc/in_progress/deploy_after_merge_plan.md
# and doc/agent_execution_rules.md section 10.5.
#
# Usage (from the MAIN checkout root, after `git merge`):
#   .\tools\deploy_after_merge.ps1                    # default: rebuild from T3
#   .\tools\deploy_after_merge.ps1 -RebuildFrom T4
#   .\tools\deploy_after_merge.ps1 -RebuildFrom All   # full re-download + recompute (slow)
#   .\tools\deploy_after_merge.ps1 -DryRun            # regenerate + verify only, no swap
#
# Exit codes: 0 = success / 1 = aborted before promote (production untouched)
#             2 = HEALTH CHECK FAILED -> ROLLED BACK
# NOTE: keep this file ASCII-only (PS 5.1 reads BOM-less files as ANSI/CP932).

param(
    [ValidateSet('T2', 'T3', 'T4', 'T5', 'All')]
    [string]$RebuildFrom = 'T3',
    [switch]$DryRun,
    [switch]$KeepWorkspace,
    [switch]$SkipRegenerate  # reuse an existing workspace (rerun after an aborted gate)
)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent

# --- precheck 1: must run from the main checkout, never from a worktree copy
if (($root -replace '\\', '/') -match '/\.claude/worktrees/') {
    Write-Host "[ABORT] Run this from the MAIN checkout, not a worktree: $root" -ForegroundColor Red
    exit 1
}

# --- precheck 2: venv python must exist
$py = Join-Path $root 'venv\Scripts\python.exe'
if (-not (Test-Path $py)) {
    Write-Host "[ABORT] venv python not found: $py" -ForegroundColor Red
    exit 1
}

# --- precheck 3: API server must be stopped (do NOT auto-kill; ask the user)
$listening = netstat -ano | Select-String ':8000\s' | Select-String 'LISTENING'
if ($listening -and -not $DryRun) {
    Write-Host "[ABORT] Port 8000 is LISTENING - the API server appears to be running." -ForegroundColor Red
    Write-Host "        Stop the backend server first, then re-run this script." -ForegroundColor Red
    exit 1
}

# --- delegate to the python runner
$runner = Join-Path $root 'backend\scripts\deploy_after_merge.py'
$argList = @($runner, '--rebuild-from', $RebuildFrom)
if ($DryRun) { $argList += '--dry-run' }
if ($KeepWorkspace) { $argList += '--keep-workspace' }
if ($SkipRegenerate) { $argList += '--skip-regenerate' }

$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
Push-Location $root
try {
    & $py @argList
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}

switch ($code) {
    0 { Write-Host "`n[OK] Promotion finished. You can restart the API server." -ForegroundColor Green }
    1 { Write-Host "`n[ABORTED] Failed before promotion. Production data is UNCHANGED." -ForegroundColor Yellow }
    2 {
        Write-Host "`n=====================================================" -ForegroundColor Red
        Write-Host "  !! HEALTH CHECK FAILED -> ROLLED BACK !!" -ForegroundColor Red
        Write-Host "  Production was restored to the previous generation." -ForegroundColor Red
        Write-Host "  Investigate the log above before retrying." -ForegroundColor Red
        Write-Host "=====================================================" -ForegroundColor Red
    }
    default { Write-Host "`n[?] Unexpected exit code: $code" -ForegroundColor Yellow }
}
exit $code
