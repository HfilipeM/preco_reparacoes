#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/admin"

echo "============================================================"
echo "  Painel de Precos - a arrancar o servidor local..."
echo "============================================================"
echo
echo "Na primeira vez, isto instala automaticamente as dependencias"
echo "necessarias (Flask, requests, beautifulsoup4, playwright)."
echo
echo "O browser vai abrir sozinho em http://localhost:5050"
echo "Para PARAR o servidor, prime Ctrl+C nesta janela."
echo "============================================================"
echo

python3 server.py
