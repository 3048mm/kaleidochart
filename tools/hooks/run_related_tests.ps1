# PostToolUse hook: after editing backend/*.py, run that module's mirrored test.
#
# Why: the only automatic verification signal in this repo is the agent choosing
# to run pytest. There is no CI (doc/issue_list.md P2). This closes the smallest
# useful part of that gap: touch a module, see its own test result immediately.
#
# It reports and never blocks. A half-finished edit is expected to fail, and a
# TDD red phase MUST be able to fail on purpose.
#
# It also reports when NO test exists, on purpose. 86 of 138 backend modules have
# no mirrored test; a hook that silently does nothing there would leave the
# impression of a gate while checking nothing. Measured on git history
# (2026-09-10): about 48% of real backend edits land on an untested module, so
# this branch is roughly as common as actually running a test.
#
# NOTE: keep this file ASCII-only. Windows PowerShell 5.1 reads BOM-less files
#       as ANSI (CP932), and multibyte text corrupts parsing.
# NOTE: no backslash string literals anywhere. They get mangled when this file is
#       written through a shell (doc/agent_execution_rules.md sec.1.3), so paths
#       are built with Join-Path and [IO.Path] members instead.

$TimeoutSeconds = 60

# Read stdin as UTF-8 EXPLICITLY. Do not use [Console]::In.ReadToEnd(): it decodes
# with the console's ANSI codepage (CP932 here), and CP932 mis-decoding swallows
# backslashes -- the second byte of many kana/kanji is 0x5C. That turns the JSON
# newline escape into a bare letter n, breaks the string structure, and then
# ConvertFrom-Json throws:
#   PARSE FAILED: ':' ... '}' ... (3199)
# The hook then exits 0 and stays completely silent. Because CLAUDE.md requires
# Japanese comments, essentially every backend edit payload contains Japanese, so
# this made the hook a no-op in live use while hand-made ASCII test payloads on
# stdin kept passing. Diagnosed 2026-09-10 (see doc/agent_execution_rules.md).
$stdin = [Console]::OpenStandardInput()
$reader = New-Object System.IO.StreamReader($stdin, (New-Object System.Text.UTF8Encoding($false)))
$raw = $reader.ReadToEnd()
$reader.Dispose()
try { $j = $raw | ConvertFrom-Json } catch { exit 0 }

$filePath = $j.tool_input.file_path
if (-not $filePath) { exit 0 }
if (-not ($filePath.ToLower().EndsWith('.py'))) { exit 0 }

$sep = [IO.Path]::DirectorySeparatorChar
$alt = [IO.Path]::AltDirectorySeparatorChar

# --- locate the main checkout (a worktree has no venv of its own) --------------
$here = Split-Path $PSScriptRoot -Parent          # tools/
$repo = Split-Path $here -Parent                  # checkout containing this script
$gitCommon = git -C $repo rev-parse --path-format=absolute --git-common-dir 2>$null
$mainRoot = if ($gitCommon) { Split-Path $gitCommon -Parent } else { $repo }

# --- normalise the edited path to a repo-relative, slash-separated form --------
try { $full = [IO.Path]::GetFullPath($filePath) } catch { exit 0 }
$repoFull = [IO.Path]::GetFullPath($repo)
if (-not $full.ToLower().StartsWith($repoFull.ToLower())) { exit 0 }
$rel = $full.Substring($repoFull.Length).TrimStart($sep, $alt)
$relSlash = $rel.Replace($sep, $alt)

if (-not $relSlash.StartsWith('backend/')) { exit 0 }
if ($relSlash.EndsWith('/__init__.py')) { exit 0 }

$testsRoot = Join-Path (Join-Path $repo 'backend') 'tests'

# --- resolve which test file to run -------------------------------------------
$target = $null
$note = ''
if ($relSlash.StartsWith('backend/tests/')) {
    # The edited file IS a test. Run it directly.
    $target = $full
    $note = 'edited test file'
} else {
    $leaf = [IO.Path]::GetFileName($relSlash)
    $testName = 'test_' + $leaf
    # mirrored location: backend/x/y.py -> backend/tests/x/test_y.py
    $inner = $relSlash.Substring('backend/'.Length)
    $dirPart = [IO.Path]::GetDirectoryName($inner)
    $mirrored = if ($dirPart) { Join-Path (Join-Path $testsRoot $dirPart) $testName }
                else { Join-Path $testsRoot $testName }
    if (Test-Path $mirrored) {
        $target = $mirrored
        $note = 'mirrored test'
    } else {
        # fall back to the same filename anywhere under backend/tests
        $found = @(Get-ChildItem $testsRoot -Recurse -File -Filter $testName -ErrorAction SilentlyContinue)
        if ($found.Count -gt 0) {
            $target = $found[0].FullName
            $note = 'same-name test in another directory'
        }
    }
}

