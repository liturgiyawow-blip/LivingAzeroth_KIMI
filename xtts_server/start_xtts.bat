@echo off
cd /d %~dp0

if not exist venv (
    echo [XTTS] Creating virtual environment...
    C:\Python310\python.exe -m venv venv
    echo [XTTS] Installing packages (first time only, ~2 GB)...
    venv\Scripts\pip install --upgrade pip
    venv\Scripts\pip install TTS torch torchaudio fastapi uvicorn requests
) else (
    echo [XTTS] Using existing venv.
)

echo [XTTS] Starting server...
C:\Python310\python.exe api_server.py
