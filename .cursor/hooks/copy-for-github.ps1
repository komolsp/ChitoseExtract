# Export changed files to github_update/ at session end (for manual GitHub upload).
# Manual run: powershell -NoProfile -ExecutionPolicy Bypass -File .cursor/hooks/copy-for-github.ps1

$ErrorActionPreference = 'Stop'

$root = Get-Location
if (-not $root) { exit 0 }

$destRoot = Join-Path $root 'github_update'
$trackFile = Join-Path $root '.cursor\session-edited-files.txt'
$excludePrefixes = @(
    'github_update\',
    '.cursor\',
    '.pytest_cache\',
    '__pycache__\',
    'build\',
    'dist\'
)

function Should-Exclude([string]$Path) {
    foreach ($prefix in $excludePrefixes) {
        if ($Path.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
    }
    return $false
}

function Find-Git {
    $cmd = Get-Command git -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $candidates = @(
        'C:\Program Files\Git\cmd\git.exe',
        'C:\Program Files\Git\bin\git.exe'
    )
    foreach ($path in $candidates) {
        if (Test-Path $path) { return $path }
    }
    return $null
}

function Get-GitChangedFiles([string]$RepoRoot) {
    $git = Find-Git
    if (-not $git) { return @() }

    $inside = & $git -C $RepoRoot rev-parse --is-inside-work-tree 2>$null
    if ($inside -ne 'true') { return @() }

    $status = & $git -C $RepoRoot status --porcelain
    if (-not $status) { return @() }

    $changed = @()
    foreach ($line in $status) {
        if ($line.Length -lt 4) { continue }
        $path = $line.Substring(3).Trim()
        if ($path -match ' -> ') {
            $path = ($path -split ' -> ', 2)[1].Trim()
        }
        $path = $path -replace '/', '\'
        if (Should-Exclude $path) { continue }
        if (Test-Path (Join-Path $RepoRoot $path) -PathType Leaf) {
            $changed += $path
        }
    }
    return $changed
}

function Get-TrackedEditedFiles([string]$RepoRoot) {
    if (-not (Test-Path $trackFile)) { return @() }
    $lines = Get-Content $trackFile -ErrorAction SilentlyContinue |
        ForEach-Object { $_.Trim() } |
        Where-Object { $_ }
    $result = @()
    foreach ($rel in $lines) {
        $rel = $rel -replace '/', '\'
        if (Should-Exclude $rel) { continue }
        if (Test-Path (Join-Path $RepoRoot $rel) -PathType Leaf) {
            $result += $rel
        }
    }
    return $result
}

$git = Find-Git
$repoRoot = $root
if ($git) {
    $detected = & $git -C $root rev-parse --show-toplevel 2>$null
    if ($detected) { $repoRoot = $detected }
}

$gitChanged = @(Get-GitChangedFiles $repoRoot)
$changed = $gitChanged
if ($changed.Count -eq 0) {
    $changed = @(Get-TrackedEditedFiles $repoRoot)
}

$changed = $changed | Sort-Object -Unique
if ($changed.Count -eq 0) { exit 0 }

if (Test-Path $destRoot) {
    Remove-Item -Recurse -Force $destRoot
}
New-Item -ItemType Directory -Force -Path $destRoot | Out-Null

foreach ($rel in $changed) {
    $src = Join-Path $repoRoot $rel
    $target = Join-Path $destRoot $rel
    $targetDir = Split-Path $target -Parent
    if (-not (Test-Path $targetDir)) {
        New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
    }
    Copy-Item -Force $src $target
}

if ($gitChanged.Count -gt 0) {
    $sourceNote = 'Source: git working tree changes'
} else {
    $sourceNote = 'Source: agent edited files in this session'
}

$readme = @(
    'GitHub update export'
    '===================='
    ''
    "Exported at: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    $sourceNote
    ''
    'Copy these files into the repo with the same paths:'
    ''
) + ($changed | ForEach-Object { "  $_" })

$readme | Set-Content -Path (Join-Path $destRoot 'README.txt') -Encoding UTF8
exit 0
