import asyncio
import asyncpg

async def t():
    for dsn in (
        "postgresql://tg_web:tg_web_dev@127.0.0.1:5432/tradeguard",
        "postgresql://postgres:tradeguard_dev@127.0.0.1:5432/tradeguard",
    ):
        try:
            c = await asyncpg.connect(dsn, timeout=5)
            print("OK", dsn.split("@")[1], await c.fetchval("select current_user"))
            await c.close()
        except Exception as e:
            print("FAIL", dsn.split("@")[1], type(e).__name__, str(e)[:100])

asyncio.run(t())
