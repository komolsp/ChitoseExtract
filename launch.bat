@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

set "ERRLOG=%~dp0startup_error.log"
set "FALLBACK_LOG=%TEMP%\ChitoseExtract_startup.log"
set "PYW="
set "PY="

REM Prefer a project-local virtual environment.
call :try_candidate "%~dp0.venv\Scripts\python.exe"
if defined PYW goto :launch

REM 1) Registry real Python (skip aliases and incomplete environments)
for %%V in (3.13 3.12 3.11 3.10 3.9) do (
    if not defined PYW call :try_reg_python "HKCU\SOFTWARE\Python\PythonCore\%%V\InstallPath"
    if not defined PYW call :try_reg_python "HKLM\SOFTWARE\Python\PythonCore\%%V\InstallPath"
)
if defined PYW goto :launch

REM 2) PATH Python, accepted only when all application dependencies import.
for /f "delims=" %%i in ('where python 2^>nul') do (
    if not defined PYW call :try_candidate "%%~fi"
)
if defined PYW goto :launch

if not defined PYW (
    call :log_line "No compatible Python found. Python 3.9+ and requirements.txt are required."
    mshta "javascript:alert('No compatible Python was found.\nInstall Python 3.9+ and run: pip install -r requirements.txt\nSee startup_error.log or %%TEMP%%\\ChitoseExtract_startup.log');close()"
    exit /b 1
)

:launch
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

:try_reg_python
set "REGPYW="
for /f "tokens=2*" %%A in ('reg query "%~1" /v WindowedExecutablePath 2^>nul') do (
    set "REGPYW=%%~B"
)
for /f "tokens=2*" %%A in ('reg query "%~1" /v ExecutablePath 2^>nul') do (
    call :try_candidate "%%~B" "%REGPYW%"
)
if defined PYW exit /b 0
for /f "tokens=2*" %%A in ('reg query "%~1" /ve 2^>nul') do (
    call :try_candidate "%%~Bpython.exe"
)
exit /b 0

:try_candidate
if not exist "%~1" exit /b 0
for %%F in ("%~1") do if "%%~zF"=="0" exit /b 0
"%~1" "%~dp0check_runtime.py" >nul 2>&1
if errorlevel 1 exit /b 0
for %%F in ("%~1") do (
    set "PY=%%~fF"
    if exist "%%~dpFpythonw.exe" set "PYW=%%~dpFpythonw.exe"
)
if not "%~2"=="" if exist "%~2" set "PYW=%~2"
if not defined PYW set "PYW=%PY%"
exit /b 0

:log_line
>>"%ERRLOG%" 2>nul echo [%date% %time%] %~1
if errorlevel 1 >>"%FALLBACK_LOG%" echo [%date% %time%] %~1
exit /b 0
