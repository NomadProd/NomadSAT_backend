#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi

if [[ ! -f .env ]]; then
  echo "WARNING: .env not found. Copying from .env.example — edit before use."
  cp .env.example .env
fi

.venv/bin/pip install -r requirements.txt -q

LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || true)"
echo "Starting backend on http://0.0.0.0:8000 (all interfaces)"
if [[ -n "$LAN_IP" ]]; then
  echo "Phone / LAN: http://${LAN_IP}:8000/docs"
else
  echo "Phone / LAN: use your Mac's Wi-Fi IP, e.g. http://192.168.x.x:8000/docs"
fi
echo "Do NOT use: uvicorn main:app --reload --port 8000  (binds 127.0.0.1 only)"

.venv/bin/uvicorn main:app --reload --host 0.0.0.0 --port 8000
