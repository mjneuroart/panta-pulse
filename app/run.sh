#!/bin/bash
# Panta Pulse — lancement local (déploiement derrière Caddy à venir, S2).
cd "$(dirname "$0")"
exec .venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8090
