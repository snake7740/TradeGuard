#!/usr/bin/env bash
# TradeGuard 备份脚本（US-E1-04，04 §9 备份策略）
# 用法：./backup.sh  → 生成 db/backup/tradeguard-YYYYmmdd-HHMM.dump
set -euo pipefail

BACKUP_DIR="$(dirname "$0")/../db/backup"
TS="$(date +%Y%m%d-%H%M)"
FILE="$BACKUP_DIR/tradeguard-$TS.dump"

mkdir -p "$BACKUP_DIR"
docker compose exec -T postgres pg_dump -U postgres -d tradeguard -Fc > "$FILE"
echo "backup done: $FILE"

# 保留最近 7 份
ls -1t "$BACKUP_DIR"/tradeguard-*.dump | tail -n +8 | xargs -r rm -f

# 恢复演练命令（US-E1-04 验收：恢复后 /api/cases 数据一致）：
# docker compose exec -T postgres pg_restore -U postgres -d tradeguard --clean --if-exists < db/backup/tradeguard-XXXX.dump
