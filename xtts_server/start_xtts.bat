@echo off
cd /d %~dp0
C:\Python310\python.exe -m venv venv
venv\Scripts\pip install TTS torch torchaudio fastapi uvicorn requests
C:\Python310\python.exe api_server.py