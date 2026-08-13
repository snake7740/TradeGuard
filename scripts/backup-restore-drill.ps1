# 备份恢复演练（US-E1-04 验收：恢复后数据一致）
# 步骤：pg_dump 产出 -> 恢复到临时库 tradeguard_restore_test -> 核心表行数对账 -> 清理
$ErrorActionPreference = "Stop"
$container = "tradeguard-postgres-1"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backupDir = Join-Path $PSScriptRoot "..\db\backup"
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
$dump = "/tmp/tg-$stamp.dump"

Write-Host "== 1. 备份 =="
docker exec $container pg_dump -U postgres -d tradeguard -Fc -f $dump
docker exec $container ls -lh $dump

Write-Host "== 2. 恢复到临时库 =="
docker exec $container psql -U postgres -c "DROP DATABASE IF EXISTS tradeguard_restore_test;" | Out-Null
docker exec $container psql -U postgres -c "CREATE DATABASE tradeguard_restore_test;" | Out-Null
# 04-invariants.sql 的触发器依赖白名单表，pg_restore 顺序无关（同 dump 内）
docker exec $container pg_restore -U postgres -d tradeguard_restore_test --no-owner --no-acl $dump

Write-Host "== 3. 核心表行数对账 =="
$tables = @("account","transaction","risk_case","risk_signal","case_evidence",
            "disposition_record","approval_record","audit_log","kb_document","sys_config")
$fail = 0
foreach ($t in $tables) {
    $src = docker exec $container psql -U postgres -d tradeguard -tAc "SELECT count(*) FROM $t;"
    $dst = docker exec $container psql -U postgres -d tradeguard_restore_test -tAc "SELECT count(*) FROM $t;"
    $mark = if ($src -eq $dst) { "OK " } else { $fail++; "DIFF" }
    Write-Host ("{0} {1,-22} src={2} restored={3}" -f $mark, $t, $src, $dst)
}

Write-Host "== 4. 清理临时库与 dump =="
docker exec $container psql -U postgres -c "DROP DATABASE tradeguard_restore_test;" | Out-Null
docker cp "${container}:$dump" (Join-Path $backupDir "tradeguard-$stamp.dump")
docker exec $container rm -f $dump
if ($fail -eq 0) { Write-Host "DRILL_OK 恢复演练通过（10 表行数一致）" } else { Write-Host "DRILL_FAIL $fail 张表不一致"; exit 1 }
