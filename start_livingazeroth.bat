@echo off
chcp 65001 >nul
cd /d %~dp0

echo [F5-TTS] Starting TTS API server...
start "F5-TTS Server" cmd /k "cd f5_tts_server && venv\Scripts\python api_server.py"

echo Waiting for F5-TTS to load (15 sec)...
timeout /t 15 /nobreak >nul

echo [LivingAzeroth] Starting backend...
start "LivingAzeroth Backend" cmd /k python main.py

timeout /t 3 /nobreak >nul

echo [LivingAzeroth] Starting TTS Worker...
:: Для отладки: замени pythonw на python, чтобы видеть ошибки worker'а
:: Когда всё заработает — верни pythonw.exe обратно
start "TTS Worker" cmd /k python -m modules.tts_engine.tts_worker

echo.
echo ========================================
echo All systems running.
echo Close THIS window to stop TTS worker.
echo Close Backend window to stop main.py.
echo Close F5-TTS window to stop TTS server.
echo ========================================
pause