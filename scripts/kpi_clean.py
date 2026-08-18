# -*- coding: utf-8 -*-
"""测试残留清洗（KPI 口径配套，US-E7-04）：剔除 pytest 运行残留的非业务数据

识别特征（立案来源审计 + 主体档案双维度，与写入路径一一对应）：
  A. source=TEST 注册案件：tests 直调 repo.register(source_type="TEST")，
     审计 basis 记 'source=TEST,...'（含 test_matrix_sc04 自建 account 的案件）；
  B. 直插案件：绕过 /api/alerts 无 case.register 审计，且主体无 account 档案
     （test_multi_role_flow 直插 INVESTIGATING 等）。业务流必经 API-W-01 立案
     审计，"无注册审计"即非业务通道；
  C. 漏标立案：经 /api/alerts 但主体无 account 档案（tests _alert 不传
     source_type 落 demo_script 缺省，AlertIn 白名单无 TEST 故无法显式打标）。
     真实门户立案主体必从账户表选取（有档案），剧本主体有 demo 交易（有档案），
     故"注册来源任意 + 无档案"即可判测试残留。

级联范围（软硬关联一并清理，事务内原子完成）：
  risk_signal / case_evidence / disposition_record / approval_record（FK）
  agent_memory.case_id（软关联，DA-T-07）
  audit_log.target=case_id（测试案件的审计行随案件消亡，业务审计不动）
  transaction：主体无 account 档案的播种行（tests _seed_tx 合成主体）
  保留：demo_script 立案（剧本/门户人工流）及其全部链路数据、kb_document。

默认 dry-run 只输出统计；--execute 才执行。需超级账号（应用角色无 DELETE 权限，
02-roles.sql 只增约束；与 scripts/data_retention.py 同一权限模型）。

用法：.venv\\Scripts\\python.exe scripts/kpi_clean.py [--execute]
"""
from __future__ import annotations

import argparse
import asyncio
import os

import asyncpg

DSN = os.getenv("PG_DSN", "postgresql://postgres:tradeguard_dev@localhost:5433/tradeguard")

# 测试案件集（A ∪ B ∪ C）：A=TEST 注册；B=无注册审计且无档案；
# C=有注册审计（任意来源）但主体无档案（API 通道漏标，真实主体必建档）
TEST_CASES = """
SELECT rc.case_id FROM risk_case rc
WHERE EXISTS (SELECT 1 FROM audit_log al WHERE al.target = rc.case_id
              AND al.action = 'case.register' AND al.basis LIKE 'source=TEST%')
   OR NOT EXISTS (SELECT 1 FROM account a WHERE a.account_hash = rc.subject_ref)"""

CASE_CHILD_TABLES = ("risk_signal", "case_evidence", "disposition_record",
                     "approval_record", "agent_memory")


async def stats(conn) -> dict[str, int]:
    ids = [r["case_id"] for r in await conn.fetch(TEST_CASES)]
    out = {"cases": len(ids)}
    if not ids:
        return out
    for tbl in CASE_CHILD_TABLES:
        out[tbl] = await conn.fetchval(
            f"SELECT count(*) FROM {tbl} WHERE case_id = ANY($1::varchar[])", ids)
    out["audit_log"] = await conn.fetchval(
        "SELECT count(*) FROM audit_log WHERE target = ANY($1::varchar[])", ids)
    out["transaction"] = await conn.fetchval("""
        SELECT count(*) FROM transaction t
        WHERE NOT EXISTS (SELECT 1 FROM account a
                          WHERE rtrim(t.account_hash) = a.account_hash)""")
    # 保留集对照（业务口径基线，不应被触碰）
    out["keep_demo"] = await conn.fetchval("""
        SELECT count(*) FROM risk_case rc
        WHERE EXISTS (SELECT 1 FROM audit_log al WHERE al.target = rc.case_id
                      AND al.action = 'case.register'
                      AND al.basis LIKE 'source=demo_script%')""")
    return out


async def execute(conn) -> dict[str, int]:
    ids = [r["case_id"] for r in await conn.fetch(TEST_CASES)]
    done = {"cases": len(ids)}
    if not ids:
        return done
    async with conn.transaction():
        for tbl in CASE_CHILD_TABLES:
            result = await conn.execute(
                f"DELETE FROM {tbl} WHERE case_id = ANY($1::varchar[])", ids)
            done[tbl] = int(result.split()[-1])
        result = await conn.execute(
            "DELETE FROM audit_log WHERE target = ANY($1::varchar[])", ids)
        done["audit_log"] = int(result.split()[-1])
        result = await conn.execute("""
            DELETE FROM transaction t
            WHERE NOT EXISTS (SELECT 1 FROM account a
                              WHERE rtrim(t.account_hash) = a.account_hash)""")
        done["transaction"] = int(result.split()[-1])
        result = await conn.execute(
            "DELETE FROM risk_case WHERE case_id = ANY($1::varchar[])", ids)
        done["risk_case"] = int(result.split()[-1])
    return done


async def main() -> None:
    ap = argparse.ArgumentParser(description="测试残留清洗（默认 dry-run，KPI 业务口径配套）")
    ap.add_argument("--execute", action="store_true", help="真正执行（缺省仅输出统计）")
    args = ap.parse_args()

    conn = await asyncpg.connect(DSN)
    try:
        if not args.execute:
            s = await stats(conn)
            print("■ 测试残留统计（DRY-RUN，未做任何变更）")
            print(f"  待清理案件 {s['cases']} 件（TEST 注册 + 无档案：直插/漏标立案）")
            for tbl in CASE_CHILD_TABLES + ("audit_log", "transaction"):
                print(f"  {tbl:<20} {s[tbl]} 行")
            print(f"  保留 demo_script 业务案件 {s['keep_demo']} 件（不动）")
            print("■ 加 --execute 执行清洗")
            return
        s = await stats(conn)
        done = await execute(conn)
        print(f"■ 清洗完成（事务原子）：案件 {done['cases']} 件（TEST 注册 + 无档案）")
        for tbl in CASE_CHILD_TABLES + ("audit_log", "transaction", "risk_case"):
            print(f"  {tbl:<20} 删除 {done.get(tbl, 0)} 行（dry-run 统计 {s.get(tbl, 0)}）")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
