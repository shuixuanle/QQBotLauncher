@echo off
rem ============================================================
rem  Start QQBot Launcher - ADMIN mode (elevated; no UAC afterwards)
rem
rem  Double-click this file, or make a shortcut to it.
rem  ONE UAC prompt appears, then the manager runs elevated and programs
rem  that need admin rights (e.g. the Hydrant bot's HttpListener) start with
rem  NO further UAC prompts, because they inherit the admin token.
rem
rem  Interpreter selection is the same as in the normal-mode script:
rem  candidates are tested with  python -c "import PyQt6"  and the first
rem  working one wins (PATH alone is not trustworthy - it can point at an
rem  interpreter without PyQt6).
rem
rem  This script waits for the manager to exit so it can report a cancelled
rem  UAC. Nothing stays on screen while the manager runs, because pythonw.exe
rem  creates no console window.
rem
rem  TIP: for a completely silent start (no console flash at all) use
rem       launch-admin.vbs
rem
rem  IMPORTANT: keep this file ASCII-only (see the normal-mode script).
rem ============================================================
setlocal
cd /d "%~dp0"

if not exist "main.py" (
    echo [ERROR] main.py not found next to this script.
    echo         Put this .bat in the QQBot Launcher folder.
    echo.
    pause
    exit /b 2
)

set "PYW="
set "PY="

call :try "%LOCALAPPDATA%\Programs\Python\Python314"
call :try "C:\Python314"
for /f "delims=" %%I in ('where python.exe 2^>nul') do call :try "%%~dpI"

if not defined PYW (
    echo [ERROR] No Python with PyQt6 was found.
    echo.
    echo         Install the dependency with:
    echo             python -m pip install -r requirements.txt
    echo.
    echo         Or edit this script and set the two paths directly.
    echo.
    pause
    exit /b 3
)

echo Using: %PY%

rem ---- already elevated? then just start, without a UAC prompt ----
rem  Real feedback: "it only said 'requesting administrator rights' and then the
rem  manager opened directly" - that happens when this script is already running
rem  elevated (or when Windows UAC is set to "never notify"), because Start-Process
rem  -Verb RunAs needs no prompt then. Checking first makes the message honest.
net session >nul 2>&1
if %errorlevel%==0 goto already_admin

echo Requesting administrator rights - please click Yes on the UAC prompt...
echo   [if Windows UAC is set to "never notify", no prompt appears - that is normal]
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $p = Start-Process -FilePath '%PYW%' -ArgumentList 'main.py','--no-elevate' -WorkingDirectory '%~dp0' -Verb RunAs -Wait -PassThru; exit $p.ExitCode } catch { exit 1223 }"
set "RC=%errorlevel%"
if not "%RC%"=="0" (
    echo.
    echo Elevation was cancelled, or the manager exited with code %RC%
    echo [1223 = the UAC prompt was cancelled]
    echo.
    pause
)
exit /b %RC%

:already_admin
echo Already running as administrator - starting directly, no UAC prompt.
"%PYW%" main.py --no-elevate
set "RC=%errorlevel%"
exit /b %RC%

rem ------------------------------------------------------------
rem  :try <folder>  - if that folder has python.exe + PyQt6, remember it
rem ------------------------------------------------------------
:try
set "D=%~1"
if "%D%"=="" exit /b 0
if not "%D:~-1%"=="\" set "D=%D%\"
if defined PYW exit /b 0
if not exist "%D%python.exe" exit /b 0
if not exist "%D%pythonw.exe" exit /b 0
"%D%python.exe" -c "import PyQt6" >nul 2>&1
if errorlevel 1 exit /b 0
set "PYW=%D%pythonw.exe"
set "PY=%D%python.exe"
exit /b 0
