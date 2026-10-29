@echo off
rem Live check of every setting, key, model, endpoint and the access controls.
rem Double-click to run. Report: logs\check_report.md   Full output: logs\check_log.txt
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
if not exist ".venv\Scripts\python.exe" (
  echo Could not find .venv\Scripts\python.exe. Create it first: py -3.12 -m venv .venv
  pause
  exit /b 1
)
if not exist logs mkdir logs
echo Checking FairFix with live Nemotron and Tavily calls. This takes about a minute...
".venv\Scripts\python.exe" -m scripts.check_live > logs\check_log.txt 2>&1
echo Exit code %ERRORLEVEL% >> logs\check_log.txt
type logs\check_log.txt
echo.
echo Report: logs\check_report.md
timeout /t 20
