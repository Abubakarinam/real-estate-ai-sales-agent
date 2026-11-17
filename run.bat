@echo off
cd /d "%~dp0"
if not exist .venv (
  echo Setting up for the first time...
  python -m venv .venv
  .venv\Scripts\python -m pip install -q -r requirements.txt
)
if not exist .env copy .env.example .env >nul
.venv\Scripts\python run.py
pause
