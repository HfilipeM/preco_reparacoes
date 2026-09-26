@echo off
setlocal
cd /d "%~dp0admin"

echo ============================================================
echo   Painel de Precos - a arrancar o servidor local...
echo ============================================================
echo.
echo Na primeira vez, isto vai instalar automaticamente as
echo dependencias necessarias (Flask, requests, beautifulsoup4,
echo playwright). Pode demorar um pouco so na primeira execucao.
echo.
echo O browser vai abrir sozinho em http://localhost:5050
echo Para PARAR o servidor, fecha esta janela ou prime Ctrl+C.
echo ============================================================
echo.

python server.py

pause
