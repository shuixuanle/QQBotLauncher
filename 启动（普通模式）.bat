@echo off
rem ============================================================
rem  Start QQBot Launcher - NORMAL mode (no admin rights)
rem
rem  Double-click this file, or make a shortcut to it.
rem  The manager is started with pythonw.exe, so no console window stays.
rem
rem  How the interpreter is chosen (important):
rem    This script tries candidates in order and, for EACH one, runs
rem        python -c "import PyQt6"
rem    then launches the first candidate that actually has PyQt6.
rem    Reason: PATH may resolve pythonw.exe to an interpreter WITHOUT PyQt6
rem    (real incident: it picked a conda python without PyQt6, and the
rem    manager died instantly with ModuleNotFoundError).
rem
rem  Use this mode if no managed program needs admin rights.
rem  If one does (e.g. the Hydrant bot's HttpListener), Windows shows a UAC
rem  prompt each time you start that program - use the admin script instead.
rem
rem  Related files in this folder:
rem    launch-normal.vbs     same thing, but with ZERO console window
rem    launch-admin.bat / launch-admin.vbs
rem
rem  IMPORTANT: keep this file ASCII-only. cmd.exe parses .bat by the system
rem  ANSI code page; multi-byte chars inside echo/if blocks get mis-parsed
rem  (a full-width bracket's trailing byte can be read as a half-width ')')
rem  and break the whole block.
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

rem  ---- candidate 1: python.org install used on this machine ----
call :try "%LOCALAPPDATA%\Programs\Python\Python314"
rem  ---- candidate 2: classic install location ----
call :try "C:\Python314"
rem  ---- candidate 3..n: whatever is on PATH ----
for /f "delims=" %%I in ('where python.exe 2^>nul') do call :try "%%~dpI"

if not defined PYW (
    echo [ERROR] No Python with PyQt6 was found.
    echo.
    echo         Install the dependency with:
    echo             python -m pip install -r requirements.txt
    echo.
    echo         Or edit this script and set the two paths directly, e.g.
    echo             set "PYW=C:\Python314\pythonw.exe"
    echo             set "PY=C:\Python314\python.exe"
    echo.
    pause
    exit /b 3
)

echo Using: %PY%
echo Starting QQBot Launcher (normal mode)...
start "" "%PYW%" "main.py"
exit /b 0

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
