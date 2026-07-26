@echo off
chcp 65001 >nul
cd /d %~dp0

echo [XTTS-v2] Starting TTS server on Python 3.10...
start "XTTS-v2 Server" cmd /k "cd /d C:\Data\Games\1_Azeroth_Live\LivingAzeroth_KIMI\xtts_server>venv\Scripts\python.exe api_server.py"

echo Waiting for XTTS-v2 to load (5 sec, first time downloads ~2GB)...
timeout /t 5 /nobreak >nul

echo [LivingAzeroth] Starting backend...
start "LivingAzeroth Backend" cmd /k python main.py

timeout /t 3 /nobreak >nul

echo [LivingAzeroth] Starting TTS Worker...
start "TTS Worker" cmd /k python -m modules.tts_engine.tts_worker

echo.
echo ========================================
echo All systems running.
echo Close THIS window to stop TTS worker.
echo Close Backend window to stop main.py.
echo Close XTTS-v2 window to stop TTS server.
echo ========================================
pause