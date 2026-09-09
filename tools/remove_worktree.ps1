# Safe worktree removal. ALWAYS use this instead of a bare `git worktree remove`.
#
# Why (doc/agent_execution_rules.md sec.11.3): `git worktree remove` is the one
# command that FOLLOWS a directory junction and deletes what it points at.
# Remove-Item, rm -rf and git clean -xdf all leave the target alone; only this
# one destroys it. On 2026-09-09 two worktrees held
#   <worktree>/frontend/node_modules -> <main>/frontend/node_modules
# and removing them raw would have wiped the main checkout's node_modules.
#
# So this script always does, in order:
#   1. refuse on conditions that would lose work
#   2. find every reparse point under the worktree and detach the LINK only,
#      verifying the target still exists after each one
#   3. only then remove the worktree (or plain-delete an unregistered leftover)
#   4. optionally delete the branch, and only if it is merged into main
#
# Usage:
#   tools/remove_worktree.ps1 <name>                 # registered worktree, clean
#   tools/remove_worktree.ps1 <name> -Force          # allow uncommitted changes
#   tools/remove_worktree.ps1 <name> -DeleteBranch   # also drop the merged branch
#   tools/remove_worktree.ps1 <name> -DryRun         # show the plan, change nothing
#
# NOTE: keep this file ASCII-only. Windows PowerShell 5.1 reads BOM-less files
#       as ANSI (CP932), and multibyte text corrupts parsing.

