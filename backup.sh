#!/bin/bash
# VPN stack daily backup
# Usage: BACKUP_DIR=/path/to/backups bash backup.sh
set -euo pipefail
BACKUP_DIR="${BACKUP_DIR:-./backups}"
REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
TS=$(date +%Y-%m-%d_%H%M)
mkdir -p "$BACKUP_DIR"

# Pack: Marzban SQLite + xray config (secrets excluded from OSS repo)
tar -czf "$BACKUP_DIR/vpn-${TS}.tar.gz" \
  -C / var/lib/marzban \
  -C "$REPO_ROOT" marzban/xray/xray_config.json 2>/dev/null || true

# Keep 30 days
find "$BACKUP_DIR" -name "vpn-*.tar.gz" -mtime +30 -delete

echo "[$(date)] backup done: $BACKUP_DIR/vpn-${TS}.tar.gz"
