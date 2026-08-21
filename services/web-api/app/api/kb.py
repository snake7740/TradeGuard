"""知识库审批路由（API-W-11~13，SC-05，DA-INV-06 发布仅人工）+ B 端问答（API-W-27，BA-BR-23）"""
import uuid
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, Request

from ..schemas import KbAskIn, KbPublishIn
from ..skills.knowledge import ask_kb, publish_and_index
from .common import operator_from_header

router = APIRouter(prefix="/api/kb", tags=["knowledge-base"])

# AA-AG-06 知识助手的 B 端服务面（SC-22）：问答仅对已识别人类角色开放，
# 问答记录可追责到人（BA-BR-23）；agent:/未识别调用方一律 403
ASK_ALLOWED_ROLES = {"风控值班员", "风控审批官", "合规审计员", "风控策略管理员"}


def _operator(request: Request, body: KbPublishIn) -> str:
    """操作者优先级：body 显式传值 > X-Operator 头（门户自动携带当前角色）>
    human:kb_admin（直调/旧客户端回落）——审计留真人而非固定占位符"""
    return body.operator or operator_from_header(
        request.headers.get("X-Operator"), "human:kb_admin")


@router.get("/applications")
async def list_applications(request: Request, status: str = "pending"):
    """API-W-11：入库申请列表（AA-SK-05 产出，status=pending）"""
    return {"items": await request.app.state.kb.applications(status)}


@router.post("/ask")
async def ask(request: Request, body: KbAskIn):
    """API-W-27：B 端知识问答（US-E14-02，SC-22，AA-AG-06 知识助手服务面）
    仅引用已发布知识（DA-KB-01 检索），未命中显式声明无先例（BA-BR-23）；
    端点级人工角色门：agent:/未识别调用方 403（问答可追责到人）"""
    try:
        actor = unquote(request.headers.get("X-Operator", ""))
    except Exception:  # noqa: BLE001 —— 解码异常按未识别处理
        actor = ""
    role = actor[len("human:"):] if actor.startswith("human:") else actor
    if role not in ASK_ALLOWED_ROLES:
        raise HTTPException(403, detail={"code": "E-FORBIDDEN-ROLE",
                                         "message": "知识问答仅对人工角色开放（BA-BR-23，问答可追责到人）"})
    return await ask_kb(request.app.state.pool, body.question, f"human:{role}")


@router.post("/applications/{doc_id}/publish")
async def publish_document(request: Request, doc_id: str, body: KbPublishIn):
    """API-W-12：确认发布（DA-INV-06 双守护 + US-E6-04 向量化入库，SC-05）
    发布与审计同事务（tg.actor 声明供 DB 触发器校验），事务后向量化 kb_embedding。"""
    try:
        return await publish_and_index(
            request.app.state.pool, doc_id, _operator(request, body), body.comment)
    except PermissionError:
        raise HTTPException(403, detail={"code": "E-KB-HUMAN-GATE",
                                         "message": "知识发布仅限人工操作，请切换人工角色后重试"})
    except LookupError:
        raise HTTPException(404, detail={"code": "E-NOT-FOUND", "message": "未找到该知识条目，请刷新列表"})
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
        raise HTTPException(404, detail={"code": "E-NOT-FOUND", "message": "未找到该知识条目，请刷新列表"})
    if doc["status"] != "pending":
        zh = {"published": "已发布", "rejected": "已驳回"}.get(doc["status"], doc["status"])
        raise HTTPException(409, detail={"code": "E-ALREADY-DECIDED",
                                         "message": f"该条目已完成审核（{zh}），请勿重复操作"})
    async with pool.acquire() as conn, conn.transaction():
        op = _operator(request, body)
        # DA-INV-06 双守护：事务内声明人类操作者，否则 DB 触发器拒发（04-invariants.sql）
        await conn.execute("SELECT set_config('tg.actor', $1, true)", op)
        await conn.execute(
            "UPDATE kb_document SET status=$1, reviewer=$2, reviewed_at=now() WHERE doc_id=$3",
            status, op, doc_id)
        await conn.execute(
            """INSERT INTO audit_log (log_id, actor, action, target, basis)
               VALUES ($1, $2, $3, $4, $5)""",
            # R-37 复审收口：comment 截断对齐 audit_log.basis varchar(300)
            uuid.uuid4().hex, op, action, doc_id,
            (body.comment or f"->{status}")[:300])
    return {"doc_id": doc_id, "status": status, "reviewer": op}
