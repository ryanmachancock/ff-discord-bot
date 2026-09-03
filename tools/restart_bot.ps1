# Restarts ff-discord-bot after a code change: finds its python.exe process
# and kills it, so the pm2 service (which already auto-respawns crashed
# processes) brings it back up running the latest bot.py/image_render.py.
#
# Must run elevated -- the pm2 service runs as LocalSystem, so Stop-Process
# against its child silently fails (access denied) from a normal session.
# This script re-launches itself elevated (UAC prompt) if it isn't already.
#
# Usage: right-click > Run with PowerShell, or from a terminal:
#   powershell -ExecutionPolicy Bypass -File tools\restart_bot.ps1

$ErrorActionPreference = 'Stop'

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "Not running as Administrator -- relaunching elevated (accept the UAC prompt)..." -ForegroundColor Yellow
    Start-Process powershell -Verb RunAs -ArgumentList @('-NoExit', '-ExecutionPolicy', 'Bypass', '-File', $PSCommandPath)
    exit
}

$procs = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'")

if ($procs.Count -eq 0) {
    Write-Host "No python.exe process found -- is the bot running at all?" -ForegroundColor Red
    exit 1
}
if ($procs.Count -gt 1) {
    Write-Host "Found $($procs.Count) python.exe processes -- can't tell which one is ff-discord-bot (Windows won't show CommandLine for a LocalSystem-owned process)." -ForegroundColor Red
    Write-Host "PIDs: $($procs.ProcessId -join ', ')"
    Write-Host "Kill the right one by hand with: Stop-Process -Id <PID> -Force"
    exit 1
}

$oldPid = $procs[0].ProcessId
Write-Host "Killing ff-discord-bot (python.exe, PID $oldPid)..."
Stop-Process -Id $oldPid -Force

Write-Host "Waiting for pm2 to respawn it..."
$deadline = (Get-Date).AddSeconds(20)
$newProc = $null
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 1
    $newProc = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.ProcessId -ne $oldPid }
    if ($newProc) { break }
}

if ($newProc) {
    Write-Host "Restarted -- new PID $($newProc.ProcessId), started $($newProc.CreationDate)" -ForegroundColor Green
} else {
    Write-Host "Timed out waiting for pm2 to respawn the process -- check the 'Discord Bots PM2' service." -ForegroundColor Red
}
