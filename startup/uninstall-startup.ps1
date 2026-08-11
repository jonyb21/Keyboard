<#
.SYNOPSIS
    Remove the AULA F75 Max host layer from logon startup. Idempotent.

.DESCRIPTION
    Deletes the Startup-folder shortcut created by install-startup.ps1. Says so
    plainly when there is nothing to remove.

    By default the running engine is left alone, so removing the shortcut does
    not yank the keyboard out from under you mid-task. Pass -StopRunning to also
    close it.

.PARAMETER StopRunning
    Also stop any AutoHotkey process currently running engine.ahk.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File startup\uninstall-startup.ps1
#>

[CmdletBinding()]
param(
    [switch]$StopRunning
)

$ErrorActionPreference = "Stop"

$repo     = Split-Path -Parent $PSScriptRoot
$service  = Join-Path $repo "services\hostlayer"
$engine   = Join-Path $service "engine.ahk"
$linkName = "AULA F75 Max Host Layer.lnk"
$startup  = [Environment]::GetFolderPath("Startup")
$linkPath = Join-Path $startup $linkName

if (Test-Path -LiteralPath $linkPath) {
    Remove-Item -LiteralPath $linkPath -Force
    Write-Host "Removed startup shortcut:" -ForegroundColor Green
    Write-Host "  $linkPath"
} else {
    Write-Host "No startup shortcut found at:" -ForegroundColor Yellow
    Write-Host "  $linkPath"
    Write-Host "Nothing to remove."
}

if ($StopRunning) {
    # Match on the command line so other AutoHotkey scripts on this machine
    # (the CIDOO runtime, for one) are never touched.
    $procs = Get-CimInstance Win32_Process -Filter "Name='AutoHotkey64.exe'" |
             Where-Object { $_.CommandLine -and $_.CommandLine -like "*engine.ahk*" -and $_.CommandLine -like "*hostlayer*" }
    if ($procs) {
        foreach ($p in $procs) {
            Stop-Process -Id $p.ProcessId -Force
            Write-Host ("Stopped running engine (pid {0})." -f $p.ProcessId) -ForegroundColor Green
        }
    } else {
        Write-Host "No running engine.ahk process found."
    }
} else {
    Write-Host ""
    Write-Host "The engine may still be running. Exit it from its tray icon, or re-run with -StopRunning."
}

exit 0
