# -*- coding: utf-8 -*-
"""多角色业务流程自动化测试（01 §6 用户旅程 × 04 §10 四角色 × BA-BP 全链回放）

以 web-portal/src/role.js 的正式 4 角色接力驱动真实 HTTP API（TestClient 走
真实 lifespan：PG 5433 + 真 MCP 8101/8102），验证跨角色业务流程闭环：

  流程 A 欺诈确认全链（SC-02/SC-08）：
    风控值班员 告警受理 → 风控审批官 复核确认+审批批准（冻结执行）→
    合规审计员 取处置凭证+核验归档（AA-SK-04/05）+ 审计链全回放 →
    风控策略管理员 阈值热更（BA-BR-05/SC-06）
  流程 B 误报排除链（SC-10）：值班员受理 → 审批官复核排除归档 → 审计员回放
  流程 C 人机边界（02 §3.3/§7）：Agent 越权触发人工环节 → 409 E-HUMAN-ONLY；
    已归档案件重复核验 → 409
  流程 D 调查→处置审批交接链（API-W-23，SC-02）：调查完成 PENDING_APPROVAL 后
    经处置提交建单（补齐 UI 全链 D2 断链）→ 审批批准冻结 → 核验归档；含一案一单防重
  流程 E 角色边界强制（A0：03 §6 权限矩阵 API 层落地）：值班员调审批/发布/配置
    端点 → 403 E-FORBIDDEN-ROLE；未识别调用方放行 + api.unknown_actor 留痕

角色名与 role.js ROLES 逐字对齐（test_routes 的“复核员/审批主管/配置管理员”
为历史测试名，不在正式 4 角色白名单内，按未识别角色放行处理，本文件起统一正式角色名）；
X-Operator 均为前端契约的
encodeURIComponent 编码中文（axios 拦截器统一注入，common.py 解码）。
"""
import asyncio
import hashlib
import os
import urllib.parse
import uuid
from datetime import datetime, timezone

import asyncpg
import pytest
from fastapi.testclient import TestClient

from conftest import MCP_CORE_URL, PG_DSN

# 正式 4 角色（web-portal/src/role.js ROLES 逐字一致）
OP_ONCALL = urllib.parse.quote("风控值班员")     # 告警受理/工作台
OP_APPROVER = urllib.parse.quote("风控审批官")   # 人工复核/审批决策
OP_AUDITOR = urllib.parse.quote("合规审计员")    # 处置凭证/核验/审计回放
OP_CONFIG = urllib.parse.quote("风控策略管理员")  # 阈值热更


@pytest.fixture(scope="module")
def client():
    os.environ["PG_DSN"] = PG_DSN                # lifespan 连测试库（tg_web@5433）
    os.environ.pop("TG_API_TOKEN", None)         # 开发直通（鉴权矩阵由 test_auth 承载）
    from app.main import create_app
    with TestClient(create_app()) as c:
        yield c


def _subject(tag: str) -> str:
    return hashlib.sha256(f"{tag}-{uuid.uuid4().hex}".encode()).hexdigest()[:64]


def _fetch(query: str, *args) -> list:
    async def go():
        conn = await asyncpg.connect(PG_DSN)
        try:
            return await conn.fetch(query, *args)
        finally:
            await conn.close()
    return asyncio.run(go())


def _execute(query: str, *args) -> None:
    async def go():
        conn = await asyncpg.connect(PG_DSN)
        try:
            await conn.execute(query, *args)
        finally:
            await conn.close()
    asyncio.run(go())


def _alert(client, operator: str, severity: str = "high") -> str:
    """【风控值班员】告警受理（API-W-01）：202 即受理成功，返回 case_id"""
    r = client.post("/api/alerts", json={"subject_ref": _subject("mrf"), "severity": severity},
                    headers={"X-Operator": operator})
    assert r.status_code == 202, r.text
    return r.json()["case_id"]


