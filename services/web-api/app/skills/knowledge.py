# -*- coding: utf-8 -*-
"""知识库向量化与检索（US-E6-04 向量化流水线，DA-T-10 pgvector HNSW）

确定性哈希 embedding（字符三元组 → 1024 维，L2 归一化）：无外部依赖、
可复现、跨进程一致，满足演示与测试的稳定性要求；生产替换 UnifiedModel
embedding 仅需换 hash_embedding 实现（端口/适配器，02 §6）。

发布门控（DA-INV-06 双守护）：应用层强制 human:* 操作者 + 事务内
set_config('tg.actor') 供 DB 触发器校验；检索仅见 published（SC-05）。

知识代谢（E1，BA-BR-20，docs/14 US-E11）：cite_count/hit_correct 累积 →
effectiveness_score 统计；长期零引用自动降级 pending（降级自动，升级与
发布人工），降级留痕并发 E-KB-DECAY 事件。
"""
from __future__ import annotations

import hashlib
import math
import uuid
from typing import Any

EMBED_DIM = 1024
CHUNK_SIZE = 200          # 切块长度（字符）
SIMILARITY_MIN = 0.22     # 检索命中阈值：哈希 embedding 下同主题实测 ≈0.29、异主题 ≤0.09，
                          # 取 0.22 分隔（低于即视为未命中，AA-SK-02 引用纪律）
KB_DECAY_DAYS = 30        # E1 代谢窗口：published 条目 N 天零引用自动转 pending（BA-BR-20）
KB_DECAY_ACTOR = "system:kb-metabolism"


def effectiveness(cite_count: int, hit_correct: int) -> float:
    """E1 有效性分（单测目标）：命中且反哺定性成功次数 / 引用次数，
    零引用记 0.0（未经验证不得给高分，BA-BR-20 降级依据同源）"""
    if not cite_count:
        return 0.0
    return round(min(1.0, max(0.0, hit_correct / cite_count)), 2)


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
        idx = int(hashlib.md5(g.encode("utf-8"),
                             usedforsecurity=False).hexdigest(), 16) % EMBED_DIM  # 特征索引非安全哈希
        vec[idx] += 1.0
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [round(x / norm, 6) for x in vec]


def chunk_text(text: str, size: int = CHUNK_SIZE) -> list[str]:
    """按固定长度切块（空块丢弃），保证短文档至少 1 块"""
    parts = [text[i:i + size] for i in range(0, max(len(text), 1), size)]
    return [p for p in parts if p.strip()] or [text]


def _vec_literal(vec: list[float]) -> str:
    return "[" + ",".join(repr(x) for x in vec) + "]"


async def _vectorize(pool, doc_id: str, content: str, embedder=None) -> int:
    """切块写入 kb_embedding（ON CONFLICT DO NOTHING：tg_web 仅 INSERT 权限，
    02-roles.sql；发布仅发生一次由 pending 守卫保证，无需清旧块）。
    embedder：EmbeddingProvider 端口（阶段1 接线，R-40；缺省哈希 baseline）"""
    if embedder is None:
        from app.core.llm_adapters import HashEmbeddingProvider
        embedder = HashEmbeddingProvider()
    chunks = chunk_text(content)
    async with pool.acquire() as conn, conn.transaction():
        for i, chunk in enumerate(chunks):
            await conn.execute(
                """INSERT INTO kb_embedding (doc_id, chunk_id, embedding, text)
                   VALUES ($1, $2, $3::vector, $4)
                   ON CONFLICT (doc_id, chunk_id) DO NOTHING""",
                doc_id, i, _vec_literal(await embedder.embed(chunk)), chunk)
    return len(chunks)


async def publish_and_index(pool, doc_id: str, operator: str, comment: str = "", embedder=None) -> dict:
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
        zh = {"published": "已发布", "rejected": "已驳回"}.get(doc["status"], doc["status"])
        raise ValueError(f"该条目已完成审核（{zh}），请勿重复操作")
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('tg.actor', $1, true)", operator)
        await conn.execute(
            "UPDATE kb_document SET status='published', reviewer=$1, reviewed_at=now() "
            "WHERE doc_id=$2", operator, doc_id)
        await conn.execute(
            """INSERT INTO audit_log (log_id, actor, action, target, basis)
               VALUES ($1, $2, 'kb.publish', $3, $4)""",
            # R-37 复审收口：comment 截断对齐 audit_log.basis varchar(300)
            uuid.uuid4().hex, operator, doc_id,
            (comment or "->published（人工门控，BA-BR-11）")[:300])
    chunks = await _vectorize(pool, doc_id, doc["content"], embedder)
    return {"doc_id": doc_id, "status": "published", "reviewer": operator, "chunks": chunks}


