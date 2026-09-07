# Worktree / agent-branch inventory: shows unreflected work at a glance.
#   - worktrees with uncommitted changes or commits not in main
#   - stale worktree registrations (folder deleted)
#   - agent branches (worktree-*) not yet merged / already merged into main
#   - data provisioning state of each worktree (read / write / NOT provisioned)
#   - phantom disk: parquet generations pinned by a worktree after production pruned them
# See doc/agent_execution_rules.md sections 7 and 10.3.
# NOTE: keep this file ASCII-only. Windows PowerShell 5.1 reads BOM-less
#       files as ANSI (CP932), and multibyte comments corrupt parsing.

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

Write-Output ""
Write-Output "=== Skills mirror (.claude/skills -> .agents/skills) ==="
# Windows cannot share the two trees with a link (rules sec.11), so they are two
# real copies and the drift is detected here instead. Also lints the YAML
# frontmatter: a '## Metadata' heading is not parsed and the skill silently
# stops auto-invoking (this happened to 2 skills before 2026-09-07).
$py = Join-Path $mainRoot 'venv\Scripts\python.exe'
$syncScript = Join-Path $root 'tools\sync_skills.py'
if ((Test-Path $py) -and (Test-Path $syncScript)) {
    & $py $syncScript --check --root $root | ForEach-Object { Write-Output "    $_" }
} else {
    Write-Output "    (skipped: venv python or tools/sync_skills.py not found)"
}