def _investigating_case(severity: str = "high") -> str:
    """测试装配：单事务直插 INVESTIGATING（对 compose EventWorker 零窗口——
    worker 2s 轮询 REGISTERED 会抢跑自动链路，直插法对其不可见；详见
    test_routes._reviewable_case 对照实验记录）。score=85 确保触及人工审批线。"""
    score = {"low": 25, "medium": 55, "high": 85}[severity]
    case_id = f"CASE-{datetime.now(timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:6]}"
    _execute(
        """INSERT INTO risk_case (case_id, subject_ref, status, risk_score, trace_id)
           VALUES ($1, $2, 'INVESTIGATING', $3, $4)""",
        case_id, _subject("mrf-ins"), score, uuid.uuid4().hex)
    return case_id


def _audit_actors(client, case_id: str) -> dict[str, str]:
    """【合规审计员】审计链回放（API-W-10）：{action: actor}，重复 action 取首条"""
    items = client.get(f"/api/audit/{case_id}").json()["items"]
    assert items, "审计链不能为空"
    return {it["action"]: it["actor"] for it in items}


# ---------- 流程 A：欺诈确认全链（四角色接力，SC-02/SC-08） ----------

def test_fraud_confirmation_flow_across_roles(client):
    # 步骤 1【风控值班员】告警受理 → 工作台可见（API-W-01/02）
    case_id = _alert(client, OP_ONCALL, severity="high")
    assert client.get(f"/api/cases/{case_id}").json()["status"] == "REGISTERED"
    board = client.get("/api/cases", params={"status": "REGISTERED", "size": 100}).json()
    assert any(c["case_id"] == case_id for c in board["items"]), "值班员工作台应见新立案"
    rows = _fetch("SELECT actor FROM audit_log WHERE target=$1 AND action='case.register'",
                  case_id)
    assert rows and rows[0]["actor"] == "human:风控值班员"

    # 步骤 2（测试装配）直插 INVESTIGATING——案件进入调查环节
    case_id = _investigating_case("high")

    # 步骤 3【风控审批官】复核确认欺诈（API-W-07）→ 建审批单（US-E5-04）
    r = client.post(f"/api/cases/{case_id}/review",
                    json={"conclusion": "block", "opinion": "图谱关联黑名单，确认欺诈提请冻结"},
                    headers={"X-Operator": OP_APPROVER})
    assert r.status_code == 200, r.text
    approval_id = r.json()["approval_id"]
    assert r.json()["status"] == "PENDING_APPROVAL"
    actors = _audit_actors(client, case_id)
    assert actors["case.transition.ReviewConfirmed"] == "human:风控审批官"

    # 步骤 4【风控审批官】审批队列见单（API-W-08）→ 冻结前置证据链（DA-INV-04）
    # → 批准（API-W-09）→ AA-SK-03 自动执行冻结至 DISPOSED
    queue = client.get("/api/approvals", params={"decision": "pending"}).json()["items"]
    assert any(a["approval_id"] == approval_id for a in queue), "审批队列应见待批工单"

    async def _evidence():
        from app.skills.mcp_adapters import CoreClient
        return await CoreClient(MCP_CORE_URL).record_case_evidence(
            case_id, [{"claim": "持卡人否认交易且商户高风险", "source_ref": "AA-AG-03:mrf",
                       "confidence": 0.9}])
    asyncio.run(_evidence())

    r = client.post(f"/api/approvals/{approval_id}/decide",
                    json={"decision": "approve", "opinion": "证据链完整，同意冻结账户"},
                    headers={"X-Operator": OP_APPROVER})
    assert r.status_code == 200, r.text
    assert r.json()["decision"] == "approved" and r.json()["route"] == "executed"
    assert client.get(f"/api/cases/{case_id}").json()["status"] == "DISPOSED"
    rec = _fetch("SELECT approver, decision FROM approval_record WHERE approval_id=$1",
                 approval_id)
    assert rec[0]["approver"] == "human:风控审批官" and rec[0]["decision"] == "approved"

    # 步骤 5【合规审计员】取处置凭证（API-W-22）→ 核验（API-W-19）→
    # 一致 → VERIFIED → ARCHIVED + 复盘入库申请（AA-SK-04/05，DA-INV-06 pending）
    dispositions = client.get(f"/api/cases/{case_id}/dispositions",
                              headers={"X-Operator": OP_AUDITOR}).json()["items"]
    assert dispositions and dispositions[-1]["status"] == "executed"
    exec_id = dispositions[-1]["exec_id"]

    r = client.post(f"/api/cases/{case_id}/verify", json={"exec_id": exec_id},
                    headers={"X-Operator": OP_AUDITOR})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["consistency_check"] is True
    assert body["case_status"] == "ARCHIVED"
    assert body["kb_application"], "核验一致后必须生成复盘入库申请（仅 pending，发布须人工）"
    kb = _fetch("SELECT status FROM kb_document WHERE doc_id=$1", body["kb_application"])
    assert kb and kb[0]["status"] == "pending"   # DA-INV-06：知识发布仅限人类
    guard = _fetch("""SELECT 1 FROM audit_log
                      WHERE action='api.request' AND actor='human:合规审计员'
                        AND basis LIKE '%/verify%'""")
    assert guard, "网关审计须留痕审计员的核验动作（BA-BR-09）"

    # 步骤 6【合规审计员】审计链全回放（API-W-10）：关键环节 actor 逐角色断言 + 时序。
    # 直插案件无 case.register 行（白名单触发器不允许 REGISTERED 直推 INVESTIGATING，
    # 受理契约与值班员 actor 已在步骤 1 独立覆盖），时序链自人工复核起验
    items = client.get(f"/api/audit/{case_id}").json()["items"]
    seq = [it["action"] for it in items]
    actors = _audit_actors(client, case_id)
    assert actors["case.transition.ReviewConfirmed"] == "human:风控审批官"
    assert actors["verification.run"] == "AA-AG-05"  # 审计 actor 无前缀（SC-01 约定）
    assert actors["case.transition.CaseArchived"] == "agent:AA-AG-05"  # 状态机 actor（02 §3）
    for earlier, later in (
            ("case.transition.ReviewConfirmed", "case.transition.ApprovalApproved"),
            ("case.transition.ApprovalApproved", "verification.run"),
            ("verification.run", "case.transition.CaseArchived")):
        assert seq.index(earlier) < seq.index(later), f"审计链时序错乱：{earlier} 应先于 {later}"

    # 步骤 7【风控策略管理员】阈值热更（API-W-16）：写回当前值（幂等不动配置），
    # 验证读快照→写库→reload 闭环与操作留痕（SC-06，BA-BR-05）
    snap = client.get("/api/config/thresholds").json()
    key = "br-01-mid-review-score"
    cur = snap["values"].get(key)
    r = client.put("/api/config/thresholds", json={key: cur},
                   headers={"X-Operator": OP_CONFIG})
    assert r.status_code == 200 and r.json()["values"][key] == cur
    rows = _fetch("""SELECT actor FROM audit_log
                     WHERE action='config.thresholds.put' ORDER BY ts DESC LIMIT 1""")
    assert rows and rows[0]["actor"] == "human:风控策略管理员"


