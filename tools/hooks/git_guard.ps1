# PreToolUse hook: guard dangerous git usage (doc/agent_execution_rules.md sec.7 / 10.6).
#   deny: git add -A/--all/. , force push, push to main/master, commit --amend
#   ask : git commit outside .claude/worktrees/ (user may approve interactively)
# Reads the hook JSON on stdin, writes a permissionDecision JSON on violation.
# NOTE: keep this file ASCII-only. Windows PowerShell 5.1 reads BOM-less files
#       as ANSI (CP932), and multibyte text corrupts parsing.

$raw = [Console]::In.ReadToEnd()
try { $j = $raw | ConvertFrom-Json } catch { exit 0 }
$cmd = $j.tool_input.command
if (-not $cmd) { exit 0 }

$deny = @()
$ask = @()

# 1. bulk staging
if ($cmd -match 'git(\s+-C\s+("[^"]*"|\S+))?\s+add\s+(\S+\s+)*(-A|--all|\.)(\s|$|")') {
    $deny += 'git add -A / --all / . is forbidden: stage explicit paths only (rules 10.6; prevents committing worktree sandbox parquet)'
}

# 2. force push / push to main / amend (forbidden everywhere)
if ($cmd -match 'git\b[^|;&]*\bpush\b[^|;&]*(--force|\s-f(\s|$))') {
    $deny += 'force push is forbidden (rules 7)'
}
if ($cmd -match 'git\b[^|;&]*\bpush\b[^|;&]*\s(main|master)(\s|$|:)') {
    $deny += 'push to main/master is forbidden (rules 7)'
}
if ($cmd -match 'git\b[^|;&]*\bcommit\b[^|;&]*--amend') {
    $deny += 'git commit --amend is forbidden (rules 7)'
}

# 3. commit outside a worktree -> ask the user (rules 7: user owns main-checkout commits)
if ($deny.Count -eq 0 -and $cmd -match 'git\b[^|;&]*\bcommit\b') {
    $cwdNorm = ''
    if ($j.cwd) { $cwdNorm = ($j.cwd -replace '\\', '/') }
    $cmdNorm = ($cmd -replace '\\', '/')
    $inWorktree = $cwdNorm -match '/\.claude/worktrees/'
    $targetsWorktree = $cmdNorm -match '-C\s+("[^"]*\.claude/worktrees/[^"]*"|''[^'']*\.claude/worktrees/[^'']*''|\S*\.claude/worktrees/\S*)'
    if (-not ($inWorktree -or $targetsWorktree)) {
        $ask += 'git commit in the main checkout: agent commits belong on worktree branches (rules 7). Confirm only if the user explicitly asked for this commit.'
    }
}

if ($deny.Count -gt 0 -or $ask.Count -gt 0) {
    $decision = if ($deny.Count -gt 0) { 'deny' } else { 'ask' }
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
