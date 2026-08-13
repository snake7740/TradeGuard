#!/usr/bin/env bash
# TradeGuard 备份脚本（US-E1-04，04 §9）
# 在 postgres 容器内 pg_dump 自定义格式（支持选择性恢复），输出到宿主 db/backup/。
# 用法（宿主）: bash scripts/backup.sh   或 PowerShell 调 scripts/backup-restore-drill.ps1
set -euo pipefail

CONTAINER="${PG_CONTAINER:-tradeguard-postgres-1}"
DB="${PG_DATABASE:-tradeguard}"
OUT_DIR="$(cd "$(dirname "$0")/.." && pwd)/db/backup"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT_FILE="$OUT_DIR/tradeguard-$STAMP.dump"

mkdir -p "$OUT_DIR"
docker exec "$CONTAINER" pg_dump -U postgres -d "$DB" -Fc -f "/tmp/tg-$STAMP.dump"
docker cp "$CONTAINER:/tmp/tg-$STAMP.dump" "$OUT_FILE"
docker exec "$CONTAINER" rm -f "/tmp/tg-$STAMP.dump"
echo "BACKUP_OK $OUT_FILE ($(du -h "$OUT_FILE" | cut -f1))"
