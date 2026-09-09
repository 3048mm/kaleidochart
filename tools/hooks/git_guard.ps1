# PreToolUse hook: guard dangerous git usage (doc/agent_execution_rules.md sec.7 / 10.6).
#   deny: git add -A/--all/. , force push, push to main/master, commit --amend
#   ask : git commit outside .claude/worktrees/ (user may approve interactively)
#   ask : bare git worktree remove (use tools/remove_worktree.ps1 instead, sec.11.3)
# Reads the hook JSON on stdin, writes a permissionDecision JSON on violation.
#
# Every git-touching invocation is also appended to logs/git_guard.log with the
# decision and the rule codes that fired. Without that record there is no way to
# tell "this rule protects us weekly" from "this rule has never fired once", and
# the second kind is exactly what should be deleted as the model improves
# (see doc/issue_list.md P3, harness simplification). Logging is best effort:
# if it fails for any reason the guard still returns its decision.
#
# NOTE: keep this file ASCII-only. Windows PowerShell 5.1 reads BOM-less files
#       as ANSI (CP932), and multibyte text corrupts parsing.
# NOTE: the log is written with -Encoding ASCII on purpose, so non-ASCII text
#       inside a logged command shows up as '?'. That is an accepted trade-off:
#       the log exists to count which rules fire, git commands themselves are
#       ASCII, and a UTF-8 log would be mis-decoded by Get-Content unless every
#       reader remembers -Encoding UTF8 (see rules sec.5.2, a repeat offender).
# NOTE: never Write-Output anything but the decision JSON. Anything else on
#       stdout is parsed as the hook result and breaks the contract.

$raw = [Console]::In.ReadToEnd()
try { $j = $raw | ConvertFrom-Json } catch { exit 0 }
$cmd = $j.tool_input.command
if (-not $cmd) { exit 0 }

$deny = @()
$ask = @()
$codes = @()

# 1. bulk staging
if ($cmd -match 'git(\s+-C\s+("[^"]*"|\S+))?\s+add\s+(\S+\s+)*(-A|--all|\.)(\s|$|")') {
    $deny += 'git add -A / --all / . is forbidden: stage explicit paths only (rules 10.6; prevents committing worktree sandbox parquet)'
    $codes += 'add-all'
}

# 2. force push / push to main / amend (forbidden everywhere)
if ($cmd -match 'git\b[^|;&]*\bpush\b[^|;&]*(--force|\s-f(\s|$))') {
    $deny += 'force push is forbidden (rules 7)'
    $codes += 'force-push'
}
if ($cmd -match 'git\b[^|;&]*\bpush\b[^|;&]*\s(main|master)(\s|$|:)') {
    $deny += 'push to main/master is forbidden (rules 7)'
    $codes += 'push-main'
}
if ($cmd -match 'git\b[^|;&]*\bcommit\b[^|;&]*--amend') {
    $deny += 'git commit --amend is forbidden (rules 7)'
    $codes += 'amend'
}

# 3. bare `git worktree remove` -> ask (rules 11.3)
# It is the one command that FOLLOWS a directory junction and deletes the target.
# Reproduced 2026-09-09 with a canary: raw removal took it from 2 files to 0,
# while tools/remove_worktree.ps1 left it untouched. The script detaches every
# reparse point first, so route removals through it.
# NOTE: this only sees agent tool calls. The script's own internal
# `git worktree remove` runs as a child process and is not intercepted.
if ($cmd -match 'git\b[^|;&]*\bworktree\b[^|;&]*\bremove\b') {
    $ask += 'bare `git worktree remove` follows junctions and deletes what they point at (rules 11.3; reproduced 2026-09-09). Use tools/remove_worktree.ps1, which detaches reparse points first. Confirm only if you have verified there are no junctions under the worktree.'
    $codes += 'worktree-remove'
}

# 4. commit outside a worktree -> ask the user (rules 7: user owns main-checkout commits)
if ($deny.Count -eq 0 -and $cmd -match 'git\b[^|;&]*\bcommit\b') {
    $cwdNorm = ''
    if ($j.cwd) { $cwdNorm = ($j.cwd -replace '\\', '/') }
    $cmdNorm = ($cmd -replace '\\', '/')
    $inWorktree = $cwdNorm -match '/\.claude/worktrees/'
    $targetsWorktree = $cmdNorm -match '-C\s+("[^"]*\.claude/worktrees/[^"]*"|''[^'']*\.claude/worktrees/[^'']*''|\S*\.claude/worktrees/\S*)'
    if (-not ($inWorktree -or $targetsWorktree)) {
        $ask += 'git commit in the main checkout: agent commits belong on worktree branches (rules 7). Confirm only if the user explicitly asked for this commit.'
        $codes += 'commit-main'
    }
}

$decision = 'allow'
if ($deny.Count -gt 0) { $decision = 'deny' }
elseif ($ask.Count -gt 0) { $decision = 'ask' }

# --- trigger log (best effort; never let it change the decision) ---------------
# Only git-touching commands are recorded. Logging every Bash call would bury the
# signal, and the question this log answers is per-rule: which guard rules fire.
if ($cmd -match '\bgit\b') {
    try {
        $repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
        $logDir = Join-Path $repoRoot 'logs'
        if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
        $logFile = Join-Path $logDir 'git_guard.log'

        # Single-generation rotation. The file only grows by one short line per git
        # command, so 1 MB is months of history; keeping one old generation is enough.
        if (Test-Path $logFile) {
            if ((Get-Item $logFile).Length -gt 1MB) {
                Move-Item $logFile ($logFile + '.1') -Force -ErrorAction SilentlyContinue
            }
        }

        $ruleField = if ($codes.Count -gt 0) { ($codes -join ',') } else { '-' }
        $toolName = if ($j.tool_name) { $j.tool_name } else { '?' }
        $cwdField = if ($j.cwd) { $j.cwd } else { '?' }
        # Collapse newlines: one event must stay one line, or counting breaks.
        $cmdOneLine = ($cmd -replace '[\r\n]+', ' ')
        if ($cmdOneLine.Length -gt 160) { $cmdOneLine = $cmdOneLine.Substring(0, 160) + '...' }

        $line = '{0} decision={1} rules={2} tool={3} cwd={4} cmd={5}' -f `
            (Get-Date -Format 'yyyy-MM-ddTHH:mm:ss'), $decision, $ruleField, $toolName, $cwdField, $cmdOneLine
        Add-Content -Path $logFile -Value $line -Encoding ASCII -ErrorAction SilentlyContinue
    } catch {
        # Deliberately silent: a failed log write must never block a git command,
        # and anything written to stdout here would be parsed as the hook result.
    }
}
# ------------------------------------------------------------------------------

if ($decision -ne 'allow') {
    $reason = (($deny + $ask) -join '; ')
    $out = @{
        hookSpecificOutput = @{
            hookEventName = 'PreToolUse'
            permissionDecision = $decision
            permissionDecisionReason = $reason
        }
    } | ConvertTo-Json -Depth 5 -Compress
    Write-Output $out
}
exit 0