param(
    [Parameter(Mandatory = $true, Position = 0)][string]$Name,
    [switch]$Force,
    [switch]$DeleteBranch,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

function Say($msg)  { Write-Output $msg }
function Warn($msg) { Write-Output ("[!] " + $msg) }
function Die($msg)  { Write-Output ("[ABORT] " + $msg); exit 1 }

# --- locate the main checkout -------------------------------------------------
$here = Split-Path $PSScriptRoot -Parent
$gitCommon = git -C $here rev-parse --path-format=absolute --git-common-dir 2>$null
if (-not $gitCommon) { Die "not inside a git repository: $here" }
$mainRoot = Split-Path $gitCommon -Parent
$wtRoot = Join-Path (Join-Path $mainRoot '.claude') 'worktrees'

# Accept either a bare name or a full path.
if ([System.IO.Path]::IsPathRooted($Name)) { $target = $Name }
else { $target = Join-Path $wtRoot $Name }
if (-not (Test-Path $target)) { Die "no such directory: $target" }
$target = (Get-Item $target).FullName
$shortName = Split-Path $target -Leaf

Say "=== remove_worktree: $shortName ==="
Say ("path      : {0}" -f $target)
if ($DryRun) { Say "mode      : DRY RUN (nothing will be changed)" }

# --- classify: registered worktree or unregistered leftover -------------------
$registered = @()
git -C $mainRoot worktree list --porcelain | ForEach-Object {
    if ($_ -match '^worktree (.+)$') {
        $registered += ([IO.Path]::GetFullPath($Matches[1])).TrimEnd([IO.Path]::DirectorySeparatorChar).ToLower()
    }
}
$key = ([IO.Path]::GetFullPath($target)).TrimEnd([IO.Path]::DirectorySeparatorChar).ToLower()
$isRegistered = $registered -contains $key
Say ("kind      : {0}" -f $(if ($isRegistered) { 'registered worktree' } else { 'unregistered leftover' }))

if ($key -eq (([IO.Path]::GetFullPath($mainRoot)).TrimEnd([IO.Path]::DirectorySeparatorChar).ToLower())) {
    Die "refusing to remove the main checkout"
}

# --- refuse on conditions that would lose work --------------------------------
$branch = $null
if ($isRegistered) {
    $branch = (git -C $target branch --show-current)
    $dirty = @(git -C $target status --short).Count
    $ahead = @(git -C $target log --oneline "main..HEAD" 2>$null).Count
    Say ("branch    : {0}" -f $branch)
    Say ("dirty     : {0}" -f $dirty)
    Say ("ahead     : {0} commit(s) not in main" -f $ahead)

    if ($dirty -gt 0 -and -not $Force) {
        git -C $target status --short | ForEach-Object { Say ("            " + $_) }
        Die "$dirty uncommitted change(s). Review them, then re-run with -Force to discard."
    }
    if ($ahead -gt 0) {
        # Not fatal: the branch keeps those commits after the worktree is gone.
        Warn "$ahead commit(s) are not in main. They stay on branch '$branch' after removal."
        if ($DeleteBranch) { Die "-DeleteBranch would drop those commits. Merge first, or drop -DeleteBranch." }
    }
}

# --- step 2: detach reparse points (THE reason this script exists) ------------
Say ""
Say "--- reparse points (junctions / symlinks) ---"
$links = @(Get-ChildItem $target -Recurse -Force -Directory -Attributes ReparsePoint -ErrorAction SilentlyContinue)
if ($links.Count -eq 0) {
    Say "    none"
} else {
    foreach ($l in $links) {
        $tgt = ($l.Target -join ' | ')
        Say ("    {0}" -f $l.FullName)
        Say ("      -> {0}" -f $tgt)
    }
    if ($DryRun) {
        Say "    (dry run: links would be detached here, targets left untouched)"
    } else {
        foreach ($l in $links) {
            $linkTargets = @($l.Target)
            try {
                # recursive:$false removes the reparse point itself and never
                # follows it, so whatever it points at is untouched.
                [System.IO.Directory]::Delete($l.FullName, $false)
            } catch {
                Die ("could not detach junction {0}: {1}" -f $l.FullName, $_.Exception.Message)
            }
            if (Test-Path $l.FullName) { Die ("junction still present after detach: {0}" -f $l.FullName) }
            foreach ($t in $linkTargets) {
                if ($t -and -not (Test-Path $t)) {
                    Die ("target vanished while detaching {0} -> {1}. STOP and investigate." -f $l.FullName, $t)
                }
            }
            Say ("    detached (target intact): {0}" -f $l.FullName)
        }
        # Nothing may remain, or `git worktree remove` would follow it.
        $again = @(Get-ChildItem $target -Recurse -Force -Directory -Attributes ReparsePoint -ErrorAction SilentlyContinue)
        if ($again.Count -gt 0) { Die "reparse points still present; refusing to remove" }
    }
}

# --- step 3: remove ------------------------------------------------------------
Say ""
if ($DryRun) {
    if ($isRegistered) { Say ("would run : git worktree remove{0} {1}" -f $(if ($Force) { ' --force' } else { '' }), $target) }
    else { Say ("would run : [IO.Directory]::Delete('{0}', recursive)" -f $target) }
    if ($DeleteBranch -and $branch) { Say ("would run : git branch -d {0}" -f $branch) }
    Say "dry run complete; nothing changed."
    exit 0
}

if ($isRegistered) {
    $args = @('-C', $mainRoot, 'worktree', 'remove')
    if ($Force) { $args += '--force' }
    $args += $target
    $out = & git @args 2>&1
    if ($LASTEXITCODE -ne 0) { Die ("git worktree remove failed: " + ($out -join ' ')) }
} else {
    [System.IO.Directory]::Delete($target, $true)
}
if (Test-Path $target) { Die "directory still present after removal: $target" }
Say ("removed   : {0}" -f $target)

# --- step 4: branch ------------------------------------------------------------
if ($DeleteBranch -and $branch) {
    # -d (not -D) so git refuses if the branch is not merged.
    $out = git -C $mainRoot branch -d $branch 2>&1
    if ($LASTEXITCODE -ne 0) { Warn ("branch not deleted: " + ($out -join ' ')) }
    else { Say ("branch    : " + ($out -join ' ')) }
}

Say ""
Say "Done. Run tools/check_worktrees.ps1 to confirm the inventory."
