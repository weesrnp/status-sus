@echo off
title Status SUS - Observabilidade e Telemetria
cd /d "%~dp0"
echo ============================================================
echo   Status SUS - Monitor de Barramentos de Saude Publica
echo   Painel disponivel em: http://127.0.0.1:8765
echo ============================================================
start "" "http://127.0.0.1:8765/"
where py >nul 2>nul
if %errorlevel% equ 0 (
    py server.py
) else (
    python server.py
)
pause
