@echo off
title Status SUS - Observabilidade e Telemetria
cd /d "%~dp0"
echo ============================================================
echo   Status SUS - Monitor de Barramentos de Saude Publica
echo   Painel disponivel em: http://127.0.0.1:8765
echo ============================================================
start "" "http://127.0.0.1:8765/"
"C:\Users\wesle\AppData\Local\Programs\Python\Python311\python.exe" server.py
pause
