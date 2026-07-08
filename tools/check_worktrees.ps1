# Worktree / agent-branch inventory: shows unreflected work at a glance.
#   - worktrees with uncommitted changes or commits not in main
#   - stale worktree registrations (folder deleted)
#   - agent branches (worktree-*) not yet merged / already merged into main
# See doc/agent_execution_rules.md section 7.
# NOTE: keep this file ASCII-only. Windows PowerShell 5.1 reads BOM-less
#       files as ANSI (CP932), and multibyte comments corrupt parsing.

$ErrorActionPreference = 'Continue'
$root = Split-Path $PSScriptRoot -Parent

Write-Output "=== Worktrees ==="
$wtPaths = @()
git -C $root worktree list --porcelain | ForEach-Object {
    if ($_ -match '^worktree (.+)$') { $wtPaths += $Matches[1] }
}
$dirtyByBranch = @{}
foreach ($p in $wtPaths) {
    if (-not (Test-Path $p)) {
        Write-Output "[!] MISSING  $p  (folder gone -> git worktree prune)"
        continue
    }
    $br = git -C $p branch --show-current
    $dirty = @(git -C $p status --short).Count
    if ($br) { $dirtyByBranch[$br] = $dirty }
    $ahead = @(git -C $p log --oneline "main..HEAD" 2>$null).Count
    # main checkout is always dirty with the user's own work; only flag worktrees
    $mark = if (($br -ne 'main' -and $dirty -gt 0) -or $ahead -gt 0) { '[!]' } else { '   ' }
    Write-Output ("{0} {1}" -f $mark, $p)
    Write-Output ("      branch={0}  dirty={1}  ahead-of-main={2}" -f $br, $dirty, $ahead)
}

Write-Output ""
Write-Output "=== Agent branches NOT merged into main (pending review/merge) ==="
$unmerged = git -C $root branch --no-merged main --list 'worktree-*' |
    ForEach-Object { ($_ -replace '^[+*]', '').Trim() } | Where-Object { $_ }
if ($unmerged) {
    foreach ($b in $unmerged) {
        Write-Output "[!] $b"
        git -C $root log --oneline -3 "main..$b" | ForEach-Object { Write-Output "      $_" }
    }
} else { Write-Output "    (none)" }

Write-Output ""
Write-Output "=== Agent branches already merged (safe to delete) ==="
$merged = git -C $root branch --merged main --list 'worktree-*' |
    ForEach-Object { ($_ -replace '^[+*]', '').Trim() } | Where-Object { $_ }
if ($merged) {
    foreach ($b in $merged) {
        if ($dirtyByBranch[$b] -gt 0) {
            Write-Output "[!] $b  (worktree still has $($dirtyByBranch[$b]) uncommitted changes - do NOT delete yet)"
        } else {
            Write-Output "    $b  -> git branch -d $b"
        }
    }
} else { Write-Output "    (none)" }
