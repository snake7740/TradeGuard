"""共享夹具：宿主侧直连运行中的栈（postgres localhost:5432，tg_web 人类写路径账号）。

路径注入使 `app` 包可导入；事件发布用记录替身（端口/适配器模式的可测试性收益）。
"""
import os
import sys

import asyncpg
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

PG_DSN = os.getenv("TG_TEST_DSN", "postgresql://tg_web:tg_web_dev@localhost:5433/tradeguard")
TG_APP_DSN = os.getenv("TG_TEST_APP_DSN", "postgresql://tg_app:tg_app_dev@localhost:5433/tradeguard")
TG_SUPER_DSN = os.getenv("TG_TEST_SUPER_DSN", "postgresql://postgres:tradeguard_dev@localhost:5433/tradeguard")
# MCP 客户端（httpx）会自动读取系统代理配置，连回环地址也被转发到代理导致 502；
# 故在导入期注入 NO_PROXY 旁路（setdefault 不覆盖用户显式配置）。尾斜杠 /mcp/ 为
# streamable-http 挂载点（无斜杠 307 重定向）。
os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1")
os.environ.setdefault("no_proxy", "localhost,127.0.0.1")
MCP_CORE_URL = os.getenv("TG_TEST_MCP_CORE", "http://127.0.0.1:8101/mcp/")
MCP_EXTERNAL_URL = os.getenv("TG_TEST_MCP_EXTERNAL", "http://127.0.0.1:8102/mcp/")


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


@pytest.fixture(autouse=True, scope="session")
def _clean_kb_tables():
    """会话级知识库清场：kb_document/kb_embedding 无 DELETE 授权（只增语义，
    02-roles.sql），跨轮污染会扰乱相似度检索断言，故经超级用户 TRUNCATE 保底。"""
    import asyncio

    async def _truncate():
        conn = await asyncpg.connect(TG_SUPER_DSN)
        try:
            await conn.execute("TRUNCATE kb_embedding, kb_document")
        finally:
            await conn.close()

    asyncio.run(_truncate())


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


@pytest.fixture
async def app_pool():
    """tg_app 连接：播种交易流水（数据发生器写角色，02-roles.sql 权限矩阵）"""
    p = await asyncpg.create_pool(TG_APP_DSN, min_size=1, max_size=2)
    yield p
    await p.close()


class FakeExternal:
    """AA-MCP-02 测试替身：返回与 mcp-external-mock 同构的原始载荷（防腐层输入）。

    默认档：征信 low 段（无信号）/ 舆情无命中 / 投诉 1 条否认交易。
    置 fail=True 模拟外部源不可用（降级路径 AA-SK-01 失败处理）。
    """

    def __init__(self, credit_band="low", sentiment_hits=None, complaint_items=1, fail=False):
        self.credit_band = credit_band
        self.sentiment_hits = sentiment_hits or []
        self.complaint_items = complaint_items
        self.fail = fail

    def _guard(self):
        if self.fail:
            raise ConnectionError("external source unavailable")

    async def query_credit_report(self, subject_id, query_reason):
        self._guard()
        score = {"high": 450, "mid": 600, "low": 750}[self.credit_band]
        return {"source": "credit-mock", "subject_id": subject_id, "credit_score": score,
                "risk_band": self.credit_band, "overdue_count_12m": 0,
                "query_reason": query_reason, "degraded": False}

    async def query_sentiment(self, subject_id, query_reason):
        self._guard()
        return {"source": "sentiment-mock", "subject_id": subject_id,
                "hits": self.sentiment_hits, "query_reason": query_reason, "degraded": False}

    async def query_complaints(self, subject_id, query_reason):
        self._guard()
        return {"source": "complaint-mock", "subject_id": subject_id,
                "items": [{"type": "deny_transaction", "content": "持卡人否认该笔交易",
                           "channel": "phone"} for _ in range(self.complaint_items)],
                "query_reason": query_reason, "degraded": False}


@pytest.fixture
async def aggregation(case_repo, pool):
    """AggregationService 装配：FakeExternal（确定性）+ 真实 CoreClient（mcp-core 实链路写 DA-T-04）"""
    from app.skills.aggregation import AggregationService
    from app.skills.mcp_adapters import CoreClient
    repo, pub = case_repo
    return AggregationService(pool=pool, cases=repo, external=FakeExternal(),
                              core=CoreClient(MCP_CORE_URL), retry=0, timeout=5.0), repo, pub


@pytest.fixture
async def disposition(case_repo, pool):
    """DispositionService 装配（AA-SK-03 确定性内核，真实 CoreClient 实链路）"""
    from app.skills.disposition import DispositionService
    from app.skills.mcp_adapters import CoreClient
    repo, pub = case_repo
    return DispositionService(pool=pool, cases=repo, core=CoreClient(MCP_CORE_URL),
                              pub=pub), repo, pub


@pytest.fixture
async def investigation(case_repo, pool):
    """InvestigationService 装配（AA-SK-02，真实 CoreClient：图谱/证据/加分实链路）"""
    from app.skills.investigation import InvestigationService
    from app.skills.mcp_adapters import CoreClient
    repo, pub = case_repo
    return InvestigationService(pool=pool, cases=repo, core=CoreClient(MCP_CORE_URL),
                                pub=pub), repo, pub


@pytest.fixture
async def verification(case_repo, pool):
    """VerificationService 装配（AA-SK-04/05，核验→归档→复盘入库申请闭环）"""
    from app.skills.verification import VerificationService
    from app.skills.mcp_adapters import CoreClient
    repo, pub = case_repo
    return VerificationService(pool=pool, cases=repo, core=CoreClient(MCP_CORE_URL),
                               pub=pub), repo, pub
