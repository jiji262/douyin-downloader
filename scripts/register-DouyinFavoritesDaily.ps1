<#
.SYNOPSIS
  (Re)register DouyinFavoritesDaily for daily 03:00 local time.
  Prefer: right-click → Run with PowerShell (as Admin if Access Denied).
#>
$ErrorActionPreference = 'Stop'
$log = Join-Path $env:TEMP 'register-DouyinFavoritesDaily.log'
function Log([string]$m) {
  $line = '[{0}] {1}' -f (Get-Date -Format 's'), $m
  Add-Content -LiteralPath $log -Value $line -Encoding UTF8
  Write-Host $line
}

try {
  $script = Join-Path $PSScriptRoot 'daily-favorites.ps1'
  if (-not (Test-Path -LiteralPath $script)) { throw "Missing: $script" }

  $userId = if ($env:USERDOMAIN) { "$env:USERDOMAIN\$env:USERNAME" } else { $env:USERNAME }
  Log "Registering as UserId=$userId script=$script"

  $action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$script`""
  $trigger = New-ScheduledTaskTrigger -Daily -At '3:00AM'
  $settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -WakeToRun
  $principal = New-ScheduledTaskPrincipal `
    -UserId $userId `
    -LogonType Interactive `
    -RunLevel Limited

  $existing = Get-ScheduledTask -TaskName 'DouyinFavoritesDaily' -ErrorAction SilentlyContinue
  if ($existing) {
    Log 'Updating existing task (Set-ScheduledTask)'
    Set-ScheduledTask `
      -TaskName 'DouyinFavoritesDaily' `
      -Action $action `
      -Trigger $trigger `
      -Settings $settings `
      -Principal $principal | Out-Null
  } else {
    Log 'Creating new task (Register-ScheduledTask)'
    Register-ScheduledTask `
      -TaskName 'DouyinFavoritesDaily' `
      -Action $action `
      -Trigger $trigger `
      -Settings $settings `
      -Principal $principal `
      -Description 'Douyin favorites daily 03:00 → MCP transcript → _factory (+ ghaishu ingest)' `
      -Force | Out-Null
  }

  $t = Get-ScheduledTask -TaskName DouyinFavoritesDaily
  $i = Get-ScheduledTaskInfo $t
  Log "OK State=$($t.State) NextRun=$($i.NextRunTime) StartBoundary=$($t.Triggers[0].StartBoundary)"
  exit 0
} catch {
  Log "FAIL: $($_.Exception.Message)"
  Log $_.ScriptStackTrace
  exit 1
}
