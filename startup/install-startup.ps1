<#
.SYNOPSIS
    Start the AULA F75 Max host layer at logon. Idempotent.

.DESCRIPTION
    Creates a shortcut in the current user's Startup folder that launches
    services\hostlayer\engine.ahk with AutoHotkey v2, hidden so logon does not
    flash a terminal window.

    A shortcut is used rather than a Run-key entry or a scheduled task because
    it needs no admin rights, survives on a managed machine, and the user can
    see and delete it. .ahk files have no file association here, so the
    shortcut targets AutoHotkey64.exe directly and passes the script as an
    argument.

    Running this twice is safe: an existing shortcut that already points at the
    right target is left alone, and one that points somewhere else is rewritten.

    The config is validated before anything is installed. Wiring a broken config
    into logon is how you end up with a dialog box you cannot dismiss.

.PARAMETER Force
    Rewrite the shortcut even when it already matches.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File startup\install-startup.ps1
#>

[CmdletBinding()]
param(
    [string]$AutoHotkey = "C:\Program Files\AutoHotkey\v2\AutoHotkey64.exe",
    [string]$Config,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$repo     = Split-Path -Parent $PSScriptRoot
$service  = Join-Path $repo "services\hostlayer"
$engine   = Join-Path $service "engine.ahk"
$linkName = "AULA F75 Max Host Layer.lnk"
$startup  = [Environment]::GetFolderPath("Startup")
$linkPath = Join-Path $startup $linkName

if (-not $Config) { $Config = Join-Path $service "layers.json" }

# ---------------------------------------------------------------- preflight
if (-not (Test-Path -LiteralPath $AutoHotkey)) {
    Write-Host "AutoHotkey v2 not found at: $AutoHotkey" -ForegroundColor Red
    Write-Host "Install AutoHotkey v2, or pass -AutoHotkey <path>."
    exit 1
}
if (-not (Test-Path -LiteralPath $engine)) {
    Write-Host "engine.ahk not found at: $engine" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path -LiteralPath $Config)) {
    Write-Host "Config not found at: $Config" -ForegroundColor Red
    Write-Host "Copy layers.example.json to layers.json first."
    exit 1
}

Write-Host "Validating config before install..." -ForegroundColor Cyan
$tmpOut = [System.IO.Path]::GetTempFileName()
$tmpErr = [System.IO.Path]::GetTempFileName()
$v = Start-Process -FilePath $AutoHotkey `
                   -ArgumentList @("`"$engine`"", "/validate", "`"$Config`"") `
                   -NoNewWindow -Wait -PassThru `
                   -RedirectStandardOutput $tmpOut -RedirectStandardError $tmpErr
Get-Content $tmpOut -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "  $_" }
Get-Content $tmpErr -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "  $_" }
Remove-Item $tmpOut, $tmpErr -Force -ErrorAction SilentlyContinue
if ($v.ExitCode -ne 0) {
    Write-Host "Config is invalid. Nothing was installed." -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------- shortcut
$targetArgs = "`"$engine`""
if ($Config -ne (Join-Path $service "layers.json")) {
    $targetArgs = "`"$engine`" `"$Config`""
}

$shell = New-Object -ComObject WScript.Shell

if ((Test-Path -LiteralPath $linkPath) -and (-not $Force)) {
    $existing = $shell.CreateShortcut($linkPath)
    if ($existing.TargetPath -eq $AutoHotkey -and $existing.Arguments -eq $targetArgs) {
        Write-Host "Already installed and up to date:" -ForegroundColor Green
        Write-Host "  $linkPath"
        Write-Host "Nothing to do."
        exit 0
    }
    Write-Host "Existing shortcut points elsewhere, rewriting it." -ForegroundColor Yellow
}

$sc = $shell.CreateShortcut($linkPath)
$sc.TargetPath       = $AutoHotkey
$sc.Arguments        = $targetArgs
$sc.WorkingDirectory = $service
$sc.Description      = "AULA F75 Max host layer (F13-F15/F18-F24 bindings and the num layer)"
$sc.IconLocation     = "$AutoHotkey,0"
$sc.WindowStyle      = 7
$sc.Save()

Write-Host ""
Write-Host "Installed." -ForegroundColor Green
Write-Host "  shortcut  $linkPath"
Write-Host "  target    $AutoHotkey"
Write-Host "  arguments $targetArgs"
Write-Host ""
Write-Host "It starts at your next logon. To start it now:"
Write-Host "  & `"$AutoHotkey`" `"$engine`""
Write-Host ""
Write-Host "To remove it:  powershell -ExecutionPolicy Bypass -File startup\uninstall-startup.ps1"
exit 0
