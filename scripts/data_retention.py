# -*- coding: utf-8 -*-
"""BA-BR-12 数据生命周期脚本（03 §7 保留策略）：数据保留 5 年，超期脱敏归档

处理范围（03 §7"事件与交易数据 5 年"）：
  transaction（DA-T-02，按月分区）：
    - 范围上界 ≤ 截止线的整月分区 → 行脱敏后拷入 transaction_archive，再 DROP 分区
      （08 §4"生命周期归档"即分区裁剪路径）；
    - 跨界/default 分区中 ts < 截止线的散行 → 脱敏归档后 DELETE；
    - 脱敏口径（03 §7）：account/payee/device 哈希仅留前 8 位 + '****'，ip/geo 置空，
      金额/mcc/channel/ts 保留（统计口径不含个人标识）。
  risk_case（DA-T-03）：
    - 已归档（ARCHIVED）且 closed_at（缺省 updated_at）< 截止线 → 原地脱敏：
      subject_ref 留前 8 位 + '****'、context_json 清空、matrix_room 置空；
      案件骨架保留（审计链 DA-T-08 引用不破坏）；已脱敏行（****后缀）不重复处理，幂等。
  不处理：audit_log / trace 保留 3 年为独立策略（03 §7），不在本脚本范围。

默认 dry-run 只输出将处理的行数与动作清单；加 --execute 才真正执行。
归档/DDL 需要表 owner 权限，默认走宿主侧超级账号 DSN（tg_web/tg_app 无 DELETE 权限）。

用法：.venv\\Scripts\\python.exe scripts\\data_retention.py [--years 5] [--execute]
"""
from __future__ import annotations

import argparse
import asyncio
import os

import asyncpg

DSN = os.getenv("PG_DSN", "postgresql://postgres:tradeguard_dev@localhost:5433/tradeguard")

# 归档表：脱敏后的交易留存（无个人标识列约束，哈希已截断）
ARCHIVE_DDL = """
CREATE TABLE IF NOT EXISTS transaction_archive (
    tx_id        varchar(40)   NOT NULL,
    account_mask varchar(16)   NOT NULL,
    payee_mask   varchar(16),
    amount       numeric(14,2) NOT NULL,
    mcc          varchar(4)    NOT NULL,
    channel      varchar(16)   NOT NULL,
    ts           timestamptz   NOT NULL,
    archived_at  timestamptz   NOT NULL DEFAULT now(),
    PRIMARY KEY (tx_id, ts)
)"""

MASK = "left({col}, 8) || '****'"


async def plan_transaction(conn, cutoff_years: int) -> dict:
    """交易表处理计划：整月过期分区（DROP）+ 散行（DELETE）"""
    # 各分区的范围上界（default 分区无 TO 界，按散行处理）
    parts = await conn.fetch("""
        SELECT c.relname AS part,
               pg_get_expr(c.relpartbound, c.oid) AS bound
        FROM pg_inherits i
        JOIN pg_class c ON c.oid = i.inhrelid
        JOIN pg_class p ON p.oid = i.inhparent
        WHERE p.relname = 'transaction' ORDER BY c.relname""")
    cutoff = await conn.fetchval(
        "SELECT now() - make_interval(years => $1)", cutoff_years)
    drop_parts, keep_parts = [], []
    for r in parts:
        bound = r["bound"]  # 形如 FOR VALUES FROM ('..') TO ('..') 或 DEFAULT
        if "TO (" not in bound:
            keep_parts.append(r["part"])
            continue
        upper = bound.split("TO ('")[1].split("'")[0]
        expired = await conn.fetchval(  # 上界文本入参，SQL 侧转 timestamptz 比较
            "SELECT $1::text::timestamptz <= $2", upper, cutoff)
        (drop_parts if expired else keep_parts).append(r["part"])
    # 整分区行数（归档量）与非过期分区中的超期散行
    part_rows = 0
    for p in drop_parts:
        part_rows += await conn.fetchval(f'SELECT count(*) FROM "{p}"')
    stray_rows = 0
    for p in keep_parts:
        stray_rows += await conn.fetchval(
            f'SELECT count(*) FROM "{p}" WHERE ts < $1', cutoff)
    return {"cutoff": cutoff, "drop_parts": drop_parts,
            "part_rows": part_rows, "stray_rows": stray_rows}


