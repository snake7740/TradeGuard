"""知识库审批路由（API-W-11~13，SC-05，DA-INV-06 发布仅人工）"""
import uuid

from fastapi import APIRouter, HTTPException, Request

from ..schemas import KbPublishIn
from ..skills.knowledge import publish_and_index

router = APIRouter(prefix="/api/kb", tags=["knowledge-base"])


@router.get("/applications")
async def list_applications(request: Request, status: str = "pending"):
    """API-W-11：入库申请列表（AA-SK-05 产出，status=pending）"""
    return {"items": await request.app.state.kb.applications(status)}


@router.post("/applications/{doc_id}/publish")
async def publish_document(request: Request, doc_id: str, body: KbPublishIn):
    """API-W-12：确认发布（DA-INV-06 双守护 + US-E6-04 向量化入库，SC-05）
    发布与审计同事务（tg.actor 声明供 DB 触发器校验），事务后向量化 kb_embedding。"""
    try:
        return await publish_and_index(
            request.app.state.pool, doc_id, body.operator, body.comment)
    except PermissionError:
        raise HTTPException(403, detail={"code": "E-KB-HUMAN-GATE",
                                         "message": "发布仅限人类操作者（DA-INV-06）"})
    except LookupError:
        raise HTTPException(404, detail={"code": "E-NOT-FOUND", "message": "doc not found"})
    except ValueError as e:
        raise HTTPException(409, detail={"code": "E-KB-NOT-PENDING", "message": str(e)})


@router.post("/applications/{doc_id}/reject")
async def reject_document(request: Request, doc_id: str, body: KbPublishIn):
    """API-W-13：驳回申请"""
    return await _decide(request, doc_id, body, "rejected", "kb.reject")


async def _decide(request: Request, doc_id: str, body: KbPublishIn, status: str, action: str):
    """驳回通道（发布已改由 publish_and_index 编排，含向量化）"""
    pool = request.app.state.pool
    doc = await pool.fetchrow("SELECT status FROM kb_document WHERE doc_id=$1", doc_id)
    if not doc:
        raise HTTPException(404, detail={"code": "E-NOT-FOUND", "message": "doc not found"})
    if doc["status"] != "pending":
        raise HTTPException(409, detail={"code": "E-ALREADY-DECIDED",
                                         "message": f"文档已决（{doc['status']}）"})
    async with pool.acquire() as conn, conn.transaction():
        # DA-INV-06 双守护：事务内声明人类操作者，否则 DB 触发器拒发（04-invariants.sql）
        await conn.execute("SELECT set_config('tg.actor', $1, true)", body.operator)
        await conn.execute(
            "UPDATE kb_document SET status=$1, reviewer=$2, reviewed_at=now() WHERE doc_id=$3",
            status, body.operator, doc_id)
        await conn.execute(
            """INSERT INTO audit_log (log_id, actor, action, target, basis)
               VALUES ($1, $2, $3, $4, $5)""",
            uuid.uuid4().hex, body.operator, action, doc_id, body.comment or f"->{status}")
    return {"doc_id": doc_id, "status": status, "reviewer": body.operator}
