@echo off
chcp 65001 >nul
cd /d %~dp0

echo [XTTS-v2] Starting TTS server...
start "XTTS-v2 Server" cmd /k "cd /d C:\Data\Games\1_Azeroth_Live\LivingAzeroth_KIMI\xtts_server && venv\Scripts\python.exe api_server.py"

echo Waiting for XTTS-v2 to warm up (15 sec, first load ~2GB)...
timeout /t 15 /nobreak >nul

echo [LivingAzeroth] Starting backend...
start "LivingAzeroth Backend" cmd /k python main.py

timeout /t 3 /nobreak >nul

echo [LivingAzeroth] Starting TTS Worker...
start "TTS Worker" cmd /k python -m modules.tts_engine.tts_worker

echo.
echo ========================================
echo All systems running.
echo Close THIS window to stop everything.
echo ========================================
pause