async def execute_transaction(conn, plan: dict) -> None:
    """归档 + 清理：先脱敏拷贝，再 DROP 分区 / DELETE 散行（同事务）"""
    sel_masked = (f"SELECT tx_id, {MASK.format(col='account_hash')}, "
                  f"CASE WHEN payee_hash IS NULL THEN NULL ELSE {MASK.format(col='payee_hash')} END, "
                  "amount, mcc, channel, ts")
    async with conn.transaction():
        await conn.execute(ARCHIVE_DDL)
        for p in plan["drop_parts"]:
            await conn.execute(
                f"""INSERT INTO transaction_archive
                    (tx_id, account_mask, payee_mask, amount, mcc, channel, ts)
                    {sel_masked} FROM "{p}" ON CONFLICT DO NOTHING""")
            await conn.execute(f'DROP TABLE "{p}"')  # 08 §4 分区裁剪归档
        if plan["stray_rows"]:
            await conn.execute(
                f"""INSERT INTO transaction_archive
                    (tx_id, account_mask, payee_mask, amount, mcc, channel, ts)
                    {sel_masked} FROM transaction WHERE ts < $1
                    ON CONFLICT DO NOTHING""", plan["cutoff"])
            await conn.execute(
                "DELETE FROM transaction WHERE ts < $1", plan["cutoff"])


async def plan_risk_case(conn, cutoff_years: int) -> dict:
    """案件脱敏计划：ARCHIVED 且关闭时间超期、尚未脱敏（****后缀幂等标记）"""
    row = await conn.fetchrow("""
        SELECT count(*) AS n, now() - make_interval(years => $1) AS cutoff
        FROM risk_case
        WHERE status = 'ARCHIVED'
          AND coalesce(closed_at, updated_at) < now() - make_interval(years => $1)
          AND subject_ref NOT LIKE '%****'""", cutoff_years)
    return {"cutoff": row["cutoff"], "rows": row["n"]}


async def execute_risk_case(conn, cutoff_years: int) -> int:
    result = await conn.execute(f"""
        UPDATE risk_case
        SET subject_ref = {MASK.format(col='subject_ref')},
            context_json = '{{}}'::jsonb, matrix_room = NULL, updated_at = now()
        WHERE status = 'ARCHIVED'
          AND coalesce(closed_at, updated_at) < now() - make_interval(years => $1)
          AND subject_ref NOT LIKE '%****'""", cutoff_years)
    return int(result.split()[-1])


async def main() -> None:
    ap = argparse.ArgumentParser(description="BA-BR-12 超期数据脱敏归档（默认 dry-run）")
    ap.add_argument("--years", type=int, default=5, help="保留年限（默认 5，BA-BR-12）")
    ap.add_argument("--execute", action="store_true", help="真正执行（缺省仅输出计划）")
    args = ap.parse_args()

    conn = await asyncpg.connect(DSN)
    try:
        tx_plan = await plan_transaction(conn, args.years)
        rc_plan = await plan_risk_case(conn, args.years)
        mode = "EXECUTE" if args.execute else "DRY-RUN"
        print(f"■ BA-BR-12 数据保留检查（保留 {args.years} 年，截止线 {tx_plan['cutoff']:%Y-%m-%d}，{mode}）")
        print(f"  [transaction] 整月过期分区 {len(tx_plan['drop_parts'])} 个"
              f"（{tx_plan['part_rows']} 行）→ 脱敏归档 transaction_archive 后 DROP PARTITION")
        for p in tx_plan["drop_parts"]:
            print(f"    - DROP {p}")
        print(f"  [transaction] 跨界/default 分区超期散行 {tx_plan['stray_rows']} 行"
              "→ 脱敏归档后 DELETE")
        print(f"  [risk_case]   ARCHIVED 超期未脱敏 {rc_plan['rows']} 行"
              "→ UPDATE 脱敏（subject_ref 截断 / context_json 清空 / matrix_room 置空）")
        if not args.execute:
            print("■ dry-run 结束：未做任何变更；加 --execute 执行上述动作")
            return
        await execute_transaction(conn, tx_plan)
        masked = await execute_risk_case(conn, args.years)
        print(f"■ 执行完成：归档交易 {tx_plan['part_rows'] + tx_plan['stray_rows']} 行、"
              f"DROP 分区 {len(tx_plan['drop_parts'])} 个、脱敏案件 {masked} 行")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
