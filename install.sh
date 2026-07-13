#!/usr/bin/env bash
# AutoWpp 2 - instalador de dependencias (Linux/macOS)
set -e
cd "$(dirname "$0")"

echo "[1/5] Python:"; python3 --version
echo "[2/5] Node:";   node --version

echo "[3/5] pip install..."
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt

echo "[4/5] npm install..."
npm install
npx puppeteer browsers install chrome || echo "AVISO: usando Chrome/Chromium do sistema."

echo "[5/5] .env..."
[ -f .env ] || cp .env.example .env

echo "Pronto! Rode: python3 frontend.py"