# ---------- 流程 B：误报排除链（值班员受理 → 审批官排除 → 审计员回放，SC-10） ----------

def test_false_positive_release_flow(client):
    # 【风控值班员】受理 medium 告警（受理契约已由流程 A 覆盖，此处聚焦排除链）
    case_id = _investigating_case("medium")
    # 【风控审批官】复核排除欺诈（API-W-07 release）→ 直接归档（BA-BP-04 出口）
    r = client.post(f"/api/cases/{case_id}/review",
                    json={"conclusion": "release", "opinion": "持卡人已确认本人交易，误报结案"},
                    headers={"X-Operator": OP_APPROVER})
    assert r.status_code == 200 and r.json()["status"] == "ARCHIVED"
    # 【合规审计员】回放：排除决策须为人工角色且留痕完整
    actors = _audit_actors(client, case_id)
    assert actors["case.transition.ReviewDismissed"] == "human:风控审批官"


# ---------- 流程 D：调查→处置审批交接链（API-W-23，补 D2 断链，SC-02） ----------

def test_investigation_to_approval_handoff_flow(client):
    """调查完成后的人工处置提交：修复前 investigation.run 转PENDING_APPROVAL 后
    无 HTTP 入口建单（UI 全链断在「调查→审批」交接），本流程验证 API-W-23 补齐。"""
    # 装配：直插 INVESTIGATING 高风险（85 分，必走审批线）
    case_id = _investigating_case("high")

    # 步骤 1【Agent】触发调查（API-W-18）→ InvestigationCompleted → PENDING_APPROVAL
    r = client.post(f"/api/cases/{case_id}/investigate")
    assert r.status_code == 200, r.text
    assert r.json()["case_status"] == "PENDING_APPROVAL"
    # 断链复现点：调查完成且无复核确认时，不应存在任何待决工单
    assert not _fetch(
        "SELECT 1 FROM approval_record WHERE case_id=$1 AND decision='pending'", case_id)

    # 步骤 2【风控值班员】提请处置（API-W-23 freeze）→ E-DISP-AUTH 门控建单
    r = client.post(f"/api/cases/{case_id}/disposition", json={"action": "freeze"},
                    headers={"X-Operator": OP_ONCALL})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["route"] == "approval_required" and body["approval_id"]
    assert not body.get("duplicate")
    approval_id = body["approval_id"]

    # 一案一单：换动作重复提交 → 幂等返回既有工单，不建第二张
    r = client.post(f"/api/cases/{case_id}/disposition", json={"action": "block"},
                    headers={"X-Operator": OP_ONCALL})
    assert r.status_code == 200 and r.json()["approval_id"] == approval_id
    assert r.json()["duplicate"] is True
    assert _fetch("""SELECT count(*) AS c FROM approval_record
                     WHERE case_id=$1 AND decision='pending'""", case_id)[0]["c"] == 1

    # 步骤 3【风控审批官】队列见单 → 冻结前置证据链（DA-INV-04）→ 批准自动执行
    queue = client.get("/api/approvals").json()["items"]
    assert any(a["approval_id"] == approval_id for a in queue), "审批队列应见 API-W-23 建单"

    async def _evidence():
        from app.skills.mcp_adapters import CoreClient
        return await CoreClient(MCP_CORE_URL).record_case_evidence(
            case_id, [{"claim": "调查定性欺诈，处置提请冻结", "source_ref": "AA-AG-03:mrf-d",
                       "confidence": 0.9}])
    asyncio.run(_evidence())

    r = client.post(f"/api/approvals/{approval_id}/decide",
                    json={"decision": "approve", "opinion": "调查结论明确，批准冻结"},
                    headers={"X-Operator": OP_APPROVER})
    assert r.status_code == 200 and r.json()["route"] == "executed", r.text
    assert client.get(f"/api/cases/{case_id}").json()["status"] == "DISPOSED"

    # 步骤 4【合规审计员】取凭证核验归档（API-W-22/19）
    dispositions = client.get(f"/api/cases/{case_id}/dispositions").json()["items"]
    exec_id = dispositions[-1]["exec_id"]
    r = client.post(f"/api/cases/{case_id}/verify", json={"exec_id": exec_id},
                    headers={"X-Operator": OP_AUDITOR})
    assert r.status_code == 200 and r.json()["case_status"] == "ARCHIVED", r.text

    # 步骤 5【合规审计员】审计链：调查→建单→归档全回放；approval.create 由 AA-AG-04
    # 落库（API-M-11）；approval.decide 审计 target=审批单号不在案件链，另经工单回查
    seq = [it["action"] for it in client.get(f"/api/audit/{case_id}").json()["items"]]
    actors = _audit_actors(client, case_id)
    assert actors["approval.create"] == "AA-AG-04"
    rec = _fetch("SELECT approver, decision FROM approval_record WHERE approval_id=$1",
                 approval_id)
    assert rec[0]["approver"] == "human:风控审批官" and rec[0]["decision"] == "approved"
    for earlier, later in (
            ("investigation.complete", "approval.create"),
            ("approval.create", "case.transition.CaseArchived")):
        assert seq.index(earlier) < seq.index(later), f"审计链时序错乱：{earlier} 应先于 {later}"