function Emit($systemMsg, $context) {
    $out = @{
        systemMessage = $systemMsg
        hookSpecificOutput = @{
            hookEventName = 'PostToolUse'
            additionalContext = $context
        }
    } | ConvertTo-Json -Depth 5 -Compress
    Write-Output $out
}

# --- no test: say so, do not stay silent --------------------------------------
if (-not $target) {
    $expected = 'backend/tests/' + $relSlash.Substring('backend/'.Length)
    $expected = $expected.Replace($leaf, $testName)
    Emit ("No test for " + $relSlash) (
        "NO TEST EXISTS for the module just edited (" + $relSlash + "). " +
        "Nothing was verified automatically. The mirrored path would be " + $expected + ". " +
        "This is not an error: 86 of 138 backend modules have no test. " +
        "If this module is worth a test, say so in the completion report rather than adding one silently mid-task.")
    exit 0
}

# --- run just that test file, with a timeout ----------------------------------
$py = Join-Path (Join-Path (Join-Path $mainRoot 'venv') 'Scripts') 'python.exe'
if (-not (Test-Path $py)) { exit 0 }

# pytest is given the REPO-RELATIVE path, not the absolute one. The repository
# path contains spaces, and Start-Process -ArgumentList joins the array on spaces
# without quoting, so an absolute path arrives split in two and pytest reports
# "file or directory not found: D:/My". The relative path has no spaces.
$relTarget = $target
try {
    $tFull = [IO.Path]::GetFullPath($target)
    if ($tFull.ToLower().StartsWith($repoFull.ToLower())) {
        $relTarget = $tFull.Substring($repoFull.Length).TrimStart($sep, $alt).Replace($sep, $alt)
    }
} catch {}
$pytestArg = if ($relTarget -ne $target) { $relTarget } else { '"' + $target + '"' }

# System.Diagnostics.Process, not Start-Process. In Windows PowerShell 5.1,
# `Start-Process -PassThru` returns an object whose ExitCode stays EMPTY even
# after HasExited is true (it is only populated when -Wait is used, and -Wait
# takes no timeout). Reading it gave "$null -eq 0" = false, which reported every
# passing run as FAILED. Measured 2026-09-10.
# Output is read asynchronously: reading one stream to the end while the other
# fills its buffer deadlocks.
$timedOut = $false
$code = -1
$text = ''
try {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $py
    $psi.Arguments = '-m pytest ' + $pytestArg + ' -q --no-header -p no:cacheprovider'
    $psi.WorkingDirectory = $repo
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true

    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi
    $null = $proc.Start()
    $outTask = $proc.StandardOutput.ReadToEndAsync()
    $errTask = $proc.StandardError.ReadToEndAsync()

    if (-not $proc.WaitForExit($TimeoutSeconds * 1000)) {
        $timedOut = $true
        try { $proc.Kill() } catch {}
        $proc.WaitForExit(5000) | Out-Null
    } else {
        $code = $proc.ExitCode
    }
    try { $text = $outTask.Result + "`n" + $errTask.Result } catch {}
    $proc.Dispose()
} catch {
    exit 0
}

if ($timedOut) {
    Emit ("Related test timed out: " + $relTarget) (
        "The test for the module just edited (" + $relTarget + ") did not finish within " +
        $TimeoutSeconds + " seconds and was killed. Nothing was verified. " +
        "Run it yourself if the result matters: pytest " + $relTarget)
    exit 0
}

# keep the summary short: the tail of pytest -q is the counts line
$lines = @($text -split "`r?`n" | Where-Object { $_.Trim() -ne '' })
$summary = if ($lines.Count -gt 0) { $lines[-1].Trim() } else { '(no output)' }
$detail = if ($lines.Count -gt 12) { ($lines[-12..-1] -join "`n") } else { ($lines -join "`n") }

if ($code -eq 0) {
    Emit ("Related test passed: " + $relTarget + "  [" + $summary + "]") (
        "Ran the " + $note + " for the file just edited: " + $relTarget + " -> PASSED (" + $summary + ").")
} else {
    Emit ("Related test FAILED: " + $relTarget + "  [" + $summary + "]") (
        "Ran the " + $note + " for the file just edited: " + $relTarget + " -> FAILED (" + $summary + "). " +
        "This is informational and does not block. A red phase in TDD, or a half-finished edit, " +
        "is expected to fail here. Do not 'fix' it reflexively; decide whether this failure is intended.`n" +
        "--- tail of pytest output ---`n" + $detail)
}
exit 0
