@echo off
rem Runs the full FairFix evaluation with live Nemotron calls (reads every sample bill photo).
rem Double-click to run. Results: eval\results.md   Full output: eval\run_log.txt
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
if not exist ".venv\Scripts\python.exe" (
  echo Could not find .venv\Scripts\python.exe. Create it first: py -3.12 -m venv .venv
  pause
  exit /b 1
)
echo Running the FairFix evaluation with Nemotron. This takes a few minutes...
".venv\Scripts\python.exe" -m eval.run_eval --models --extract > eval\run_log.txt 2>&1
echo Exit code %ERRORLEVEL% >> eval\run_log.txt
echo Done. Results are in eval\results.md
timeout /t 15