# ---------- 流程 C：人机边界与重复核验守卫（02 §3.3/§7，DA-INV-01） ----------

def test_human_only_boundary_and_verify_guard(client):
    case_id = _investigating_case("high")
    # Agent 越权触发人工复核 → 409 E-HUMAN-ONLY（human_only 守卫，02 §7）
    r = client.post(f"/api/cases/{case_id}/review",
                    json={"conclusion": "block", "opinion": "Agent 不得替代人工复核决策"},
                    headers={"X-Operator": "agent:AA-AG-99"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "E-HUMAN-ONLY"
    assert client.get(f"/api/cases/{case_id}").json()["status"] == "INVESTIGATING"

    # 已归档案件重复核验 → 409（核验仅对 DISPOSED 开放，状态机守护 DA-INV-01）
    archived = _investigating_case("medium")
    r = client.post(f"/api/cases/{archived}/review",
                    json={"conclusion": "release", "opinion": "排除欺诈先归档再验重复核验"},
                    headers={"X-Operator": OP_APPROVER})
    assert r.status_code == 200 and r.json()["status"] == "ARCHIVED"
    r = client.post(f"/api/cases/{archived}/verify", json={"exec_id": "exec-nope"})
    assert r.status_code == 409   # 非 DISPOSED 拒绝核验（非法迁移守护）


# ---------- 流程 E：角色边界强制（A0：03 §6 权限矩阵 × API 层 RBAC） ----------

def test_role_boundary_enforcement_at_api_gate(client):
    """角色边界不再是前端菜单的纸面约定：网关按白名单×角色集合强制拦截。
    修复前隐患：任何持 token 者自声明 X-Operator 即可调任意端点（审批/发布/配置）。"""
    case_id = _investigating_case("high")

    # 值班员越权复核 → 403（/cases/*/review 仅风控审批官）
    r = client.post(f"/api/cases/{case_id}/review",
                    json={"conclusion": "block", "opinion": "值班员无权复核确认欺诈"},
                    headers={"X-Operator": OP_ONCALL})
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "E-FORBIDDEN-ROLE"

    # 值班员越权审批/知识发布/阈值配置 → 均 403（端点白名单对齐 tg_web 授权面）
    r = client.post("/api/approvals/AP-NOPE/decide",
                    json={"decision": "approve", "opinion": "值班员无权审批决策"},
                    headers={"X-Operator": OP_ONCALL})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "E-FORBIDDEN-ROLE"
    r = client.post("/api/kb/applications/KB-NOPE/publish",
                    json={"comment": "值班员无权发布知识条目入库"},
                    headers={"X-Operator": OP_ONCALL})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "E-FORBIDDEN-ROLE"
    r = client.put("/api/config/thresholds", json={"br-01-mid-review-score": "50"},
                   headers={"X-Operator": OP_ONCALL})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "E-FORBIDDEN-ROLE"

    # 审计员越权审批 → 403（审计职责只读回放，不得兼裁决）
    r = client.post("/api/approvals/AP-NOPE/decide",
                    json={"decision": "approve", "opinion": "审计员不得兼任审批裁决"},
                    headers={"X-Operator": OP_AUDITOR})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "E-FORBIDDEN-ROLE"

    # 越权拦截必须留痕（BA-BR-09：拒绝也要可追责）
    rows = _fetch("""SELECT count(*) AS c FROM audit_log
                     WHERE action='api.forbidden' AND actor LIKE 'human:风控%'""")
    assert rows[0]["c"] >= 4, "每次越权拦截须落 api.forbidden 审计"

    # 未识别调用方（历史名/无头）：放行 + api.unknown_actor 留痕（兼容 MCP/CI）
    r = client.put("/api/config/thresholds", json={},
                   headers={"X-Operator": urllib.parse.quote("配置管理员")})
    assert r.status_code == 422                                  # 穿透到端点（非 403）
    rows = _fetch("""SELECT count(*) AS c FROM audit_log
                     WHERE action='api.unknown_actor' AND basis LIKE '%/api/config%'""")
    assert rows[0]["c"] >= 1, "未识别角色访问白名单端点须留痕"
