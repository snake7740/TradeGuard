"""合成数据发生器（US-E2-02，04 §8；采样参数借鉴 PaySim，见 docs/09）
规模参数：演示档 5000 账户 / 10 万交易 / 5 组欺诈团伙；--scale small 出 500 账户冒烟档。
欺诈行为特征：快进快出（transfer 占比高）、近 1 小时高频聚集（velocity 窗口可触发）、
同设备多账户、团伙共享联系方式。正常交易均匀散布近 60 天。
"""
import argparse
import asyncio
import hashlib
import os
import random
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg

PG_DSN = os.getenv("PG_DSN", "postgresql://tg_app:tg_app_dev@localhost:5432/tradeguard")

# PaySim 式交易类型分布（CNP 线上消费为主，transfer 为欺诈高发通道）
CHANNELS = ["CNP", "CNP", "CNP", "POS", "POS", "ATM", "transfer", "transfer"]
MCCS = ["5411", "5812", "5912", "4111", "5651", "6011", "4829", "5732"]
GEOS = ["杭州", "上海", "北京", "深圳", "广州", "成都", "武汉"]


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


async def main(accounts: int, txs: int, rings: int):
    rnd = random.Random(20260813)  # nosec B311 —— 合成数据固定种子（可重现），非安全用途
    conn = await asyncpg.connect(PG_DSN)
    try:
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(days=60)

        # 1. 账户
        hashes = [sha(f"acct-{i}") for i in range(accounts)]
        await conn.executemany(
            """INSERT INTO account (account_hash, risk_level, list_flag, credit_score_mock)
               VALUES ($1, 0, 'none', $2) ON CONFLICT (account_hash) DO NOTHING""",
            [(h, rnd.randint(480, 850)) for h in hashes])
        print(f"accounts: {len(hashes)}")

        # 2. 欺诈团伙：共享设备指纹 + 收款账户 + 联系方式
        #   （供 SAME_DEVICE/SAME_PAYEE/SAME_CONTACT 图谱，v_graph_edge 四类边全可实证）
        ring_devices, ring_payees, ring_members = [], [], []
        for r in range(rings):
            ring_devices.append(sha(f"ring-device-{r}"))
            ring_payees.append(sha(f"ring-payee-{r}"))
            # 每团伙 8–12 个成员账户
            members = rnd.sample(hashes, rnd.randint(8, 12))
            ring_members.append(members)
            await conn.executemany(
                "UPDATE account SET list_flag='watch', risk_level=3, contact_hash=$2 "
                "WHERE account_hash=$1",
                [(m, sha(f"ring-contact-{r}")) for m in members])
        print(f"fraud rings: {rings}")

        # 3. 正常交易
        rows = []
        for i in range(txs):
            ts = window_start + timedelta(seconds=rnd.randint(0, int((now - window_start).total_seconds())))
            rows.append((
                f"TX-{uuid.uuid4().hex[:16]}", rnd.choice(hashes),
                rnd.choice(hashes) if rnd.random() < 0.6 else None,
                round(rnd.lognormvariate(3.5, 1.1), 2),
                rnd.choice(MCCS), rnd.choice(CHANNELS),
                sha(f"dev-{rnd.randint(0, accounts // 2)}"),
                f"10.{rnd.randint(0,255)}.{rnd.randint(0,255)}.{rnd.randint(1,254)}/32",
                rnd.choice(GEOS), ts,
            ))

        # 4. 欺诈簇：近 1 小时高频小额 + 快进快出 transfer + 同设备（支撑 SC-11 velocity 验证）
        #    ts 全部落在 now()-uniform(2min,55min)：单主体近 1h 12~15 笔 ≥ velocity_1h
        #    阈值（BA-BR-14 缺省 10），聚合时 velocity 奖励可实证触发；
        #    （旧实现 burst 在 30~55 天前，velocity 窗口永远为空，SC-11 验证名不副实）
        for r in range(rings):
            for m in rnd.sample(ring_members[r], rnd.randint(3, 5)):
                for k in range(rnd.randint(12, 15)):
                    rows.append((
                        f"TX-{uuid.uuid4().hex[:16]}", m, ring_payees[r],
                        round(rnd.uniform(50, 480), 2),
                        "6011", "transfer", ring_devices[r],
                        f"172.16.{r}.{rnd.randint(1,254)}/32", "缅北",
                        now - timedelta(minutes=rnd.uniform(2, 55)),
                    ))

        # 分批插入：10 万行单次 executemany 实测会在客户端发送侧挂死
        # （pg_stat_activity 呈现 ClientRead 数小时不动），改 5000 行/批提交并显示进度；
        # tx_id 为 uuid 天然不重，ON CONFLICT DO NOTHING 仅兜底，复跑幂等
        tx_sql = """INSERT INTO transaction (tx_id, account_hash, payee_hash, amount, mcc, channel,
                                             device_fp_hash, ip, geo, ts)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8::inet,$9,$10) ON CONFLICT DO NOTHING"""
        for i in range(0, len(rows), 5000):
            await conn.executemany(tx_sql, rows[i:i + 5000])
            print(f"transactions: {min(i + 5000, len(rows))}/{len(rows)}", flush=True)
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", choices=["demo", "small"], default="demo")
    args = ap.parse_args()
    cfg = {"demo": (5000, 100_000, 5), "small": (500, 5000, 2)}[args.scale]
    asyncio.run(main(*cfg))
