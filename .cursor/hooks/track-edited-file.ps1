# 记录 Agent 本次会话中编辑过的文件路径（不依赖 git）
$ErrorActionPreference = 'SilentlyContinue'

$inputText = [Console]::In.ReadToEnd()
if (-not $inputText) { exit 0 }

try {
    $payload = $inputText | ConvertFrom-Json
} catch {
    exit 0
}

$path = $null
foreach ($key in @('file_path', 'filePath', 'path', 'edited_file', 'editedFile')) {
    if ($payload.PSObject.Properties.Name -contains $key -and $payload.$key) {
        $path = [string]$payload.$key
        break
    }
}
if (-not $path) { exit 0 }

$root = Get-Location
if (-not $root) { exit 0 }

$normalized = $path -replace '/', '\'
if ([System.IO.Path]::IsPathRooted($normalized)) {
    try {
        $normalized = [System.IO.Path]::GetRelativePath($root, $normalized)
    } catch {
        exit 0
    }
}

$excludePrefixes = @(
    'github_update\',
    '.cursor\',
    '.pytest_cache\',
    '__pycache__\',
    'build\',
    'dist\'
)
foreach ($prefix in $excludePrefixes) {
    if ($normalized.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        exit 0
    }
}

$trackFile = Join-Path $root '.cursor\session-edited-files.txt'
$cursorDir = Split-Path $trackFile -Parent
if (-not (Test-Path $cursorDir)) {
    New-Item -ItemType Directory -Force -Path $cursorDir | Out-Null
}

$existing = @()
if (Test-Path $trackFile) {
    $existing = Get-Content $trackFile -ErrorAction SilentlyContinue
}
if ($existing -notcontains $normalized) {
    Add-Content -Path $trackFile -Value $normalized -Encoding UTF8
}
exit 0
