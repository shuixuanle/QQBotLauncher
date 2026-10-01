@echo off
rem ============================================================
rem  Hydrant bot launcher (NewHydrant) - controlled entry point
rem
rem  Called by: QQBot Launcher  ("Hydrant / NewHydrant main program")
rem  Command  : cmd.exe /c start_hydrant.bat     <- no path, no quotes
rem  Work dir : this file's folder (..\scripts)
rem
rem  Flow:
rem    1) already admin?  -> go to :run
rem    2) otherwise re-run this script elevated via UAC (-Wait)
rem    3) cd to the build output folder, run dotnet in the foreground
rem
rem  WHY the elevation lives here and not in the launcher command:
rem    The launcher passes argv straight to QProcess; Qt escapes quotes in
rem    an argument into backslash-quote, and PowerShell treats backslash-quote
rem    as its own escape -> three levels of nested quoting always break
rem    (real incident: PowerShell printed the command instead of running it,
rem    exit code 0 but nothing started). With this script the command needs
rem    no quotes at all.
rem
rem  IMPORTANT: keep this file ASCII-only. cmd.exe parses .bat by the system
rem  ANSI code page, and multi-byte characters inside echo/if blocks get
rem  mis-parsed (the trailing byte of a full-width bracket can be read as a
rem  half-width closing paren), which closes an if-block early.
rem  Chinese notes live in scripts/README-start_hydrant.md instead.
rem
rem  Do NOT add "pause": it would hold the console open.
rem
rem  Where the bot lives: this script derives it RELATIVE to its own folder, so
rem  the repo contains no hard-coded personal path. Override in either way:
rem    - set an environment variable QQBOT_HYDRANT_DIR before launching, or
rem    - edit the fallback path below.
rem ============================================================
setlocal
rem ---- locate the bot folder: env var > relative derivation > fallback ----
set "BOT_DIR=%QQBOT_HYDRANT_DIR%"
if not defined BOT_DIR (
    rem <launcher>\scripts\..\..\..\..\..\.. = six levels up from here
    pushd "%~dp0..\..\..\..\..\.." 2>nul && set "BOT_DIR=%CD%\Bleatingsheep.NewHydrant.Bot\bin\Debug\net10.0" && popd
)
if not defined BOT_DIR set "BOT_DIR=%~dp0..\Bleatingsheep.NewHydrant.Bot\bin\Debug\net10.0"
rem  Edit this line if your layout differs from the derivation above:
rem  set "BOT_DIR=D:\path\to\Bleatingsheep.NewHydrant.Bot\bin\Debug\net10.0"

rem ---- (1) already elevated? ----
net session >nul 2>&1
if %errorlevel%==0 goto run

rem ---- (2) re-run this script elevated; -Wait keeps us alive until it ends ----
rem  An elevated process starts with cwd = C:\Windows\System32, so cd to this
rem  script folder first; %~f0 may contain spaces and non-ASCII characters.
echo Requesting administrator rights - please click Yes on the UAC prompt...
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $p = Start-Process -FilePath '%~f0' -Verb RunAs -Wait -PassThru; exit $p.ExitCode } catch { exit 1223 }"
set "RC=%errorlevel%"
if not "%RC%"=="0" (
    echo.
    echo Elevation did not complete, or child exit code = %RC%
    echo [1223 means the UAC prompt was cancelled]
    echo To start without elevation, remove the elevation part of this script.
)
endlocal & exit /b %RC%

rem ---- (3) elevated: run the bot in the foreground ----
:run
title NewHydrant bot [admin]
cd /d "%BOT_DIR%"
if errorlevel 1 (
    echo Cannot enter folder: %BOT_DIR%
    endlocal & exit /b 3
)
echo Working dir: %CD%
echo Starting Bleatingsheep.NewHydrant.Bot.dll ...
dotnet Bleatingsheep.NewHydrant.Bot.dll
set "RC=%errorlevel%"
echo.
echo Process exited with code %RC%
endlocal & exit /b %RC%