async def index_document(pool, doc_id: str, operator: str, comment: str = "", embedder=None) -> dict:
    """publish_and_index 的别名入口（技能层语义：发布即向量化）"""
    return await publish_and_index(pool, doc_id, operator, comment, embedder)


async def search_kb(pool, query: str, top_k: int = 5, embedder=None) -> list[dict]:
    """DA-KB-01 检索：仅 published 可见（DA-INV-06），余弦相似度降序，
    低于 SIMILARITY_MIN 视为未命中（AA-SK-02 未命中须显式声明，不得虚构引用）"""
    if embedder is None:
        from app.core.llm_adapters import HashEmbeddingProvider
        embedder = HashEmbeddingProvider()
    rows = await pool.fetch(
        """SELECT e.doc_id, d.title,
                  MAX(1 - (e.embedding <=> $1::vector)) AS similarity
           FROM kb_embedding e
           JOIN kb_document d ON d.doc_id = e.doc_id
           WHERE d.status = 'published'
           GROUP BY e.doc_id, d.title
           ORDER BY similarity DESC LIMIT $2""",
        _vec_literal(await embedder.embed(query)), top_k)
    hits = [{"doc_id": r["doc_id"], "title": r["title"], "similarity": float(r["similarity"])}
            for r in rows if r["similarity"] >= SIMILARITY_MIN]
    if hits:  # E1 引用计数累积（代谢统计输入，best-effort 不阻断检索）
        try:
            await pool.execute(
                "UPDATE kb_document SET cite_count = cite_count + 1"
                " WHERE doc_id = ANY($1::varchar[])",
                [h["doc_id"] for h in hits])
        except Exception:  # noqa: BLE001 —— 计数失败不影响检索结果
            pass
    return hits


async def mark_kb_feedback(pool, doc_ids: list[str]) -> None:
    """E1 命中正确性反哺：KB 引用成功升级定性（R-48 反哺路径）时记 hit_correct，
    best-effort 不阻断调查主链路（US-E11）"""
    if not doc_ids:
        return
    try:
        await pool.execute(
            "UPDATE kb_document SET hit_correct = hit_correct + 1"
            " WHERE doc_id = ANY($1::varchar[])", list(dict.fromkeys(doc_ids)))
    except Exception:  # noqa: BLE001 —— 统计失败不影响调查结论
        pass


async def kb_metabolism(pool, pub, decay_days: int = KB_DECAY_DAYS) -> dict[str, Any]:
    """E1 知识代谢任务（BA-BR-20，SC-17 载体，US-E11）：
      1) effectiveness_score 全量重算（published 条目，cite/hit 累积输入）；
      2) 自动降级：超窗零引用 published → pending（降级自动；升级/发布仍人工，
         DA-INV-06 方向不变：本函数从不置 published）+ 审计 + E-KB-DECAY 事件。
    """
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            """UPDATE kb_document SET effectiveness_score = CASE
                   WHEN cite_count > 0
                   THEN ROUND(LEAST(1.0, hit_correct::numeric / cite_count), 2)
                   ELSE 0 END
               WHERE status = 'published'""")
        decayed = await conn.fetch(
            """UPDATE kb_document SET status='pending'
               WHERE status='published' AND cite_count = 0
                 AND COALESCE(reviewed_at, ts)
                     < now() - make_interval(days=>$1)
               RETURNING doc_id, title""",
            decay_days)
        for r in decayed:
            await conn.execute(
                """INSERT INTO audit_log (log_id, actor, action, target, basis)
                   VALUES ($1, $2, 'kb.decay', $3, $4)""",
                uuid.uuid4().hex, KB_DECAY_ACTOR, r["doc_id"],
                f"{decay_days} 天零引用自动降级 pending（BA-BR-20，重新发布须人工）"[:300])
    for r in decayed:  # 事件在事务外发（SSE/MQ 不因投递失败回滚降级）
        await pub.publish(
            r["doc_id"], "E-KB-DECAY",
            {"doc_id": r["doc_id"], "title": r["title"],
             "reason": f"zero_citation_{decay_days}d",
             "new_status": "pending"},
            KB_DECAY_ACTOR)
    return {"decayed": len(decayed),
            "doc_ids": [r["doc_id"] for r in decayed]}
