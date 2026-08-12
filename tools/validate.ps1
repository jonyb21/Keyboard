<#
.SYNOPSIS
    Repository gate lane. One command, seven checks, exit 0 or 1.

.DESCRIPTION
    1. AutoHotkey syntax gate on every .ahk file (load errors, not logic).
    2. engine.ahk /validate on every shipped config.
    3. tests/hotkeynames.ahk  - proves the hotkey names those configs compile
       to are ones AutoHotkey actually accepts. /validate cannot cover this
       because it deliberately registers nothing.
    4. The hostlayer Python mirror suite.
    5. The HID protocol/remap Python suite.
    6. The read-only device-audit Python suite.
    7. Repository integration contracts.

    Deterministic, offline, no device. /ErrorStdOut is mandatory on the syntax
    gate: without it a load error opens a modal dialog and hangs forever.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\validate.ps1
#>

[CmdletBinding()]
param(
    [string]$AutoHotkey = "C:\Program Files\AutoHotkey\v2\AutoHotkey64.exe"
)

$ErrorActionPreference = "Stop"

$repo    = Split-Path -Parent $PSScriptRoot
$service = Join-Path $repo "services\hostlayer"
$tmp     = Join-Path ([System.IO.Path]::GetTempPath()) ("hostlayer-validate-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tmp -Force | Out-Null

$script:failures = 0
$script:step = 0

function Write-Head($text) {
    Write-Host ""
    Write-Host "== $text" -ForegroundColor Cyan
}

function Read-TextOrEmpty {
    # Get-Content -Raw yields $null for a 0-byte file, and every caller here
    # goes on to call .Trim() on the result. Always hand back a real string.
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return "" }
    $t = Get-Content -LiteralPath $Path -Raw -ErrorAction SilentlyContinue
    if ($null -eq $t) { return "" }
    return [string]$t
}

function Invoke-Step {
    param(
        [string]$Name,
        [string]$Exe,
        [string[]]$Arguments
    )
    $script:step++
    $out = Join-Path $tmp ("step{0}.out" -f $script:step)
    $err = Join-Path $tmp ("step{0}.err" -f $script:step)
    $p = Start-Process -FilePath $Exe -ArgumentList $Arguments -NoNewWindow -Wait -PassThru `
                       -RedirectStandardOutput $out -RedirectStandardError $err
    $stdout = Read-TextOrEmpty $out
    $stderr = Read-TextOrEmpty $err

    if ($p.ExitCode -eq 0) {
        Write-Host ("  PASS  " + $Name) -ForegroundColor Green
        if ($stdout.Trim()) { $stdout.TrimEnd() -split "`r?`n" | ForEach-Object { Write-Host "        $_" } }
    } else {
        Write-Host ("  FAIL  " + $Name + "  (exit " + $p.ExitCode + ")") -ForegroundColor Red
        if ($stdout.Trim()) { $stdout.TrimEnd() -split "`r?`n" | ForEach-Object { Write-Host "        $_" } }
        if ($stderr.Trim()) { $stderr.TrimEnd() -split "`r?`n" | ForEach-Object { Write-Host "        $_" } }
        $script:failures++
    }
}

if (-not (Test-Path -LiteralPath $AutoHotkey)) {
    Write-Host "AutoHotkey v2 not found at: $AutoHotkey" -ForegroundColor Red
    Write-Host "Install it or pass -AutoHotkey <path>."
    exit 1
}

Write-Host "hostlayer gate lane" -ForegroundColor White
Write-Host "  repo    $repo"
Write-Host "  ahk     $AutoHotkey"

# ---------------------------------------------------------------- 1. syntax
Write-Head "AutoHotkey syntax gate"
foreach ($f in @(
    "core.ahk",
    "engine.ahk",
    "lib\jsonparse.ahk",
    "tests\hotkeynames.ahk",
    "tests\probe.ahk"
)) {
    $full = Join-Path $service $f
    Invoke-Step -Name $f -Exe $AutoHotkey -Arguments @("/validate", "/ErrorStdOut", "`"$full`"")
}

# ---------------------------------------------------------------- 2. configs
Write-Head "Config validation (engine.ahk /validate)"
$engine = Join-Path $service "engine.ahk"
foreach ($cfg in @("layers.json", "layers.example.json")) {
    $full = Join-Path $service $cfg
    Invoke-Step -Name $cfg -Exe $AutoHotkey -Arguments @("`"$engine`"", "/validate", "`"$full`"")
}

# ------------------------------------------------------------ 3. hotkey names
Write-Head "Hotkey name acceptance"
$hk = Join-Path $service "tests\hotkeynames.ahk"
Invoke-Step -Name "hotkeynames.ahk" -Exe $AutoHotkey -Arguments @("`"$hk`"")

# ---------------------------------------------------------------- 4. python
Write-Head "Python mirror suite"
$python = "py"
if (-not (Get-Command $python -ErrorAction SilentlyContinue)) { $python = "python" }
Push-Location $service
try {
    Invoke-Step -Name "unittest discover" -Exe $python -Arguments @("-m", "unittest", "discover", "-s", "tests")
} finally {
    Pop-Location
}

# ------------------------------------------------------------ 5. HID service
Write-Head "HID protocol/remap suite"
Push-Location $repo
try {
    Invoke-Step -Name "services/hid tests" -Exe $python -Arguments @(
        "-m", "unittest", "discover", "-s", "services/hid/tests", "-t", "."
    )
} finally {
    Pop-Location
}

# --------------------------------------------------------- 6. device audit
Write-Head "Device-audit suite"
Push-Location $repo
try {
    Invoke-Step -Name "services/device_audit tests" -Exe $python -Arguments @(
        "-m", "unittest", "discover", "-s", "services/device_audit/tests", "-t", "."
    )
} finally {
    Pop-Location
}

# --------------------------------------------------------- 7. integration
Write-Head "Repository integration suite"
Push-Location $repo
try {
    Invoke-Step -Name "integration tests" -Exe $python -Arguments @(
        "-m", "unittest", "discover", "-s", "tests", "-t", "."
    )
} finally {
    Pop-Location
}

# ---------------------------------------------------------------- verdict
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
Write-Host ""
if ($script:failures -eq 0) {
    Write-Host "ALL CHECKS PASSED" -ForegroundColor Green
    exit 0
}
Write-Host ("$($script:failures) CHECK(S) FAILED") -ForegroundColor Red
exit 1
