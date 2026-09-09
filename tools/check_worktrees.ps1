# Worktree / agent-branch inventory: shows unreflected work at a glance.
#   - worktrees with uncommitted changes or commits not in main
#   - stale worktree registrations (folder deleted)
#   - unregistered leftovers (folder present, not in git worktree list)
#   - agent branches (worktree-*) not yet merged / already merged into main
#   - data provisioning state of each worktree (read / write / NOT provisioned)
#   - phantom disk: parquet generations pinned by a worktree after production pruned them
#   - skills mirror drift (.claude/skills -> .agents/skills) and frontmatter lint
#   - stale plans in doc/in_progress (a development item nobody is carrying)
# See doc/agent_execution_rules.md sections 7, 9, 10.3 and 11.3.
# NOTE: keep this file ASCII-only. Windows PowerShell 5.1 reads BOM-less
#       files as ANSI (CP932), and multibyte comments corrupt parsing.

param(
    # Days without an edit before a doc/in_progress plan is reported. 14 by
    # default: work here is regularly interrupted for a week at a time, so a
    # shorter window would cry wolf on items that are merely paused.
    [int]$StaleDays = 14
)

$ErrorActionPreference = 'Continue'
$root = Split-Path $PSScriptRoot -Parent

# Production data lives in the MAIN checkout, which is not necessarily $root:
# this script may be run from a worktree's own copy, and then $root is that
# worktree. --git-common-dir always resolves to the main checkout's .git.
$gitCommon = git -C $root rev-parse --path-format=absolute --git-common-dir 2>$null
$mainRoot = if ($gitCommon) { Split-Path $gitCommon -Parent } else { $root }
$prodParquetDir = Join-Path $mainRoot 'data\parquet_master'

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
    # disposable sandbox data inside a worktree (skip main: its data/ is production)
    if ($br -ne 'main') {
        $dataDir = Join-Path $p 'data'

        # provisioning state, from config.local.toml written by
        # tools/provision_worktree_data.py (see doc/agent_execution_rules.md section 10.3)
        $confPath = Join-Path $p 'config.local.toml'
        $mode = 'NOT provisioned'
        if (Test-Path $confPath) {
            # -Encoding UTF8 is REQUIRED: the file is BOM-less UTF-8 with Japanese
            # comments, and PowerShell 5.1 would decode it as CP932, corrupting
            # line boundaries. See doc/agent_execution_rules.md section 5.
            $conf = Get-Content $confPath -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
            if ($conf -match '(?m)^\s*root\s*=\s*"([^"]+)"') {
                $mode = if ($Matches[1] -match 'sandbox') { 'write (sandbox)' } else { 'read (prod ref)' }
            }
        }
        $pmark = if ($mode -eq 'NOT provisioned') { '[!]' } else { '   ' }
        Write-Output ("      {0} data provisioning: {1}" -f $pmark, $mode)

        if (Test-Path $dataDir) {
            $bytes = (Get-ChildItem $dataDir -Recurse -File -ErrorAction SilentlyContinue |
                Measure-Object Length -Sum).Sum

            # Parquet shared with production via hardlink costs no extra disk, so the
            # raw byte sum overstates real usage. Split it, and flag generations that
            # production has already pruned: those keep their blocks alive (phantom disk)
            # until this worktree is removed.
            $sbPq = Join-Path $dataDir 'sandbox\parquet_master'
            $sharedBytes = 0
            $stale = @()
            if (Test-Path $sbPq) {
                foreach ($f in (Get-ChildItem $sbPq -File -Filter *.parquet -ErrorAction SilentlyContinue)) {
                    if (Test-Path (Join-Path $prodParquetDir $f.Name)) { $sharedBytes += $f.Length }
                    else { $stale += $f }
                }
            }
            $mb = [math]::Round($bytes / 1MB, 0)
            $ownMb = [math]::Round(($bytes - $sharedBytes) / 1MB, 0)
            if ($mb -ge 1) {
                if ($sharedBytes -gt 0) {
                    Write-Output ("      local data: {0} MB listed / {1} MB not shared with production" -f $mb, $ownMb)
                } else {
                    Write-Output ("      local data: {0} MB" -f $mb)
                }
            }
            if ($stale.Count -gt 0) {
                $smb = [math]::Round((($stale | Measure-Object Length -Sum).Sum) / 1MB, 0)
                # Either a stale real copy, or a hardlink keeping a pruned generation's
                # blocks alive. Both are reclaimed the same way: remove the worktree.
                Write-Output ("      [!] holds {0} parquet generation(s) no longer in production" -f $stale.Count)
                Write-Output ("          {0} MB reclaimed when this worktree is removed" -f $smb)
            }
        }
    }
}

