"""合成数据发生器（US-E2-02，04 §8；采样参数借鉴 PaySim，见 docs/09）
规模参数：演示档 5000 账户 / 10 万交易 / 5 组欺诈团伙；--scale small 出 500 账户冒烟档。
欺诈行为特征：快进快出（transfer 占比高）、夜间集中（22:00–05:00）、同设备多账户。
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
    rnd = random.Random(20260813)
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

        # 2. 欺诈团伙：共享设备指纹 + 收款账户（供 SAME_DEVICE/SAME_PAYEE 图谱）
        ring_devices, ring_payees = [], []
        for r in range(rings):
            ring_devices.append(sha(f"ring-device-{r}"))
            ring_payees.append(sha(f"ring-payee-{r}"))
            # 每团伙 8–12 个成员账户
            members = rnd.sample(hashes, rnd.randint(8, 12))
            await conn.executemany(
                "UPDATE account SET list_flag='watch', risk_level=3 WHERE account_hash=$1",
                [(m,) for m in members])
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

        # 4. 欺诈簇：夜间高频小额 + 快进快出 transfer + 同设备（支撑 SC-11 velocity 验证）
        for r in range(rings):
            for m in rnd.sample(hashes, 10):
                burst = window_start + timedelta(days=rnd.randint(30, 55), hours=rnd.randint(22, 28))
                for k in range(rnd.randint(10, 15)):  # 单小时 10+ 笔 → velocity_1h 超阈
                    rows.append((
                        f"TX-{uuid.uuid4().hex[:16]}", m, ring_payees[r],
                        round(rnd.uniform(50, 480), 2),
                        "6011", "transfer", ring_devices[r],
                        f"172.16.{r}.{rnd.randint(1,254)}/32", "缅北",
                        burst + timedelta(minutes=k * 3),
                    ))

        await conn.executemany(
            """INSERT INTO transaction (tx_id, account_hash, payee_hash, amount, mcc, channel,
                                        device_fp_hash, ip, geo, ts)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8::inet,$9,$10) ON CONFLICT DO NOTHING""", rows)
        print(f"transactions: {len(rows)}")
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", choices=["demo", "small"], default="demo")
    args = ap.parse_args()
    cfg = {"demo": (5000, 100_000, 5), "small": (500, 5000, 2)}[args.scale]
    asyncio.run(main(*cfg))
