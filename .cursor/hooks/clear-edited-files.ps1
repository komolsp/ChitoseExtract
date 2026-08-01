# 会话开始时清空本次编辑文件列表
$ErrorActionPreference = 'SilentlyContinue'

$root = Get-Location
if (-not $root) { exit 0 }

$trackFile = Join-Path $root '.cursor\session-edited-files.txt'
$cursorDir = Split-Path $trackFile -Parent
if (-not (Test-Path $cursorDir)) {
    New-Item -ItemType Directory -Force -Path $cursorDir | Out-Null
}
Set-Content -Path $trackFile -Value '' -Encoding UTF8
exit 0
