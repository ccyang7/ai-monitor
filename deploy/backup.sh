#!/usr/bin/env bash
set -euo pipefail

cd /opt/ai-monitor
mkdir -p /opt/backup

./deploy/compose.sh exec -T postgres pg_dump -U ai_monitor -d ai_monitor \
  | gzip > "/opt/backup/ai_monitor_$(date +%F).sql.gz"

find /opt/backup -name 'ai_monitor_*.sql.gz' -mtime +7 -delete
