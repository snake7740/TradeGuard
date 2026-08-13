# -*- coding: utf-8 -*-
"""知识库向量化与检索（US-E6-04 向量化流水线，DA-T-10 pgvector HNSW）

确定性哈希 embedding（字符三元组 → 1024 维，L2 归一化）：无外部依赖、
可复现、跨进程一致，满足演示与测试的稳定性要求；生产替换 UnifiedModel
embedding 仅需换 hash_embedding 实现（端口/适配器，02 §6）。

发布门控（DA-INV-06 双守护）：应用层强制 human:* 操作者 + 事务内
set_config('tg.actor') 供 DB 触发器校验；检索仅见 published（SC-05）。
"""
from __future__ import annotations

import hashlib
import math
import uuid

EMBED_DIM = 1024
CHUNK_SIZE = 200          # 切块长度（字符）
SIMILARITY_MIN = 0.22     # 检索命中阈值：哈希 embedding 下同主题实测 ≈0.29、异主题 ≤0.09，
                          # 取 0.22 分隔（低于即视为未命中，AA-SK-02 引用纪律）


def hash_embedding(text: str) -> list[float]:
    """字符一/二/三元组哈希累积 → L2 归一化向量（确定性，无外部依赖；
    多粒度特征保证短查询与长文档的关键词重叠可被捕捉）"""
    vec = [0.0] * EMBED_DIM
    t = text.lower().replace(" ", "")
    grams: list[str] = list(t)
    grams += [t[i:i + 2] for i in range(len(t) - 1)]
    grams += [t[i:i + 3] for i in range(max(len(t) - 2, 0)) or [0]]
    for g in grams:
        if not g:
            continue
        idx = int(hashlib.md5(g.encode("utf-8")).hexdigest(), 16) % EMBED_DIM
        vec[idx] += 1.0
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [round(x / norm, 6) for x in vec]


def chunk_text(text: str, size: int = CHUNK_SIZE) -> list[str]:
    """按固定长度切块（空块丢弃），保证短文档至少 1 块"""
    parts = [text[i:i + size] for i in range(0, max(len(text), 1), size)]
    return [p for p in parts if p.strip()] or [text]


def _vec_literal(vec: list[float]) -> str:
    return "[" + ",".join(repr(x) for x in vec) + "]"


async def _vectorize(pool, doc_id: str, content: str) -> int:
    """切块写入 kb_embedding（ON CONFLICT DO NOTHING：tg_web 仅 INSERT 权限，
    02-roles.sql；发布仅发生一次由 pending 守卫保证，无需清旧块）"""
    chunks = chunk_text(content)
    async with pool.acquire() as conn, conn.transaction():
        for i, chunk in enumerate(chunks):
            await conn.execute(
                """INSERT INTO kb_embedding (doc_id, chunk_id, embedding, text)
                   VALUES ($1, $2, $3::vector, $4)
                   ON CONFLICT (doc_id, chunk_id) DO NOTHING""",
                doc_id, i, _vec_literal(hash_embedding(chunk)), chunk)
    return len(chunks)


async def publish_and_index(pool, doc_id: str, operator: str, comment: str = "") -> dict:
    """人工发布 + 向量化入库（API-W-12 编排，DA-INV-06 + US-E6-04）

    operator 必须 human:* （Schema 与 DB 触发器双层校验）；发布与审计同事务，
    向量化在事务后执行（失败不回滚发布，申请单保留可重试，AA-SK-05 失败处理）。
    """
    if not operator.startswith("human:"):
        raise PermissionError("E-KB-HUMAN-GATE: 发布仅限人类操作者（DA-INV-06）")
    doc = await pool.fetchrow("SELECT status, content FROM kb_document WHERE doc_id=$1", doc_id)
    if not doc:
        raise LookupError(doc_id)
    if doc["status"] != "pending":
        raise ValueError(f"E-KB-NOT-PENDING: 文档已决（{doc['status']}）")
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('tg.actor', $1, true)", operator)
        await conn.execute(
            "UPDATE kb_document SET status='published', reviewer=$1, reviewed_at=now() "
            "WHERE doc_id=$2", operator, doc_id)
        await conn.execute(
            """INSERT INTO audit_log (log_id, actor, action, target, basis)
               VALUES ($1, $2, 'kb.publish', $3, $4)""",
            uuid.uuid4().hex, operator, doc_id, comment or "->published（人工门控，BA-BR-11）")
    chunks = await _vectorize(pool, doc_id, doc["content"])
    return {"doc_id": doc_id, "status": "published", "reviewer": operator, "chunks": chunks}


async def index_document(pool, doc_id: str, operator: str, comment: str = "") -> dict:
    """publish_and_index 的别名入口（技能层语义：发布即向量化）"""
    return await publish_and_index(pool, doc_id, operator, comment)


async def search_kb(pool, query: str, top_k: int = 5) -> list[dict]:
    """DA-KB-01 检索：仅 published 可见（DA-INV-06），余弦相似度降序，
    低于 SIMILARITY_MIN 视为未命中（AA-SK-02 未命中须显式声明，不得虚构引用）"""
    rows = await pool.fetch(
        """SELECT e.doc_id, d.title,
                  MAX(1 - (e.embedding <=> $1::vector)) AS similarity
           FROM kb_embedding e
           JOIN kb_document d ON d.doc_id = e.doc_id
           WHERE d.status = 'published'
           GROUP BY e.doc_id, d.title
           ORDER BY similarity DESC LIMIT $2""",
        _vec_literal(hash_embedding(query)), top_k)
    return [{"doc_id": r["doc_id"], "title": r["title"], "similarity": float(r["similarity"])}
            for r in rows if r["similarity"] >= SIMILARITY_MIN]
