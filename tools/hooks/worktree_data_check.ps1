# SessionStart hook: warn when the session runs in a worktree whose data/ has
# not been provisioned. See doc/agent_execution_rules.md section 10.3.
#
# Why a warning and not an auto-fix: the mode (read / write) depends on what the
# task will do, and --mode write copies ~1.6GB. That choice belongs to a human or
# the orchestrating session, not to a startup hook.
#
# NOTE: keep this file ASCII-only, including the messages it emits. Windows
#       PowerShell 5.1 reads BOM-less files as ANSI (CP932) and multibyte text
#       corrupts parsing. Same rule as git_guard.ps1.

# Read stdin as UTF-8 EXPLICITLY, not via [Console]::In: that decodes with the
# console ANSI codepage (CP932 here) and mis-decoding swallows backslashes, which
# breaks the JSON and drops us into the catch below -- silently falling back to
# Get-Location. See doc/agent_execution_rules.md section 12.
$stdin = [Console]::OpenStandardInput()
$reader = New-Object System.IO.StreamReader($stdin, (New-Object System.Text.UTF8Encoding($false)))
$raw = $reader.ReadToEnd()
$reader.Dispose()
$cwd = $null
try {
    $j = $raw | ConvertFrom-Json
    if ($j.cwd) { $cwd = $j.cwd }
} catch { }
if (-not $cwd) { $cwd = (Get-Location).Path }

$norm = ($cwd -replace '\\', '/')
$marker = '/.claude/worktrees/'
$idx = $norm.IndexOf($marker)
if ($idx -lt 0) { exit 0 }   # main checkout: data/ is production, nothing to do

$rest = $norm.Substring($idx + $marker.Length)
$name = ($rest -split '/')[0]
if (-not $name) { exit 0 }
$wtRoot = $norm.Substring(0, $idx) + $marker + $name

# provision_worktree_data.py records the data location in config.local.toml
$conf = Join-Path $wtRoot 'config.local.toml'
if (Test-Path $conf) {
    # -Encoding UTF8 is REQUIRED. config.local.toml is BOM-less UTF-8 with
    # Japanese comments; PowerShell 5.1 otherwise decodes it as CP932, and the
    # mis-decoded comment swallows the following newline, gluing "[data]" onto
    # the comment line so the section is never matched.
    # See doc/agent_execution_rules.md section 5.
    $text = Get-Content $conf -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
    if ($text -match '(?m)^\s*\[data\]') { exit 0 }
}

$cmd = 'python tools/provision_worktree_data.py . --mode read'
$msg = "Worktree '$name' is NOT data-provisioned. Any DB or Parquet access will stop with DataNotProvisionedError. Run: $cmd (use --mode write to build a sandbox for schema/indicator/pipeline work). See doc/agent_execution_rules.md 10.3."

$ctx = "This worktree has not been data-provisioned, so backend/paths.py will raise DataNotProvisionedError on any DB or Parquet access. Before running anything that touches data, run '$cmd' for read-only tasks, or '--mode write' to build a disposable sandbox (hardlinked Parquet + copied user-asset DBs). Do NOT work around this by pointing paths at production."

$out = @{
    systemMessage = $msg
    hookSpecificOutput = @{
        hookEventName = 'SessionStart'
        additionalContext = $ctx
    }
} | ConvertTo-Json -Depth 5 -Compress
Write-Output $out
exit 0
