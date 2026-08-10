<#
.SYNOPSIS
  Daily favorites ingest: Douyin collect increase → MCP transcript hook → _factory index.

.DESCRIPTION
  Production scheduler entry (D1). Reuses %APPDATA%\Douzy\config.yml + .cookies.json
  and media root G:\media\douyin. Built-in Douzy transcript stays false; MCP extract
  runs via mcp_transcript.enabled in the merged overlay.

  API key (D6): prefer process env DOUYIN_API_KEY, else API_KEY / OPENAI_API_KEY.
  If still empty, optionally load Douzy transcript.api_key into *this process only*
  (never written to git / logs).

  Exit codes (business-visible; scheduler LastTaskResult):
    0  — CLI ok, today's delta present, transcripts not all-failed, ingest ok
    2  — media root missing/unwritable
    3  — Douzy cookies/config missing
    4  — repo/venv/overlay missing
    5  — merge config failed
    6  — today's delta missing or empty after CLI (projection would no-op)
    7  — delta present but every new row transcript_status=failed
    8  — ingest failed / wrote LAST_DEGRADED
    other — CLI non-zero passthrough

.NOTES
  Scheduler: daily 03:00 local. Register/update via:
    scripts\register-DouyinFavoritesDaily.ps1
  (admin PowerShell if Access Denied). See scripts\SCHEDULED-TASK.md.

  Cookie renewal: only maintain %APPDATA%\Douzy\.cookies.json (Douzy login or cookie_fetcher).
#>
[CmdletBinding()]
param(
    [switch]$SkipIngest,
    [int]$CollectLimit = 20,
    [string]$MediaRoot = 'G:\media\douyin',
    [string]$RepoRoot = 'D:\dev\github\douyin-downloader',
    [string]$GhaishuRoot = 'D:\ghaishu'
)

$ErrorActionPreference = 'Stop'
$DouzyDir = Join-Path $env:APPDATA 'Douzy'
$DouzyConfig = Join-Path $DouzyDir 'config.yml'
$DouzyCookies = Join-Path $DouzyDir '.cookies.json'
$DouzyDb = Join-Path $DouzyDir 'dy_downloader.db'
$OverlaySrc = Join-Path $RepoRoot 'config.automation.yml'
$LogDir = Join-Path $env:LOCALAPPDATA 'douyin-downloader-daily\logs'
$FactoryDir = Join-Path $MediaRoot '_factory'
$day = Get-Date -Format 'yyyyMMdd'
$localDate = Get-Date -Format 'yyyy-MM-dd'
$logFile = Join-Path $LogDir "$day.log"

function Write-Log([string]$Message) {
    $line = '[{0}] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Add-Content -LiteralPath $logFile -Value $line -Encoding UTF8
    Write-Host $line
}

function Write-Degraded([string]$Reason, [int]$Code) {
    $degraded = Join-Path $FactoryDir 'LAST_DEGRADED'
    $payload = @(
        "ts=$((Get-Date).ToString('o'))"
        "local_date=$localDate"
        "code=$Code"
        "reason=$Reason"
    ) -join "`n"
    Set-Content -LiteralPath $degraded -Value $payload -Encoding utf8
    Write-Log "LAST_DEGRADED written: $degraded ($Reason)"
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
New-Item -ItemType Directory -Force -Path $FactoryDir | Out-Null

Write-Log '=== daily-favorites start ==='

if (-not (Test-Path -LiteralPath $MediaRoot)) {
    Write-Log "FAIL: media root missing or unmapped: $MediaRoot"
    exit 2
}
try {
    $probe = Join-Path $FactoryDir ('.write_probe_{0}' -f $PID)
    Set-Content -LiteralPath $probe -Value 'ok' -Encoding ascii
    Remove-Item -LiteralPath $probe -Force
} catch {
    Write-Log "FAIL: media root not writable: $MediaRoot ($_)"
    exit 2
}

if (-not (Test-Path -LiteralPath $DouzyCookies)) {
    Write-Log "FAIL: Douzy cookies missing: $DouzyCookies"
    exit 3
}
if (-not (Test-Path -LiteralPath $DouzyConfig)) {
    Write-Log "FAIL: Douzy config missing: $DouzyConfig"
    exit 3
}
if (-not (Test-Path -LiteralPath $OverlaySrc)) {
    Write-Log "FAIL: automation overlay missing: $OverlaySrc"
    exit 4
}

$venvPython = Join-Path $RepoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Log "FAIL: venv python missing: $venvPython"
    exit 4
}

