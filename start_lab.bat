@echo off
chcp 65001 > nul
cd /d "%~dp0"
title My Little Lab

if not exist ".venv\Scripts\python.exe" (
  echo [1/2] Creating virtual environment...
  py -3 -m venv .venv || python -m venv .venv
  echo [2/2] Installing packages...
  ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
)

".venv\Scripts\python.exe" app.py
pause
