@echo off
rem Starts FairFix on this computer at http://localhost:8765 and opens it.
rem Close this window to stop the app.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
if not exist ".venv\Scripts\python.exe" (
  echo Could not find .venv\Scripts\python.exe. Create it first: py -3.12 -m venv .venv
  pause
  exit /b 1
)
echo Starting FairFix at http://localhost:8765  (close this window to stop it)
start "" http://localhost:8765
".venv\Scripts\python.exe" -m uvicorn app.main:app --port 8765
pause
