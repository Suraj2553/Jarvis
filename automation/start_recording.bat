@echo off
setlocal
cd /d "%~dp0\.."
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "automation\main.py"
) else (
  python "automation\main.py"
)
pause
