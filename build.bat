@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

set "PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
    for /f "delims=" %%i in ('where python 2^>nul') do if not defined FALLBACK_PY set "FALLBACK_PY=%%~fi"
    if not defined FALLBACK_PY (
        echo 未找到 Python。请先创建 .venv 或安装 Python 3.9+。
        pause
        exit /b 1
    )
    set "PYTHON=%FALLBACK_PY%"
)

echo 使用 Python: %PYTHON%
echo 正在安装锁定的发布依赖...
"%PYTHON%" -m pip install -r requirements-lock.txt
if errorlevel 1 (
    echo 依赖安装失败。
    pause
    exit /b 1
)

echo 正在运行打包前检查...
"%PYTHON%" check_runtime.py
if errorlevel 1 (
    echo 运行环境检查失败。
    pause
    exit /b 1
)

echo 开始打包 ChitoseExtract.exe...
"%PYTHON%" build.py %*
if errorlevel 1 (
    echo 打包或打包后自检失败。
    pause
    exit /b 1
)

echo.
echo 完成。启动文件：dist\ChitoseExtract.exe
pause