Write-Output ""
Write-Output "=== Unregistered leftovers in .claude/worktrees ==="
# A worktree removal that stops halfway leaves a real directory that
# `git worktree list` does not know about, so every check above skips it:
# they all iterate the registered list. Observed on 2026-09-01 with
# `dataview-columns` (2.2 MB, no .git, no backend/). This section is the only
# one that can see that class of residue.
# Path comparison uses [IO.Path]::GetFullPath, which normalises separators to the
# OS native form: `git worktree list` reports forward slashes while Get-ChildItem
# reports backslashes, and a plain string compare would call every folder a leftover.
$sep = [IO.Path]::DirectorySeparatorChar
$wtRoot = Join-Path (Join-Path $mainRoot '.claude') 'worktrees'
if (Test-Path $wtRoot) {
    $registered = @{}
    foreach ($p in $wtPaths) {
        try { $registered[([IO.Path]::GetFullPath($p)).TrimEnd($sep).ToLower()] = $true } catch {}
    }
    $leftovers = @(Get-ChildItem $wtRoot -Directory -ErrorAction SilentlyContinue | Where-Object {
        -not $registered.ContainsKey(([IO.Path]::GetFullPath($_.FullName)).TrimEnd($sep).ToLower())
    })
    if ($leftovers.Count -gt 0) {
        foreach ($d in $leftovers) {
            $bytes = (Get-ChildItem $d.FullName -Recurse -File -ErrorAction SilentlyContinue |
                Measure-Object Length -Sum).Sum
            $mb = [math]::Round($bytes / 1MB, 1)
            $hasGit = Test-Path (Join-Path $d.FullName '.git')
            $kind = if ($hasGit) { 'has .git but is not registered (registration pruned?)' } else { 'no .git: a worktree removal stopped partway' }
            Write-Output ("[!] {0}  ({1} MB)  {2}" -f $d.Name, $mb, $kind)
            Write-Output ("      tools/remove_worktree.ps1 {0}" -f $d.Name)
        }
        Write-Output "    NOTE: always remove through tools/remove_worktree.ps1. It detaches"
        Write-Output "          junctions first; a bare git worktree remove follows them and"
        Write-Output "          deletes what they point at (rules sec.11.3, reproduced 2026-09-09)."
    } else { Write-Output "    (none)" }
} else {
    Write-Output "    (no .claude/worktrees directory)"
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
            # remove_worktree.ps1 detaches junctions, removes the worktree and drops
            # the branch in one safe step; plain `git branch -d` leaves the worktree.
            $wtName = $b -replace '^worktree-', ''
            Write-Output "    $b  -> tools/remove_worktree.ps1 $wtName -DeleteBranch"
        }
    }
} else { Write-Output "    (none)" }

Write-Output ""
Write-Output "=== Skills mirror (.claude/skills -> .agents/skills) ==="
# Windows cannot share the two trees with a link (rules sec.11), so they are two
# real copies and the drift is detected here instead. Also lints the YAML
# frontmatter: a '## Metadata' heading is not parsed and the skill silently
# stops auto-invoking (this happened to 2 skills before 2026-09-07).
$py = Join-Path (Join-Path $mainRoot 'venv') (Join-Path 'Scripts' 'python.exe')
$syncScript = Join-Path (Join-Path $root 'tools') 'sync_skills.py'
if ((Test-Path $py) -and (Test-Path $syncScript)) {
    & $py $syncScript --check --root $root | ForEach-Object { Write-Output "    $_" }
} else {
    Write-Output "    (skipped: venv python or tools/sync_skills.py not found)"
}

Write-Output ""
Write-Output "=== Stale plans in doc/in_progress (no edit for $StaleDays+ days) ==="
# A plan file is the handoff document for an unfinished development item
# (rules sec.9). One that stops being edited is an item nobody is carrying,
# and nothing else in the workflow surfaces that. 14 days is the default
# because work here is regularly interrupted for a week at a time.
$inProgress = Join-Path (Join-Path $mainRoot 'doc') 'in_progress'
if (Test-Path $inProgress) {
    $cutoff = (Get-Date).AddDays(-$StaleDays)
    $stale = @(Get-ChildItem $inProgress -File -Filter '*_plan.md' -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -ne '_TEMPLATE.md' -and $_.LastWriteTime -lt $cutoff } |
        Sort-Object LastWriteTime)
    if ($stale.Count -gt 0) {
        foreach ($f in $stale) {
            $days = [int]((Get-Date) - $f.LastWriteTime).TotalDays
            Write-Output ("[!] {0}  (last edited {1} days ago, {2})" -f $f.Name, $days, $f.LastWriteTime.ToString('yyyy-MM-dd'))
        }
        Write-Output "    Finish it, or move it to doc/completed/ with the outcome recorded."
    } else { Write-Output "    (none)" }
} else {
    Write-Output "    (no doc/in_progress directory)"
}
