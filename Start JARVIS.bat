@echo off
cd /d "d:\Python_Scripts\Jarvis"
echo Starting JARVIS v2.0...

set PYTHON=.venv\Scripts\python.exe

if not exist "%PYTHON%" (
    echo ERROR: Virtual environment not found.
    echo Run: py -3.11 -m venv .venv  and then install requirements.
    pause
    exit /b 1
)

:LOOP
"%PYTHON%" -u main.py %* 2>jarvis_err.log
set EXIT=%ERRORLEVEL%

if %EXIT% EQU 0 goto DONE

echo.
echo JARVIS exited with code %EXIT%

rem -1073741819 = 0xC0000005 ACCESS VIOLATION (native crash) — auto-restart
if %EXIT% EQU -1073741819 (
    echo Native crash detected. Restarting in 3 seconds...
    echo Check crash_trace.txt for the stack trace.
    timeout /t 3 /nobreak >nul
    goto LOOP
)

rem Any other non-zero exit — show log and wait
echo Check jarvis_err.log for details
type jarvis_err.log
pause

:DONE
