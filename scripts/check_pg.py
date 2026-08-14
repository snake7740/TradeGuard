# -*- coding: utf-8 -*-
"""Postgres 连通性快检：端口读 PGPORT（compose 宿主映射缺省 5433，见 docker-compose.yml）"""
import asyncio
import os

import asyncpg

PORT = os.getenv("PGPORT", "5433")


async def t():
    for dsn in (
        f"postgresql://tg_web:tg_web_dev@127.0.0.1:{PORT}/tradeguard",
        f"postgresql://postgres:tradeguard_dev@127.0.0.1:{PORT}/tradeguard",
    ):
        try:
            c = await asyncpg.connect(dsn, timeout=5)
            print("OK", dsn.split("@")[1], await c.fetchval("select current_user"))
            await c.close()
        except Exception as e:
            print("FAIL", dsn.split("@")[1], type(e).__name__, str(e)[:100])

asyncio.run(t())
