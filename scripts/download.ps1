# 下载一条抖音链接
# 用法：.\scripts\download.ps1 -Url "https://v.douyin.com/xxxx"
param(
    [Parameter(Mandatory = $true)]
    [string]$Url,
    [string]$Path = ".\Downloaded",
    [int]$Thread = 5
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$py = Join-Path $PWD ".venv\Scripts\python.exe"
& $py run.py -c config.yml -u $Url -p $Path -t $Thread -v
exit $LASTEXITCODE
