# 从 PowerShell 调用 MSYS2 编译 minimal ffmpeg。
# 用法：powershell -ExecutionPolicy Bypass -File scripts/build_minimal_ffmpeg.ps1

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot

function Find-MsysLauncher {
    param([string]$MsysRoot)
    $ucrt = Join-Path $MsysRoot 'ucrt64.exe'
    if (Test-Path $ucrt) { return $ucrt }
    $mingw = Join-Path $MsysRoot 'mingw64.exe'
    if (Test-Path $mingw) { return $mingw }
    return $null
}

$msysRoots = @(
    'C:\msys64',
    'C:\tools\msys64',
    "$env:ProgramFiles\msys64",
    "$env:LOCALAPPDATA\msys64"
)

$launcher = $msysRoots | ForEach-Object { Find-MsysLauncher $_ } | Where-Object { $_ } | Select-Object -First 1
if (-not $launcher) {
    Write-Error @"
未找到 MSYS2。请先安装 https://www.msys2.org/ ，并在 MINGW64/UCRT64 中安装工具链：

  pacman -S --needed mingw-w64-ucrt-x86_64-gcc mingw-w64-ucrt-x86_64-nasm mingw-w64-ucrt-x86_64-yasm `
           mingw-w64-ucrt-x86_64-make mingw-w64-ucrt-x86_64-pkg-config curl tar xz
"@
}

$rootPosix = ($Root -replace '\\', '/')
$script = "$rootPosix/scripts/build_minimal_ffmpeg.sh"
Write-Host "使用 MSYS2 编译 minimal ffmpeg ..."
Write-Host "项目目录：$Root"
& $launcher -lc "bash '$script'"

if (-not (Test-Path (Join-Path $Root 'ffmpeg-minimal\ffmpeg.exe'))) {
    Write-Error '编译失败：未生成 ffmpeg-minimal\ffmpeg.exe'
}

Write-Host 'minimal ffmpeg 已生成：ffmpeg-minimal\ffmpeg.exe'
