@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

set "ERRLOG=%~dp0startup_error.log"
set "FALLBACK_LOG=%TEMP%\ChitoseExtract_startup.log"
set "VENV_PY=%~dp0.venv\Scripts\python.exe"
set "VENV_PYW=%~dp0.venv\Scripts\pythonw.exe"
set "PY="
set "PYW="

REM Only the project-local virtual environment is accepted. Never fall back to a
REM system interpreter: a stray Python can import every dependency while carrying
REM versions that differ from requirements-lock.txt (e.g. peewee 3.18 vs 4.2),
REM which fails silently during a run instead of here at startup.
call :try_venv
if not defined PY goto :missing_venv

if /i "%~1"=="--check" (
    echo PY=%PY%
    echo PYW=%PYW%
    exit /b 0
)
call :log_line "start: %PY% main.py"
if /i "%~1"=="--debug" (
    "%PY%" "%~dp0main.py"
    exit /b %errorlevel%
)
REM Do not use start /B: hidden console exit may kill the child (looks like no reaction)
start "" "%PYW%" "%~dp0main.py"
exit /b 0

:try_venv
if not exist "%VENV_PY%" exit /b 0
"%VENV_PY%" "%~dp0check_runtime.py" >nul 2>&1
if errorlevel 1 exit /b 0
set "PY=%VENV_PY%"
if exist "%VENV_PYW%" (set "PYW=%VENV_PYW%") else (set "PYW=%VENV_PY%")
exit /b 0

:missing_venv
REM Do not put parentheses in these values: they are assigned inside an if-block,
REM and cmd closes the block early on a stray ')'.
if exist "%VENV_PY%" (
    set "REASON=Project .venv is unusable - Python 3.9+ or dependencies missing."
    set "HINT=Repair it with:\n  .venv\\Scripts\\python.exe -m pip install -r requirements.txt"
) else (
    set "REASON=Project .venv not found."
    set "HINT=Create it first - Python 3.9+ required:\n  python -m venv .venv\n  .venv\\Scripts\\python.exe -m pip install -r requirements.txt"
)
call :log_line "%REASON%"
REM --check is a diagnostic probe: report on stderr and exit non-zero, never
REM block on a modal dialog.
if /i "%~1"=="--check" (
    echo %REASON% 1>&2
    exit /b 1
)
mshta "javascript:alert('%REASON%\n\n%HINT%\n\nSee startup_error.log');close()"
exit /b 1

:log_line
>>"%ERRLOG%" 2>nul echo [%date% %time%] %~1
if errorlevel 1 >>"%FALLBACK_LOG%" echo [%date% %time%] %~1
exit /b 0
