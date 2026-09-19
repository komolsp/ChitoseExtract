@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

REM 打包只使用项目自带环境，避免系统解释器带来的依赖版本漂移。
set "PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
    echo 未找到项目自带环境 .venv，已中止。请先创建：
    echo     python -m venv .venv
    echo     .venv\Scripts\python.exe -m pip install -r requirements-lock.txt
    pause
    exit /b 1
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
