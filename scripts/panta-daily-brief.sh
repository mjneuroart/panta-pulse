#!/bin/bash
# Panta Pulse — brief quotidien (cron Hermes no_agent → Telegram).
# Lit le service local (127.0.0.1:8090), formate, imprime sur stdout (livré verbatim).
set -u

BASE=http://127.0.0.1:8090
B=""
for _ in 1 2 3; do
  B=$(curl -sS -m 150 "$BASE/api/briefing?force=1" 2>/dev/null) && [ -n "$B" ] && break
  sleep 5
done

if [ -z "$B" ]; then
  echo "⚠️ Panta Pulse — brief du jour indisponible : service local injoignable ($(date -u '+%d/%m %H:%M UTC')). Vérifier : systemctl --user status panta-pulse"
  exit 0
fi

S=$(curl -sS -m 120 "$BASE/api/signals?force=1" 2>/dev/null) || S=""

exec /root/bounty/panta/app/.venv/bin/python3 /root/bounty/panta/scripts/format_daily_brief.py "$B" "$S"
