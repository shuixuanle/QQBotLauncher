@echo off
REM ---------------------------------------------------------------------------
REM Run every checker in tools\check_*.py  (double-click friendly entry point)
REM
REM Why this .bat exists:
REM   * cmd defaults to cp936 (GBK) on Chinese Windows, which garbles the
REM     Chinese output of the checkers -- and a few symbols cannot even be
REM     encoded, so a checker could die while printing. `chcp 65001` switches
REM     this console to UTF-8 and fixes both.
REM   * It also pins PYTHONIOENCODING so child processes and run_all_checks.py
REM     agree on the encoding.
REM
REM Keep this file pure ASCII (see CONTRIBUTING.md):
REM   a .bat with non-ASCII bytes is read as ANSI by cmd and shows up garbled.
REM ---------------------------------------------------------------------------
setlocal
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

cd /d "%~dp0.."
echo Working directory: %CD%
echo.

REM Pick an interpreter: the launcher `py -3` first (any python.org install),
REM then plain `python`. Without this, a machine whose `python` is only the
REM Microsoft Store alias would print "Python was not found" and exit 9009.
set PYTHON_CMD=
py -3 -c "import sys" >nul 2>nul && set PYTHON_CMD=py -3
if not defined PYTHON_CMD (
    python -c "import sys" >nul 2>nul && set PYTHON_CMD=python
)
if not defined PYTHON_CMD (
    echo [ERROR] No Python found. Install Python 3.9+ and make sure either
    echo         the "py" launcher or "python" works in cmd.
    echo.
    pause
    exit /b 9009
)

%PYTHON_CMD% "%~dp0run_all_checks.py" %*
set EXITCODE=%ERRORLEVEL%

echo.
echo Exit code = %EXITCODE%   (0 = all checks passed)
pause
exit /b %EXITCODE%
