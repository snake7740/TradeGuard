"""共享夹具：宿主侧直连运行中的栈（postgres localhost:5432，tg_web 人类写路径账号）。

路径注入使 `app` 包可导入；事件发布用记录替身（端口/适配器模式的可测试性收益）。
"""
import os
import sys

import asyncpg
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

PG_DSN = os.getenv("TG_TEST_DSN", "postgresql://tg_web:tg_web_dev@localhost:5433/tradeguard")


class RecordingPublisher:
    """EventPublisher 测试替身：只记录不投递"""

    def __init__(self):
        self.published = []

    async def publish(self, case_id, event, payload, actor):
        self.published.append({"case_id": case_id, "event": event, "payload": payload, "actor": actor})

    async def subscribe(self, *a, **kw):  # pragma: no cover
        pass

    async def unsubscribe(self, *a, **kw):  # pragma: no cover
        pass


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(PG_DSN, min_size=1, max_size=4)
    yield p
    await p.close()


@pytest.fixture
async def case_repo(pool):
    from app.repositories import CaseRepository
    pub = RecordingPublisher()
    return CaseRepository(pool, pub), pub
