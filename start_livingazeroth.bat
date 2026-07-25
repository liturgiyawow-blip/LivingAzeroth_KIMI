@echo off
chcp 65001 >nul
cd /d %~dp0

echo [LivingAzeroth] Starting backend...
start "LivingAzeroth Backend" cmd /k python main.py

timeout /t 3 /nobreak >nul

echo [LivingAzeroth] Starting TTS Worker (background)...
start /B pythonw.exe -m modules.tts_engine.tts_worker

echo.
echo ========================================
echo LivingAzeroth is running.
echo Close THIS window to kill TTS worker.
echo Close Backend window to stop main.py.
echo ========================================
pause