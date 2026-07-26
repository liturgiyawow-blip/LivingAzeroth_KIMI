@echo off
chcp 65001 >nul
cd /d %~dp0

echo [GPT-SoVITS] Starting TTS API server...
start "GPT-SoVITS Server" cmd /k "cd /d C:\GPT-SoVITS && start_api.bat"

echo Waiting for GPT-SoVITS to load (25 sec)...
timeout /t 25 /nobreak >nul

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
echo Close GPT-SoVITS window to stop TTS server.
echo ========================================
pause