# API key: env first; else Douzy transcript.api_key into this process only
$hasKey = @('DOUYIN_API_KEY', 'API_KEY', 'OPENAI_API_KEY') | Where-Object {
    -not [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($_))
}
if (-not $hasKey) {
    $reader = Join-Path $RepoRoot 'tools\_read_douzy_api_key_once.py'
    if (Test-Path -LiteralPath $reader) {
        $loaded = & $venvPython $reader 2>$null
        if ($loaded) {
            $env:DOUYIN_API_KEY = $loaded
            Write-Log 'API key: loaded from Douzy config into process env (not logged)'
        } else {
            Write-Log 'WARN: no API key in env or Douzy config; transcripts will fail with .err.txt'
        }
    }
} else {
    Write-Log ("API key: using env {0}" -f ($hasKey -join ','))
}

$env:DOUYIN_MEDIA_ROOT = $MediaRoot
# Put merged config beside Douzy cookies so cookie: auto finds .cookies.json
$mergedConfig = Join-Path $DouzyDir 'config.daily.generated.yml'
$mergeScript = Join-Path $RepoRoot 'tools\merge_daily_config.py'
& $venvPython $mergeScript $DouzyConfig $OverlaySrc $mergedConfig $DouzyDb $CollectLimit
if ($LASTEXITCODE -ne 0) {
    Write-Log "FAIL: merge config exit $LASTEXITCODE"
    exit 5
}
Write-Log "merged config: $mergedConfig"

Push-Location $RepoRoot
try {
    Write-Log 'running CLI collect increase…'
    & $venvPython -m cli.main --config $mergedConfig
    $cliExit = $LASTEXITCODE
} finally {
    Pop-Location
}

if ($cliExit -ne 0) {
    Write-Log "FAIL: CLI exit $cliExit"
    exit $cliExit
}

# Business gate: local-day delta must exist and not be all-failed.
$deltaPath = Join-Path $FactoryDir "delta-$localDate.jsonl"
if (-not (Test-Path -LiteralPath $deltaPath)) {
    Write-Degraded -Reason "missing_delta:$deltaPath" -Code 6
    Remove-Item -LiteralPath $mergedConfig -Force -ErrorAction SilentlyContinue
    Write-Log "FAIL: no local-day delta after CLI: $deltaPath"
    exit 6
}

$statuses = @()
Get-Content -LiteralPath $deltaPath -Encoding UTF8 | ForEach-Object {
    $line = $_.Trim()
    if (-not $line) { return }
    try {
        $obj = $line | ConvertFrom-Json
        if ($obj.transcript_status) { $statuses += [string]$obj.transcript_status }
    } catch { }
}
if ($statuses.Count -eq 0) {
    Write-Degraded -Reason "empty_delta:$deltaPath" -Code 6
    Remove-Item -LiteralPath $mergedConfig -Force -ErrorAction SilentlyContinue
    Write-Log "FAIL: delta empty: $deltaPath"
    exit 6
}

$failedCount = @($statuses | Where-Object { $_ -eq 'failed' }).Count
$okCount = @($statuses | Where-Object { $_ -eq 'ok' -or $_ -eq 'skipped' }).Count
Write-Log "delta $localDate rows=$($statuses.Count) ok_or_skipped=$okCount failed=$failedCount"

if ($okCount -eq 0 -and $failedCount -gt 0) {
    Write-Degraded -Reason "all_transcripts_failed:failed=$failedCount" -Code 7
    Remove-Item -LiteralPath $mergedConfig -Force -ErrorAction SilentlyContinue
    Write-Log "FAIL: all transcript rows failed ($failedCount); refusing success exit"
    exit 7
}

$lastOk = Join-Path $FactoryDir 'LAST_OK'
Set-Content -LiteralPath $lastOk -Value ((Get-Date).ToString('o')) -Encoding ascii
$degradedPath = Join-Path $FactoryDir 'LAST_DEGRADED'
if (Test-Path -LiteralPath $degradedPath) {
    Remove-Item -LiteralPath $degradedPath -Force -ErrorAction SilentlyContinue
}
Write-Log "LAST_OK written: $lastOk"

if (-not $SkipIngest) {
    $ingest = Join-Path $GhaishuRoot '.scripts\ingest-douyin-delta.ps1'
    if (Test-Path -LiteralPath $ingest) {
        Write-Log "calling ingest-douyin-delta.ps1 -Date $localDate"
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $ingest -Date $localDate
        if ($LASTEXITCODE -ne 0) {
            Write-Degraded -Reason "ingest_exit:$LASTEXITCODE" -Code 8
            Remove-Item -LiteralPath $mergedConfig -Force -ErrorAction SilentlyContinue
            Write-Log "FAIL: ingest exit $LASTEXITCODE"
            exit 8
        }
    } else {
        Write-Log "ingest script not found yet: $ingest"
    }
}

Remove-Item -LiteralPath $mergedConfig -Force -ErrorAction SilentlyContinue
Write-Log '=== daily-favorites ok ==='
exit 0
