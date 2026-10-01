@echo off
rem ============================================================
rem  Diagnose / work around:  "Could not create temporary directory!"
rem
rem  A PyInstaller one-file exe unpacks itself into %TEMP%\_MEIxxxx at
rem  startup. That error means the unpack folder could not be created.
rem  The TEMP *setting* on this machine is fine, so the remaining causes are:
rem      (1) the exe path itself contains non-ASCII characters
rem          (PyInstaller's bootloader uses narrow-char path APIs)
rem      (2) antivirus / Controlled Folder Access blocks _MEI* folders
rem      (3) the exe was started elevated and the elevated token cannot
rem          write to %TEMP%
rem
rem  This script:
rem      * lists every candidate exe (dist\*.exe, then *.exe here)
rem      * prints the current TEMP/TMP
rem      * points TEMP/TMP at a short ASCII folder for THIS process only
rem      * launches the chosen exe and captures stderr to a log
rem
rem  Usage:
rem      double-click                      -> run the first exe found
rem      launch-exe-fix.bat 2              -> run the 2nd exe in the list
rem
rem  ASCII-only on purpose (cmd parses .bat by the ANSI code page).
rem ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ================ packaged exe launcher / diagnostic ================
echo.
echo [1] current TEMP/TMP
echo     TEMP = %TEMP%
echo     TMP  = %TMP%
echo.

echo [2] candidate exe files
set "COUNT=0"
for %%F in ("dist\*.exe") do (
    set /a COUNT+=1
    echo     [!COUNT!] %%~fF   %%~zF bytes
)
if %COUNT%==0 (
    for %%F in ("*.exe") do (
        set /a COUNT+=1
        echo     [!COUNT!] %%~fF   %%~zF bytes
    )
)
if %COUNT%==0 (
    echo     none found
    echo.
    echo [ERROR] No exe found. Build it first:
    echo             python tools\build_exe.py
    echo.
    pause
    exit /b 2
)
echo.

rem ---- pick the requested index (default 1) ----
set "WANT=%1"
if "%WANT%"=="" set "WANT=1"
set "EXE="
set "IDX=0"
for %%F in ("dist\*.exe") do (
    set /a IDX+=1
    if "!IDX!"=="%WANT%" set "EXE=%%~fF"
)
if not defined EXE (
    for %%F in ("*.exe") do (
        set /a IDX+=1
        if not defined EXE if "!IDX!"=="%WANT%" set "EXE=%%~fF"
    )
)
if not defined EXE (
    echo [ERROR] index %WANT% not found; using the first one.
    for %%F in ("dist\*.exe") do if not defined EXE set "EXE=%%~fF"
    for %%F in ("*.exe") do if not defined EXE set "EXE=%%~fF"
)
echo [3] selected exe
echo     %EXE%
echo.

echo [4] non-ASCII check on the exe path
set "PATHTEST=%EXE%"
echo     path: %PATHTEST%
echo     %PATHTEST% | findstr /r /c:"^[ -~]*$" >nul
if errorlevel 1 (
    echo     !! The path contains NON-ASCII characters.
    echo        PyInstaller's bootloader may fail to create the unpack folder.
    echo        WORKAROUND: copy the exe to a pure-ASCII folder and run it there:
    echo            mkdir C:\QQBotTest
    echo            copy "%EXE%" C:\QQBotTest\
    echo            C:\QQBotTest\*.exe
) else (
    echo     OK - the path is pure ASCII.
)
echo.

rem ---- short ASCII temp folder for this process only ----
set "TMPROOT=%~dp0_tmp"
if not exist "%TMPROOT%" mkdir "%TMPROOT%" 2>nul
set "TEMP=%TMPROOT%"
set "TMP=%TMPROOT%"
echo [5] TEMP/TMP for this launch
echo     %TEMP%
echo.

set "ERRLOG=%~dp0exe_stderr.log"
if exist "%ERRLOG%" del "%ERRLOG%"

echo [6] starting - stderr goes to exe_stderr.log ...
start "" /wait "%EXE%" 2>"%ERRLOG%"
set "RC=%errorlevel%"
echo.
echo [7] exit code = %RC%
if exist "%ERRLOG%" (
    echo     ---- stderr ----
    type "%ERRLOG%"
    echo     -----------------
) else (
    echo     no stderr captured
)
echo.
echo If you saw "Could not create temporary directory!" again, try:
echo     1] run the NORMAL exe first, not the admin one
echo     2] copy the exe to C:\QQBotTest\ and run it there
echo     3] add the project folder + C:\Users\%USERNAME%\AppData\Local\Temp
echo        to your antivirus exclusions, including Controlled Folder Access
echo.
pause
exit /b %RC%
